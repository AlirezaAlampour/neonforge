"""Persistent Motion Director jobs using the existing gateway and supervisor."""
from __future__ import annotations

import asyncio
import json
import hashlib
import uuid
import time
from datetime import datetime
from typing import Literal
from shared.motion_quality import PROFILES

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import String, Text, DateTime, desc, select
from sqlalchemy.orm import Mapped, mapped_column

from .planner import LocalDirector, ShotRequest, ShotSpec, segment_shot
from . import media
from .renderers import SubmissionUncertain, get_renderer
from .host_client import host_request
from .scene import SceneSpec
from .planner import StrictModel
from pydantic import Field
from .usage import PlanningUsage
from .editing import camera_revision

PRODUCTION_VERSION='iteration-v1'

tasks: dict[str, asyncio.Task] = {}
planning_lock = asyncio.Lock()


class Revision(StrictModel):
    instruction: str = Field(min_length=1, max_length=1200)


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

    def load_plan(plan_id):
        with runtime.SessionLocal() as db:
            row = db.get(MotionPlan, plan_id)
        if not row:
            raise HTTPException(404, "Scene plan not found")
        return ShotSpec.model_validate_json(row.shot_json)

    def preview_response(state):
        root = f"/api/v1/outputs/motion_director/scenes/{state['id']}"
        if state['status'] == 'completed':
            state.update(preview_url=root+'/preview.mp4', first_frame_url=root+'/first-frame.png',
                         export_url=root+'/grok-export.zip', prompt_url=root+'/prompt.txt', script_url=root+'/scene.py')
        return state

    def fingerprint(request):
        data = request.model_dump(exclude={"plan_id"})
        if data.get('quality') == 'draft':
            data.pop('quality')  # pre-quality saved plans keep their fingerprint
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    async def planned_shot(request, reuse_cache=True):
        async with planning_lock:
            started=time.monotonic()
            key = fingerprint(request)
            with runtime.SessionLocal() as db:
                saved = db.execute(select(MotionPlan).where(MotionPlan.request_hash == key).order_by(desc(MotionPlan.created_at))).scalars().first()
            if saved and reuse_cache:
                cached=ShotSpec.model_validate_json(saved.shot_json)
                if cached.production_version==PRODUCTION_VERSION or not cached.scene:
                    cached.planning_usage=PlanningUsage(source='saved-plan',new_claude_invocations=0,
                        elapsed_seconds=round(time.monotonic()-started,3),original=cached.planning_usage.original if cached.planning_usage else None)
                    return cached
            shot = LocalDirector().plan(request)
            previous = None
            reused=False
            if request.parent_plan_id:
                with runtime.SessionLocal() as db:
                    parent = db.get(MotionPlan, request.parent_plan_id)
                if not parent:
                    raise HTTPException(404, "The scene to revise is missing")
                parent_shot=ShotSpec.model_validate_json(parent.shot_json)
                previous = parent_shot.scene
                edited=camera_revision(parent_shot,request.revision)
                if edited:
                    shot=edited;reused=True
            if not reused and request.pipeline=='3d' and reuse_cache:
                def scene_key(value):
                    return value.model_dump(exclude={'plan_id','seed','quality'})
                with runtime.SessionLocal() as db:
                    recent=db.execute(select(MotionPlan).order_by(desc(MotionPlan.created_at)).limit(100)).scalars().all()
                for row in recent:
                    candidate=ShotSpec.model_validate_json(row.shot_json)
                    if candidate.scene and scene_key(candidate.request)==scene_key(request):
                        canvas=(shot.width,shot.height)
                        shot=candidate.model_copy(deep=True);shot.width,shot.height=canvas
                        shot.planning_usage=PlanningUsage(source='scene-reuse',new_claude_invocations=0,
                            elapsed_seconds=round(time.monotonic()-started,3),original=candidate.planning_usage.original if candidate.planning_usage else None)
                        reused=True;break
            if not reused and (request.director == "opus" or (request.director == "auto" and getattr(runtime, "HOST_CONTROL_SOCKET", ""))):
                try:
                    payload = {"shot": request.model_dump(), "references": [asset.relative_path for asset in assets_for(request)]}
                    if previous:
                        payload["previous_scene"] = previous
                    data = await host_request(runtime, "claude/plan", payload)
                    candidate = ShotSpec.model_validate(data)
                    if fingerprint(candidate.request) != key or candidate.reference_ids != shot.reference_ids or (candidate.width, candidate.height, candidate.frames) != (shot.width, shot.height, shot.frames):
                        raise ValueError("Director changed protected request or hardware bounds")
                    shot = candidate
                except (RuntimeError, httpx.HTTPError, ValueError, OSError):
                    if request.pipeline == "3d":
                        raise HTTPException(503, "Claude Opus could not produce a valid 3D scene. Check subscription availability and try planning again; no simulated scene or paid fallback was used.")
                    shot.notes.append("Opus unavailable or invalid. This shot uses the deterministic cinematic fallback; no retry occurs during rendering.")
            if request.pipeline == "3d":
                if not shot.scene:
                    raise HTTPException(503, "3D scenes require the existing authenticated Claude Opus director")
                try:
                    shot.scene = SceneSpec.model_validate(shot.scene).validate_timeline(request.duration).model_dump()
                except ValueError as exc:
                    raise HTTPException(422, "Invalid 3D geometry or timeline; no render was started") from exc
            plan_id = str(uuid.uuid4())
            shot.request = request.model_copy(update={"plan_id": plan_id})
            shot.production_version=PRODUCTION_VERSION
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
        scene_plan = json.loads(row.shot_json).get("request", {}).get("plan_id") if json.loads(row.shot_json).get("scene") else None
        return {"job_id": row.id, "service": json.loads(row.shot_json).get("request", {}).get("engine", "h3"), "status": row.status, "message": row.message,
                "generation_claude_invocations":0,
                "blender_preview_url": f"/api/v1/outputs/motion_director/scenes/{scene_plan}/preview.mp4" if scene_plan else None,
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

    async def capabilities(quality="draft"):
        minimum = PROFILES[quality]["minimum_gib"]
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
        h3.update(scheduler_ready=scheduler, available_gb=round(available_gb, 1), minimum_gb=minimum, quality=quality, native_canvas=PROFILES[quality]["vertical"],
                  render_ready=bool(h3.get("available") and scheduler and available_gb >= minimum and not h3.get("busy")))
        if h3.get("available") and not scheduler:
            h3["reason"] = "H3 safety coordination is unavailable. Wait for the resource scheduler and its private host connection to recover."
        elif h3.get("available") and available_gb < minimum:
            h3["reason"] = f"H3 requires {minimum:g} GiB available; current {available_gb:.1f} GiB. Existing services will be preserved."
        elif h3.get("busy"):
            h3["reason"] = "H3 has an existing workload. Wait for it to finish."
        planner = {"active": "local-director", "opus_available": False,
                   "reason": "The private host Claude Code adapter is unavailable. Deterministic planning remains available."}
        resources = None
        resident_models = []
        if getattr(runtime, "HOST_CONTROL_SOCKET", ""):
            try:
                status = await host_request(runtime, "claude/status")
                planner.update(opus_available=status.get("available", False), subscription=status.get("subscription"), reason=status.get("reason"))
                resources = await host_request(runtime, "resources")
                resident_models = [item.get('name', 'another model') for item in resources.get('shared_gpu', {}).get('releasable', {}).get('items', [])
                                   if item.get('kind') in {'ollama_model', 'hunyuan_models'}]
                if resident_models:
                    h3.update(render_ready=False, reason="Other models are resident (" + ", ".join(resident_models[:8]) + "). Unload them before H3; automatic unloading is disabled.")
                blockers = [b for b in resources.get("blockers", []) if not b.startswith("H3 needs")]
                if blockers or resources.get("exclusive_claims") or resources.get("shared_gpu", {}).get("exclusive"):
                    h3["render_ready"] = False
                    h3["reason"] = " ".join(blockers) or "A GPU-exclusive generation is already active."
            except (RuntimeError, httpx.HTTPError, OSError):
                h3.update(render_ready=False, reason="Authenticated host GPU safety/coordination is unavailable.")
        h3["can_recover_memory"] = bool(h3.get("available") and scheduler and available_gb < minimum and not h3.get("busy") and resources
                                        and not resident_models
                                        and not resources.get("exclusive_claims") and not resources.get("shared_gpu", {}).get("exclusive")
                                        and not [b for b in resources.get("blockers", []) if not b.startswith("H3 needs")])
        blender = {"available": False, "reason": "The private Blender host connection is unavailable"}
        if getattr(runtime, "HOST_CONTROL_SOCKET", ""):
            try:
                blender = await host_request(runtime, "scene/capabilities")
            except (RuntimeError, httpx.HTTPError, OSError):
                pass
        return {"h3": h3, "grok": await get_renderer("grok", runtime).available(), "planner": planner, "resources": resources, "blender": blender}

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
                    ready = await capabilities(shot.request.quality)
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
                        scene_path = await media.scene_window(runtime.resolve_output_path(f"motion_director/scenes/{shot.request.plan_id}/preview.mp4"), runtime.resolve_output_path(f"motion_director/{job_id}"), i, shot.width, shot.height, shot.request.quality) if shot.scene else None
                        # Persist submission intent before POST: uncertain acks and
                        # gateway restarts must never submit this clip twice.
                        if clip.status == "submitting":
                            uncertain = True
                            raise SubmissionUncertain("A clip submission acknowledgement is missing. Inspect H3 before retrying.")
                        segment_update(segment_id, status="submitting")
                        await update(job_id, status="loading", message=f"Clip {i+1}/{count}: continuing the planned scene…")
                        uncertain = True
                        try:
                            if shot.scene:
                                prompt_id = await renderer.submit(bounded, assets_for(shot.request), segment_id, context, scene_path=scene_path)
                            else:
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
                                    if shot.request.quality == 'native':
                                        await media.trim(runtime.resolve_output_path(source), previous, 5, bool(i), quality='native')
                                    else:
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
                    ready = await capabilities(shot.request.quality)
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
                        if shot.scene:
                            window = await media.scene_window(runtime.resolve_output_path(f"motion_director/scenes/{shot.request.plan_id}/preview.mp4"), runtime.resolve_output_path(f"motion_director/{job_id}"), 0, shot.width, shot.height, shot.request.quality)
                            prompt_id = await renderer.submit(shot, assets_for(shot.request), job_id, scene_path=window)
                        else:
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
    async def get_capabilities(quality: Literal["draft", "native"] = "draft"):
        return await capabilities(quality)

    @router.post("/memory/recover")
    async def recover_idle_memory():
        return await recover_memory()

    @router.post("/imports/grok")
    async def import_grok(file: UploadFile = File(...), reference_job_id: str | None = Form(default=None), reference_plan_id: str | None = Form(default=None)):
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
            base = load_plan(reference_plan_id) if reference_plan_id else ShotSpec.model_validate_json(load(reference_job_id).shot_json) if reference_job_id else None
            request = ShotRequest(prompt=base.request.prompt if base else "Grok Imagine video imported for comparison",
                                  reference_ids=base.reference_ids if base else ["manual-import"], engine="grok",
                                  duration=min((5, 10, 15, 30), key=lambda n: abs(n-duration)),
                                  orientation="vertical" if videos[0]["height"] > videos[0]["width"] else "horizontal")
            shot = LocalDirector().plan(request)
            if base and base.scene:
                shot.scene = base.scene
                shot.request.plan_id = base.request.plan_id
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

    @router.post('/plans/{plan_id}/preview', status_code=202)
    async def build_preview(plan_id: str):
        shot = load_plan(plan_id)
        if not shot.scene:
            raise HTTPException(422, 'This older shot has no 3D scene; create a new 3D preview')
        SceneSpec.model_validate(shot.scene).validate_timeline(shot.request.duration)
        try:
            state = await host_request(runtime, 'scene/start', {'id':plan_id, 'scene':shot.scene, 'duration':shot.request.duration,
                                                               'width':shot.width, 'height':shot.height, 'prompt':shot.renderer_prompt})
        except (RuntimeError, httpx.HTTPError, OSError) as exc:
            raise HTTPException(503, 'The private Blender preview service is unavailable') from exc
        return preview_response(state)

    @router.get('/plans/{plan_id}/preview')
    async def preview_status(plan_id: str):
        load_plan(plan_id)
        try:
            return preview_response(await host_request(runtime, 'scene/status', {'id':plan_id}))
        except (RuntimeError, httpx.HTTPError, OSError) as exc:
            raise HTTPException(503, 'The private Blender preview service is unavailable') from exc

    @router.post('/plans/{plan_id}/revise')
    async def revise_scene(plan_id: str, change: Revision):
        base = load_plan(plan_id)
        if not base.scene or not change.instruction.strip():
            raise HTTPException(422, 'Describe a correction to an existing 3D scene')
        request = base.request.model_copy(update={'parent_plan_id':plan_id, 'revision':change.instruction.strip(), 'plan_id':None})
        assets_for(request)
        return await planned_shot(request)

    @router.post("/jobs", status_code=202)
    async def submit(request: ShotRequest):
        assets_for(request)
        if request.engine != "h3":
            raise HTTPException(503, "Hermes Grok OAuth video access is unverified; use manual import/compare. No paid fallback is allowed.")
        if request.pipeline == '3d':
            if not request.plan_id:
                raise HTTPException(409, 'Build and review the Blender preview before generating the final video')
            shot = submitted_plan(request)
            if not shot.scene:
                raise HTTPException(409, 'A validated 3D scene is required')
            state = await host_request(runtime, 'scene/status', {'id':request.plan_id})
            if state.get('status') != 'completed' or not runtime.resolve_output_path(f'motion_director/scenes/{request.plan_id}/preview.mp4').is_file():
                raise HTTPException(409, 'Wait for the Blender preview to finish before generating H3')
        ready = await capabilities(request.quality)
        if ready["h3"].get("can_recover_memory"):
            await recover_memory()
            ready = await capabilities(request.quality)
        if not ready["h3"]["render_ready"]:
            raise HTTPException(503, ready["h3"].get("reason", "H3 is unavailable"))
        workflow = ready["h3"].get("workflows", {}).get(request.workflow, {})
        if not workflow.get("available"):
            raise HTTPException(503, workflow.get("reason", "Selected H3 workflow is unavailable"))
        if (request.duration > 5 or request.quality == "native") and not ready["h3"].get("continuation_available"):
            raise HTTPException(503, "Installed H3 continuation nodes are unavailable")
        if request.pipeline == '3d' and not ready['h3'].get('reference_video'):
            raise HTTPException(503, 'The installed H3 reference-video nodes are unavailable')
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
