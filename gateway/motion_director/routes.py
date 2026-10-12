"""Persistent Motion Director jobs using the existing gateway and supervisor."""
from __future__ import annotations

import asyncio
import json
import hashlib
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import String, Text, DateTime, desc, select
from sqlalchemy.orm import Mapped, mapped_column

from .planner import LocalDirector, ShotRequest, ShotSpec
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

    def fingerprint(request):
        return hashlib.sha256(json.dumps(request.model_dump(exclude={"plan_id"}), sort_keys=True).encode()).hexdigest()

    async def planned_shot(request):
        async with planning_lock:
            key = fingerprint(request)
            with runtime.SessionLocal() as db:
                saved = db.execute(select(MotionPlan).where(MotionPlan.request_hash == key).order_by(desc(MotionPlan.created_at))).scalars().first()
            if saved:
                return ShotSpec.model_validate_json(saved.shot_json)
            shot = LocalDirector().plan(request)
            if request.director == "opus":
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
        return {"job_id": row.id, "service": "h3", "status": row.status, "message": row.message,
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
                scheduler = "h3" in response.json().get("managed_services", {})
            except (httpx.HTTPError, ValueError, TypeError):
                pass
        h3.update(scheduler_ready=scheduler, available_gb=round(available_gb, 1), minimum_gb=90,
                  render_ready=bool(h3.get("available") and scheduler and available_gb >= 90 and not h3.get("busy")))
        if h3.get("available") and not scheduler:
            h3["reason"] = "H3 reservation policy is not enabled in the supervisor. Configure the verified private H3 route in both services."
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
                if resources.get("blockers") or resources.get("exclusive_claims"):
                    h3["render_ready"] = False
                    h3["reason"] = " ".join(resources.get("blockers", [])) or "A GPU-exclusive generation is already active."
            except (RuntimeError, httpx.HTTPError, OSError):
                h3.update(render_ready=False, reason="Authenticated host GPU safety/coordination is unavailable.")
        return {"h3": h3, "grok": await get_renderer("grok", runtime).available(), "planner": planner, "resources": resources}

    async def run(job_id):
        row = load(job_id)
        shot = ShotSpec.model_validate_json(row.shot_json)
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
                    prompt_id = await renderer.submit(shot, assets_for(shot.request), job_id)
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
        shot = submitted_plan(request)
        ready = await capabilities()
        if not ready["h3"]["render_ready"]:
            raise HTTPException(503, ready["h3"].get("reason", "H3 is unavailable"))
        workflow = ready["h3"].get("workflows", {}).get(request.workflow, {})
        if not workflow.get("available"):
            raise HTTPException(503, workflow.get("reason", "Selected H3 workflow is unavailable"))
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
