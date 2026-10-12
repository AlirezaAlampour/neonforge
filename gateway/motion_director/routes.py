"""Persistent Motion Director jobs using the existing gateway and supervisor."""
from __future__ import annotations

import asyncio
import json
import hashlib
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import String, Text, DateTime, desc, select
from sqlalchemy.orm import Mapped, mapped_column

from .planner import LocalDirector, ShotRequest, ShotSpec, segment_shot
from . import media
from .renderers import SubmissionUncertain, get_renderer
from .host_client import host_request

tasks: dict[str, asyncio.Task] = {}
planning_lock = asyncio.Lock()


def create_router(runtime):
    router = APIRouter(prefix="/api/v1/motion-director", tags=["motion-director"])

    class MotionJob(runtime.Base):
        __tablename__ = "motion_director_jobs"
        id: Mapped[str] = mapped_column(String(36), primary_key=True)
        shot_json: Mapped[str] = mapped_column(Text)
        status: Mapped[str] = mapped_column(String(32))
        message: Mapped[str] = mapped_column(Text, default="Queued")
        error: Mapped[str | None] = mapped_column(Text, nullable=True)
        prompt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        claim_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
        output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
        history_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
        completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    runtime.MotionJob = MotionJob

    class MotionPlan(runtime.Base):
        __tablename__ = "motion_director_plans"
        id: Mapped[str] = mapped_column(String(36), primary_key=True)
        request_hash: Mapped[str] = mapped_column(String(64), index=True)
        shot_json: Mapped[str] = mapped_column(Text)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    runtime.MotionPlan = MotionPlan

    class MotionSegment(runtime.Base):
        __tablename__ = "motion_director_segments"
        id: Mapped[str] = mapped_column(String(48), primary_key=True)
        job_id: Mapped[str] = mapped_column(String(36), index=True)
        position: Mapped[int] = mapped_column()
        status: Mapped[str] = mapped_column(String(32), default="waiting")
        prompt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
        source_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    runtime.MotionSegment = MotionSegment

    def fingerprint(request):
        return hashlib.sha256(json.dumps(request.model_dump(exclude={"plan_id"}), sort_keys=True).encode()).hexdigest()

    async def planned_shot(request, reuse_cache=True):
        async with planning_lock:
            key = fingerprint(request)
            with runtime.SessionLocal() as db:
                saved = db.execute(select(MotionPlan).where(MotionPlan.request_hash == key).order_by(desc(MotionPlan.created_at))).scalars().first()
            if saved and reuse_cache:
                return ShotSpec.model_validate_json(saved.shot_json)
            shot = LocalDirector().plan(request)
            if request.director == "opus" or (request.director == "auto" and getattr(runtime, "HOST_CONTROL_SOCKET", "")):
                try:
                    data = await host_request(runtime, "claude/plan", {"shot": request.model_dump(), "references": [asset.relative_path for asset in assets_for(request)]})
                    candidate = ShotSpec.model_validate(data)
                    if fingerprint(candidate.request) != key or candidate.reference_ids != shot.reference_ids or (candidate.width, candidate.height, candidate.frames) != (shot.width, shot.height, shot.frames):
                        raise ValueError("Director changed protected request or hardware bounds")
                    shot = candidate
                except (RuntimeError, httpx.HTTPError, ValueError, OSError):
                    shot.notes.append("Opus unavailable or invalid. This shot uses the deterministic cinematic fallback; no retry occurs during rendering.")
            plan_id = str(uuid.uuid4())
            shot.request = request.model_copy(update={"plan_id": plan_id})
            with runtime.SessionLocal() as db:
                db.add(MotionPlan(id=plan_id, request_hash=key, shot_json=shot.model_dump_json(), created_at=runtime._now_utc()))
                db.commit()
            return shot

    def submitted_plan(request):
        if request.plan_id:
            with runtime.SessionLocal() as db:
                saved = db.get(MotionPlan, request.plan_id)
            if not saved or saved.request_hash != fingerprint(request):
                raise HTTPException(409, "The shot changed or its plan is missing. Plan it again before rendering.")
            return ShotSpec.model_validate_json(saved.shot_json)
        if request.director == "opus":
            raise HTTPException(409, "Plan an Opus-directed shot first; rendering never calls the planner.")
        return LocalDirector().plan(request)

    def load(job_id):
        with runtime.SessionLocal() as db:
            row = db.get(MotionJob, job_id)
            if not row:
                raise HTTPException(404, "Motion Director job not found")
            return row

    def serialize(row):
        with runtime.SessionLocal() as db:
            clips = db.execute(select(MotionSegment).where(MotionSegment.job_id == row.id).order_by(MotionSegment.position)).scalars().all()
        return {"job_id": row.id, "service": json.loads(row.shot_json).get("request", {}).get("engine", "h3"), "status": row.status, "message": row.message,
                "segments": [{"index": c.position, "status": c.status, "prompt_id": c.prompt_id} for c in clips],
                "completed_segments": sum(c.status == "completed" for c in clips), "total_segments": len(clips),
                "error": row.error, "created_at": row.created_at.isoformat(), "prompt_id": row.prompt_id,
                "result_path": row.output_path, "shot": json.loads(row.shot_json),
                "preview_url": f"/api/v1/outputs/{row.output_path}" if row.output_path else None,
                "download_url": f"/api/v1/history/{row.history_id}/download" if row.history_id else None}

    async def update(job_id, **changes):
        with runtime.SessionLocal() as db:
            row = db.get(MotionJob, job_id)
            for key, value in changes.items():
                setattr(row, key, value)
            db.commit()
        await runtime.store_job(runtime.JobRecord(job_id=row.id, service="h3", status=row.status,
                                                 created_at=row.created_at.isoformat(), result_path=row.output_path,
                                                 completed_at=row.completed_at.isoformat() if row.completed_at else None,
                                                 message=row.message, error=row.error))
        return row

    def assets_for(request):
        assets = [runtime.get_comfyui_asset_or_404(r, input_id="reference") for r in request.reference_ids]
        if any(asset.kind != "image" for asset in assets):
            raise HTTPException(422, "Every reference must be an image")
        for asset in assets:
            if not runtime.resolve_asset_path(asset.relative_path, runtime.COMFYUI_UPLOADS_DIR).is_file():
                raise HTTPException(404, "Reference image file is missing")
        return assets

    async def recover_memory():
        response = await runtime.http_client.post(f"{runtime.SUPERVISOR_URL.rstrip('/')}/reclaim/h3", json={}, timeout=45)
        if response.status_code != 200:
            raise HTTPException(409, response.json().get("detail", "Memory recovery is unavailable while GPU work is active"))
        return response.json()

    async def capabilities():
        h3 = await get_renderer("h3", runtime).available()
        memory = runtime.get_memory_status()
        available_gb = memory.get("available_gb", 0)
        # Gateway memory helpers expose free_gb on older releases.
        if not available_gb:
            available_gb = runtime.read_meminfo().get("MemAvailable", 0) / 1048576
        scheduler = False
        if h3.get("available"):
            try:
                response = await runtime.http_client.get(f"{runtime.SUPERVISOR_URL.rstrip('/')}/workloads/status", timeout=10)
                response.raise_for_status()
                status = response.json()
                scheduler = ("h3" in status.get("managed_services", {})
                             and (status.get("host") is None or bool(status["host"].get("gpu", {}).get("cap_active"))))
            except (httpx.HTTPError, ValueError, TypeError):
                pass
        h3.update(scheduler_ready=scheduler, available_gb=round(available_gb, 1), minimum_gb=90,
                  render_ready=bool(h3.get("available") and scheduler and available_gb >= 90 and not h3.get("busy")))
        if h3.get("available") and not scheduler:
            h3["reason"] = "H3 safety coordination is unavailable. Wait for the resource scheduler and its private host connection to recover."
        elif h3.get("available") and available_gb < 90:
            h3["reason"] = f"H3 requires 90 GiB available; current {available_gb:.1f} GiB. Existing services will be preserved."
        elif h3.get("busy"):
            h3["reason"] = "H3 has an existing workload. Wait for it to finish."
        planner = {"active": "local-director", "opus_available": False,
                   "reason": "The private host Claude Code adapter is unavailable. Deterministic planning remains available."}
        resources = None
        if getattr(runtime, "HOST_CONTROL_SOCKET", ""):
            try:
                status = await host_request(runtime, "claude/status")
                planner.update(opus_available=status.get("available", False), subscription=status.get("subscription"), reason=status.get("reason"))
                resources = await host_request(runtime, "resources")
                if resources.get("blockers") or resources.get("exclusive_claims") or resources.get("shared_gpu", {}).get("exclusive"):
                    h3["render_ready"] = False
                    h3["reason"] = " ".join(resources.get("blockers", [])) or "A GPU-exclusive generation is already active."
            except (RuntimeError, httpx.HTTPError, OSError):
                h3.update(render_ready=False, reason="Authenticated host GPU safety/coordination is unavailable.")
        h3["can_recover_memory"] = bool(h3.get("available") and scheduler and available_gb < 90 and not h3.get("busy") and resources
                                        and not resources.get("exclusive_claims") and not resources.get("shared_gpu", {}).get("exclusive")
                                        and not [b for b in resources.get("blockers", []) if not b.startswith("H3 needs")])
        return {"h3": h3, "grok": await get_renderer("grok", runtime).available(), "planner": planner, "resources": resources}

    def segment_update(segment_id, **changes):
        with runtime.SessionLocal() as db:
            clip = db.get(MotionSegment, segment_id)
            for key, value in changes.items():
                setattr(clip, key, value)
            db.commit()
            return clip

    async def run_sequence(job_id, shot):
        renderer = get_renderer("h3", runtime)
        count = shot.request.duration // 5
        row = load(job_id)
        claim_id = row.claim_id
        submitted = False
        terminal = False
        uncertain = False
        with runtime.SessionLocal() as db:
            for i in range(count):
                segment_id = f"{job_id}-{i}"
                if not db.get(MotionSegment, segment_id):
                    db.add(MotionSegment(id=segment_id, job_id=job_id, position=i, status="waiting"))
            db.commit()
        async with runtime.gpu_heavy_sem:
            try:
                if not claim_id:
                    await update(job_id, status="preparing", message="Reserving H3 and recovering idle model memory…")
                    ready = await capabilities()
                    if ready["h3"].get("can_recover_memory"):
                        await recover_memory()
                    prepared = await runtime.prepare_workload("h3", job_id=job_id, claim_id=job_id, workload_id="motion-h3",
                                                              model_label="MiniMax H3 sequential", frames=124, width=shot.width, height=shot.height)
                    claim_id = prepared["claim_id"]
                    await update(job_id, claim_id=claim_id)
                previous = None
                outputs = []
                for i in range(count):
                    segment_id = f"{job_id}-{i}"
                    with runtime.SessionLocal() as db:
                        clip = db.get(MotionSegment, segment_id)
                    submitted = bool(clip.prompt_id)
                    terminal = clip.status == "completed"
                    if terminal and clip.output_path:
                        previous = runtime.resolve_output_path(clip.output_path)
                        outputs.append(previous)
                        continue
                    bounded = segment_shot(shot, i)
                    if not submitted:
                        if i:
                            await update(job_id, status="preparing", message=f"Clip {i+1}/{count}: recovering 90 GiB before continuing…")
                            response = await runtime.http_client.post(f"{runtime.SUPERVISOR_URL.rstrip('/')}/recheck/h3", json={"claim_id": claim_id}, timeout=45)
                            response.raise_for_status()
                        context = await media.continuation(previous, runtime.resolve_output_path(f"motion_director/{job_id}"), i, shot.width, shot.height) if previous else None
                        # Persist submission intent before POST: uncertain acks and
                        # gateway restarts must never submit this clip twice.
                        if clip.status == "submitting":
                            uncertain = True
                            raise SubmissionUncertain("A clip submission acknowledgement is missing. Inspect H3 before retrying.")
                        segment_update(segment_id, status="submitting")
                        await update(job_id, status="loading", message=f"Clip {i+1}/{count}: continuing the planned scene…")
                        uncertain = True
                        try:
                            prompt_id = await renderer.submit(bounded, assets_for(shot.request), segment_id, context)
                        except httpx.HTTPStatusError:
                            uncertain = False  # definite non-acceptance, not cancellation
                            raise
                        uncertain = False
                        submitted = True
                        clip = segment_update(segment_id, status="running", prompt_id=prompt_id)
                        await update(job_id, prompt_id=prompt_id, status="running", message=f"Generating clip {i+1}/{count}…")
                    absent_since = None
                    while True:
                        try:
                            entry = await renderer.history(clip.prompt_id)
                            if entry:
                                status = entry.get("status", {})
                                if status.get("status_str") == "error" or any(m[0] in {"execution_error", "execution_interrupted"} for m in status.get("messages", [])):
                                    terminal = True
                                    segment_update(segment_id, status="failed")
                                    raise RuntimeError(f"H3 clip {i+1} failed; no new clip or automatic retry was submitted")
                                if status.get("completed") or status.get("status_str") == "success":
                                    terminal = True
                                    await update(job_id, status="finalizing", message=f"Saving clip {i+1}/{count}…")
                                    source = await renderer.retrieve(entry, segment_id)
                                    relative = f"motion_director/{job_id}/clip-{i}.mp4"
                                    previous = runtime.resolve_output_path(relative)
                                    await media.trim(runtime.resolve_output_path(source), previous, 5, bool(i))
                                    segment_update(segment_id, status="completed", source_path=source, output_path=relative)
                                    outputs.append(previous)
                                    break
                            queue = await renderer.queue()
                            present = any(len(item) > 1 and item[1] == clip.prompt_id for item in queue.get("queue_running", []) + queue.get("queue_pending", []))
                            if not entry and not present:
                                absent_since = absent_since or asyncio.get_running_loop().time()
                                if asyncio.get_running_loop().time() - absent_since > 60:
                                    terminal = True
                                    raise RuntimeError("Accepted clip disappeared. No automatic resubmission.")
                            else:
                                absent_since = None
                        except httpx.HTTPError:
                            await update(job_id, message=f"Clip {i+1}/{count}: reconnecting to H3; its reservation is retained…")
                        await asyncio.sleep(runtime.COMFYUI_POLL_INTERVAL_SEC)
                await update(job_id, status="finalizing", message="Joining continuous clips and restoring resources…")
                output = f"motion_director/{job_id}.mp4"
                await media.assemble(outputs, runtime.resolve_output_path(output), shot.request.duration)
                history_id = runtime.persist_generation_record(job_id=job_id, service="motion_director", payload=shot.model_dump(mode="json"), output_path=output, model_used="MiniMax H3 sequential")
                await update(job_id, status="completed", message=f"{shot.request.duration}-second video ready · {count} clips", output_path=output,
                             history_id=history_id, error=None, completed_at=runtime._now_utc())
            except SubmissionUncertain as exc:
                uncertain = True
                await update(job_id, status="failed", message="Clip submission needs inspection", error=str(exc), completed_at=runtime._now_utc())
            except Exception as exc:
                await update(job_id, status="failed", message="Video stopped safely", error=str(exc)[:1200], completed_at=runtime._now_utc())
            finally:
                if claim_id and not uncertain and (not submitted or terminal):
                    await runtime.release_workload("h3", claim_id)

    async def run(job_id):
        row = load(job_id)
        shot = ShotSpec.model_validate_json(row.shot_json)
        if shot.request.duration >= 5:
            await run_sequence(job_id, shot)
            return
        renderer = get_renderer("h3", runtime)
        claim_id = row.claim_id
        submitted = bool(row.prompt_id)
        terminal = False
        uncertain = False
        async with runtime.gpu_heavy_sem:
            try:
                if not submitted:
                    ready = await capabilities()
                    if not ready["h3"]["render_ready"]:
                        raise RuntimeError(ready["h3"].get("reason", "H3 is unavailable"))
                    workflow = ready["h3"].get("workflows", {}).get(shot.request.workflow, {})
                    if not workflow.get("available"):
                        raise RuntimeError(workflow.get("reason", "Selected H3 workflow is unavailable"))
                    await update(job_id, status="preparing", message="Reserving H3 and checking shared memory…")
                    prepared = await runtime.prepare_workload("h3", job_id=job_id, claim_id=job_id,
                                                              workload_id="motion-h3", model_label="MiniMax H3",
                                                              frames=shot.frames, width=shot.width, height=shot.height)
                    claim_id = prepared["claim_id"]
                    await update(job_id, status="loading", message="Uploading references to the existing H3 instance…", claim_id=claim_id)
                    uncertain = True
                    try:
                        prompt_id = await renderer.submit(shot, assets_for(shot.request), job_id)
                    except httpx.HTTPStatusError:
                        uncertain = False
                        raise
                    uncertain = False
                    submitted = True
                    row = await update(job_id, status="queued", message="H3 accepted the shot", prompt_id=prompt_id)
                else:
                    # Reconcile accepted prompts after restart; never resubmit inference.
                    await update(job_id, status="running", message="Reconnecting to the accepted H3 shot…", error=None)
                missing_since = None
                while True:
                    try:
                        entry = await renderer.history(row.prompt_id)
                        if entry:
                            status = entry.get("status", {})
                            errors = [m[1] for m in status.get("messages", []) if m[0] in {"execution_error", "execution_interrupted"}]
                            if status.get("status_str") == "error" or errors:
                                terminal = True
                                raise RuntimeError("H3 execution failed: " + str(errors)[:1200])
                            if status.get("completed") or status.get("status_str") == "success":
                                terminal = True
                                await update(job_id, status="finalizing", message="Retrieving the completed video…")
                                output = await renderer.retrieve(entry, job_id)
                                history_id = runtime.persist_generation_record(job_id=job_id, service="motion_director",
                                                                               payload=shot.model_dump(mode="json"), output_path=output, model_used="MiniMax H3")
                                await update(job_id, status="completed", message="Video ready", output_path=output,
                                             history_id=history_id, completed_at=runtime._now_utc(), error=None)
                                break
                        queue = await renderer.queue()
                        queued = any(len(item) > 1 and item[1] == row.prompt_id for item in queue.get("queue_pending", []))
                        running = any(len(item) > 1 and item[1] == row.prompt_id for item in queue.get("queue_running", []))
                        if not entry and not queued and not running:
                            missing_since = missing_since or asyncio.get_running_loop().time()
                            if asyncio.get_running_loop().time() - missing_since > 60:
                                # History and queue both confirm absence after a publication grace.
                                terminal = True
                                raise RuntimeError("The accepted H3 prompt disappeared from queue and history. No automatic retry was submitted.")
                        else:
                            missing_since = None
                        await update(job_id, status="queued" if queued else "running",
                                     message="Waiting in H3 queue…" if queued else "H3 is generating the shot…", error=None)
                    except httpx.HTTPError:
                        await update(job_id, status="running", message="H3 connection lost; keeping its reservation and waiting…")
                    await asyncio.sleep(runtime.COMFYUI_POLL_INTERVAL_SEC)
            except SubmissionUncertain as exc:
                uncertain = True
                await update(job_id, status="failed", message="Submission needs inspection", error=str(exc), completed_at=runtime._now_utc())
            except Exception as exc:
                detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                await update(job_id, status="failed", message="Shot failed", error=detail[:2000], completed_at=runtime._now_utc())
            finally:
                # Cancellation, lost acknowledgements and tracking errors must not
                # admit a competing job while an accepted render may still run.
                if claim_id and not uncertain and (not submitted or terminal):
                    await runtime.release_workload("h3", claim_id)

    def start(job_id):
        task = asyncio.create_task(run(job_id))
        tasks[job_id] = task
        task.add_done_callback(lambda done: tasks.pop(job_id, None))

    runtime.start_motion_job = start
    runtime.update_motion_job = update

    @router.get("/capabilities")
    async def get_capabilities():
        return await capabilities()

    @router.post("/memory/recover")
    async def recover_idle_memory():
        return await recover_memory()

    @router.post("/imports/grok")
    async def import_grok(file: UploadFile = File(...), reference_job_id: str | None = Form(default=None)):
        # Manual uploads only: never fetch caller URLs, resolve API keys or spend
        # credits. Original file is preserved separately from the phone preview.
        job_id = str(uuid.uuid4())
        original = runtime.resolve_output_path(f"motion_director/imports/{job_id}-original.mp4")
        original.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            header = await file.read(32)
            if b"ftyp" not in header:
                raise HTTPException(400, "Import an MP4 exported from Grok")
            with original.open("xb") as target:
                target.write(header)
                while chunk := await file.read(1024**2):
                    size += len(chunk)
                    if size > 200 * 1024**2:
                        raise HTTPException(413, "Imported video exceeds 200 MiB")
                    target.write(chunk)
            data = await media.probe(original)
            videos = [s for s in data["streams"] if s.get("codec_type") == "video"]
            duration = float(data["format"]["duration"])
            if not videos or not (0 < duration <= 60) or not (0 < videos[0]["width"] <= 4096 and 0 < videos[0]["height"] <= 4096):
                raise HTTPException(422, "Import a video up to 60 seconds and 4096 pixels per axis")
            base = ShotSpec.model_validate_json(load(reference_job_id).shot_json) if reference_job_id else None
            request = ShotRequest(prompt=base.request.prompt if base else "Grok Imagine video imported for comparison",
                                  reference_ids=base.reference_ids if base else ["manual-import"], engine="grok",
                                  duration=min((5, 10, 15, 30), key=lambda n: abs(n-duration)),
                                  orientation="vertical" if videos[0]["height"] > videos[0]["width"] else "horizontal")
            shot = LocalDirector().plan(request)
            shot.planner = "manual-import"
            shot.notes = [f"Manually imported Grok video, {duration:.2f} seconds. No Hermes/xAI generation call was made."]
            shot.frames = round(duration * 24)
            output = f"motion_director/{job_id}.mp4"
            await media.normalize_import(original, runtime.resolve_output_path(output), shot.width, shot.height)
            history_id = runtime.persist_generation_record(job_id=job_id, service="motion_director", payload=shot.model_dump(mode="json"), output_path=output, model_used="Grok manual import")
            with runtime.SessionLocal() as db:
                row = MotionJob(id=job_id, shot_json=shot.model_dump_json(), status="completed", message="Grok video imported · ready to compare",
                                output_path=output, history_id=history_id, created_at=runtime._now_utc(), completed_at=runtime._now_utc())
                db.add(row); db.commit()
            return serialize(row)
        except (RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(422, "The imported MP4 could not be validated or prepared for playback") from exc
        finally:
            await file.close()

    @router.post("/references")
    async def upload_reference(file: UploadFile = File(...)):
        header = await file.read(32)
        await file.seek(0)
        image = header.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"BM")) or (header[:4] == b"RIFF" and header[8:12] == b"WEBP")
        if not image:
            await file.close()
            raise HTTPException(400, "Upload a PNG, JPEG, WebP or BMP reference image")
        return await runtime.upload_comfyui_asset(file, kind="image")

    @router.get("/references/{asset_id}")
    async def reference_preview(asset_id: str):
        asset = runtime.get_comfyui_asset_or_404(asset_id, input_id="reference")
        if asset.kind != "image":
            raise HTTPException(422, "Reference must be an image")
        path = runtime.resolve_asset_path(asset.relative_path, runtime.COMFYUI_UPLOADS_DIR)
        if not path.is_file():
            raise HTTPException(404, "Reference file is missing")
        return FileResponse(path, media_type=asset.content_type)

    @router.post("/plan")
    async def plan(request: ShotRequest):
        assets_for(request)
        return await planned_shot(request)

    @router.post("/jobs", status_code=202)
    async def submit(request: ShotRequest):
        assets_for(request)
        if request.engine != "h3":
            raise HTTPException(503, "Hermes Grok OAuth video access is unverified; use manual import/compare. No paid fallback is allowed.")
        ready = await capabilities()
        if ready["h3"].get("can_recover_memory"):
            await recover_memory()
            ready = await capabilities()
        if not ready["h3"]["render_ready"]:
            raise HTTPException(503, ready["h3"].get("reason", "H3 is unavailable"))
        workflow = ready["h3"].get("workflows", {}).get(request.workflow, {})
        if not workflow.get("available"):
            raise HTTPException(503, workflow.get("reason", "Selected H3 workflow is unavailable"))
        if request.duration > 5 and not ready["h3"].get("continuation_available"):
            raise HTTPException(503, "Installed H3 continuation nodes are unavailable")
        shot = submitted_plan(request) if request.plan_id else await planned_shot(request, reuse_cache=False)
        with runtime.SessionLocal() as db:
            active_count = db.query(MotionJob).filter(MotionJob.status.notin_(["completed", "failed"])).count()
            if active_count >= 4:
                raise HTTPException(429, "Four shots are already waiting. Let one finish before adding another.")
            row = MotionJob(id=str(uuid.uuid4()), shot_json=shot.model_dump_json(), status="queued", message="Queued", created_at=runtime._now_utc())
            db.add(row)
            db.commit()
        await update(row.id)
        start(row.id)
        return serialize(row)

    @router.get("/jobs")
    async def list_jobs():
        with runtime.SessionLocal() as db:
            rows = db.execute(select(MotionJob).order_by(desc(MotionJob.created_at)).limit(50)).scalars().all()
        return {"items": [serialize(r) for r in rows]}

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str):
        return serialize(load(job_id))

    @router.post("/jobs/{job_id}/reconcile", status_code=202)
    async def reconcile(job_id: str):
        row = load(job_id)
        if not row.prompt_id:
            raise HTTPException(409, "No acknowledged prompt to reconcile; inspect H3 before resubmitting")
        if row.status == "completed" or job_id in tasks:
            return serialize(row)
        start(job_id)
        return serialize(row)

    return router


async def resume_motion_jobs(runtime):
    with runtime.SessionLocal() as db:
        rows = db.execute(select(runtime.MotionJob).where(runtime.MotionJob.status.notin_(["completed", "failed"]))).scalars().all()
    for row in rows:
        if row.prompt_id:
            runtime.start_motion_job(row.id)
        else:
            await runtime.update_motion_job(row.id, status="failed", message="Gateway restarted before acknowledgement",
                                            error="No automatic retry. Inspect H3's queue before submitting again.", completed_at=runtime._now_utc())


async def shutdown_motion_jobs():
    pending = list(tasks.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
