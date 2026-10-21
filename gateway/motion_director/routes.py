"""Persistent Motion Director jobs using the existing gateway and supervisor."""
from __future__ import annotations

import asyncio
import json
import hashlib
import uuid
import time
from datetime import datetime, timezone
from typing import Literal
from shared.motion_quality import PROFILES, dimensions

import httpx
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import String, Text, DateTime, desc, select
from sqlalchemy.exc import IntegrityError
from redis.exceptions import RedisError
from sqlalchemy.orm import Mapped, mapped_column

from .planner import LocalDirector, ShotRequest, ShotSpec, segment_shot
from . import media
from .renderers import SubmissionRejected, SubmissionUncertain, get_renderer
from .host_client import host_request
from .scene import SceneSpec
from .planner import StrictModel
from pydantic import Field
from .usage import PlanningUsage
from .editing import camera_revision

PRODUCTION_VERSION='iteration-v1'

tasks: dict[str, asyncio.Task] = {}
planning_lock = asyncio.Lock()
submission_lock = asyncio.Lock()
watchdog: asyncio.Task | None = None
TERMINAL = {"completed", "failed", "interrupted", "blocked", "needs_inspection", "cancelled"}
TRACKING_TIMEOUT = 7200
CONNECTION_TIMEOUT = 300


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
        request_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
        dispatch_version: Mapped[int | None] = mapped_column(nullable=True)
        started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        queue_position: Mapped[int | None] = mapped_column(nullable=True)
        cancel_requested: Mapped[bool] = mapped_column(default=False, server_default="0")

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
        attempts_json: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    def same_scene_request(a, b):
        excluded = {'plan_id', 'parent_plan_id', 'revision', 'quality', 'seed'}
        return a.model_dump(exclude=excluded) == b.model_dump(exclude=excluded)

    def reusable_scene(shot):
        # Earlier camera edits could retain a parent after the user changed the
        # main prompt. Do not propagate that stale scene through the cache.
        if shot.request.parent_plan_id and shot.planning_usage and shot.planning_usage.source in {'local-camera','scene-reuse'}:
            with runtime.SessionLocal() as db:
                parent = db.get(MotionPlan, shot.request.parent_plan_id)
            return bool(parent and same_scene_request(shot.request, ShotSpec.model_validate_json(parent.shot_json).request))
        return True

    async def planned_shot(request, reuse_cache=True):
        async with planning_lock:
            started=time.monotonic()
            key = fingerprint(request)
            with runtime.SessionLocal() as db:
                saved = db.execute(select(MotionPlan).where(MotionPlan.request_hash == key).order_by(desc(MotionPlan.created_at))).scalars().first()
            if saved and reuse_cache:
                cached=ShotSpec.model_validate_json(saved.shot_json)
                if (cached.production_version==PRODUCTION_VERSION or not cached.scene) and (cached.width,cached.height)==dimensions(request.quality,request.orientation) and reusable_scene(cached):
                    cached.planning_usage=PlanningUsage(source='saved-plan',new_claude_invocations=0,
                        elapsed_seconds=round(time.monotonic()-started,3),original=cached.planning_usage.original if cached.planning_usage else None)
                    return cached
            shot = LocalDirector().plan(request)
            canvas = (shot.width, shot.height)
            previous = None
            reused=False
            if request.parent_plan_id:
                with runtime.SessionLocal() as db:
                    parent = db.get(MotionPlan, request.parent_plan_id)
                if not parent:
                    raise HTTPException(404, "The scene to revise is missing")
                parent_shot=ShotSpec.model_validate_json(parent.shot_json)
                previous = parent_shot.scene
                edited=camera_revision(parent_shot,request.revision) if same_scene_request(request,parent_shot.request) else None
                if edited:
                    shot=edited;reused=True
            if not reused and request.pipeline=='3d' and reuse_cache:
                def scene_key(value):
                    return value.model_dump(exclude={'plan_id','seed','quality'})
                with runtime.SessionLocal() as db:
                    recent=db.execute(select(MotionPlan).order_by(desc(MotionPlan.created_at)).limit(100)).scalars().all()
                for row in recent:
                    candidate=ShotSpec.model_validate_json(row.shot_json)
                    if candidate.scene and reusable_scene(candidate) and scene_key(candidate.request)==scene_key(request):
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
            # Reusing/correcting a scene must retain the requested generation
            # profile, even when its parent was planned at draft resolution.
            shot.width, shot.height = canvas
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
            shot = ShotSpec.model_validate_json(saved.shot_json)
            if (shot.width,shot.height) != dimensions(request.quality,request.orientation):
                raise HTTPException(409, 'This saved plan has inconsistent generation dimensions. Reuse the shot and rebuild its preview before generating.')
            return shot
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
        def stamp(value):
            return value.replace(tzinfo=timezone.utc).isoformat() if value else None
        with runtime.SessionLocal() as db:
            clips = db.execute(select(MotionSegment).where(MotionSegment.job_id == row.id).order_by(MotionSegment.position)).scalars().all()
            waiting = db.execute(select(MotionJob.id).where(MotionJob.status == 'queued', MotionJob.prompt_id.is_(None)).order_by(MotionJob.created_at, MotionJob.id)).scalars().all()
        now = runtime._now_utc().replace(tzinfo=None)
        elapsed = max(0, ((row.completed_at or now).replace(tzinfo=None) - (row.started_at or row.created_at).replace(tzinfo=None)).total_seconds())
        uncertain = any(c.status == 'submitting' for c in clips)
        scene_plan = json.loads(row.shot_json).get("request", {}).get("plan_id") if json.loads(row.shot_json).get("scene") else None
        return {"job_id": row.id, "service": json.loads(row.shot_json).get("request", {}).get("engine", "h3"), "status": row.status, "message": row.message,
                "generation_claude_invocations":0,
                "queue_position": waiting.index(row.id)+1 if row.id in waiting else row.queue_position,
                "elapsed_seconds": round(elapsed), "started_at": stamp(row.started_at),
                "updated_at": stamp(row.updated_at),
                "cancel_requested": row.cancel_requested,
                "can_cancel": row.status not in TERMINAL and not row.cancel_requested,
                "can_retry": row.status in {'failed','blocked','interrupted','cancelled'} and not uncertain and all(not c.prompt_id or c.status in {'completed','failed'} for c in clips),
                "can_reconcile": bool(row.prompt_id or uncertain or any(c.prompt_id for c in clips)) and row.status != 'completed',
                "blender_preview_url": f"/api/v1/outputs/motion_director/scenes/{scene_plan}/preview.mp4" if scene_plan else None,
                "segments": [{"index": c.position, "status": c.status, "prompt_id": c.prompt_id} for c in clips],
                "completed_segments": sum(c.status == "completed" for c in clips), "total_segments": len(clips),
                "error": row.error, "created_at": stamp(row.created_at), "prompt_id": row.prompt_id,
                "result_path": row.output_path, "shot": json.loads(row.shot_json),
                "preview_url": f"/api/v1/outputs/{row.output_path}" if row.output_path else None,
                "download_url": f"/api/v1/history/{row.history_id}/download" if row.history_id else None}

    async def update(job_id, **changes):
        changes['updated_at'] = runtime._now_utc()
        with runtime.SessionLocal() as db:
            row = db.get(MotionJob, job_id)
            for key, value in changes.items():
                setattr(row, key, value)
            db.commit()
        try:
            await runtime.store_job(runtime.JobRecord(job_id=row.id, service="h3", status=row.status,
                                                 created_at=row.created_at.isoformat(), result_path=row.output_path,
                                                 completed_at=row.completed_at.isoformat() if row.completed_at else None,
                                                 message=row.message, error=row.error))
        except (RedisError, OSError) as exc:
            runtime.log.warning('Motion job %s persisted; Redis cache unavailable: %s', job_id, type(exc).__name__)
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
                shared_gpu = resources.get('shared_gpu') or {}
                resident_models = [item.get('name', 'another model') for item in (shared_gpu.get('releasable') or {}).get('items', [])
                                   if item.get('kind') in {'ollama_model', 'hunyuan_models'}]
                if resident_models:
                    h3.update(render_ready=False, reason="Other models are resident (" + ", ".join(resident_models[:8]) + "). Unload them before H3; automatic unloading is disabled.")
                blockers = [b for b in resources.get("blockers", []) if not b.startswith("H3 needs")]
                if blockers or resources.get("exclusive_claims") or shared_gpu.get("exclusive"):
                    h3["render_ready"] = False
                    h3["reason"] = " ".join(blockers) or "A GPU-exclusive generation is already active."
            except (RuntimeError, httpx.HTTPError, OSError):
                h3.update(render_ready=False, reason="Authenticated host GPU safety/coordination is unavailable.")
        h3["can_recover_memory"] = bool(h3.get("available") and scheduler and available_gb < minimum and not h3.get("busy") and resources
                                        and not resident_models
                                        and not resources.get("exclusive_claims") and not (resources.get("shared_gpu") or {}).get("exclusive")
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

    def ensure_segments(job_id, shot):
        with runtime.SessionLocal() as db:
            for i in range(max(1, shot.request.duration // 5)):
                segment_id = f'{job_id}-{i}'
                if not db.get(MotionSegment, segment_id):
                    db.add(MotionSegment(id=segment_id, job_id=job_id, position=i, status='waiting'))
            db.commit()

    async def release_claim(job_id):
        row = load(job_id)
        if row.claim_id and await runtime.release_workload('h3', row.claim_id):
            await update(job_id, claim_id=None)

    async def track_clip(job_id, clip, renderer, count):
        missing_since = None
        disconnected_since = None
        started = time.monotonic()
        guard_at = 0
        safety_stop = None
        while True:
            if time.monotonic() - started > TRACKING_TIMEOUT:
                raise SubmissionUncertain('H3 tracking timed out. Reservation retained; check the accepted shot. No resubmission occurred.')
            try:
                # A targeted interrupt is supported by the inspected local H3 server.
                # Never issue the global interrupt or delete another client's queue.
                if getattr(runtime, 'HOST_CONTROL_SOCKET', '') and time.monotonic() - guard_at >= 5:
                    guard_at = time.monotonic()
                    resources = await host_request(runtime, 'resources')
                    gpu = resources.get('gpu', {})
                    if gpu.get('temperature_c', 0) >= 70 or resources.get('memory', {}).get('available_gb', 100) < 40:
                        safety_stop = 'H3 stopped at the 70°C thermal or 40 GiB emergency memory guard. Cool down/recover memory, then retry explicitly.'
                        await renderer.interrupt(clip.prompt_id)
                    if load(job_id).cancel_requested:
                        await renderer.interrupt(clip.prompt_id)
                entry = await renderer.history(clip.prompt_id)
                if entry:
                    status = entry.get('status', {})
                    errors = [m[1] for m in status.get('messages', []) if m[0] in {'execution_error', 'execution_interrupted'}]
                    if status.get('status_str') == 'error' or errors:
                        segment_update(clip.id, status='failed')
                        raise RuntimeError(safety_stop or 'H3 execution failed: ' + str(errors)[:1200])
                    if status.get('completed') or status.get('status_str') == 'success':
                        return entry
                queue = await renderer.queue()
                pending = [item[1] for item in queue.get('queue_pending', []) if len(item) > 1]
                running = [item[1] for item in queue.get('queue_running', []) if len(item) > 1]
                if clip.prompt_id not in pending + running and not entry:
                    missing_since = missing_since or time.monotonic()
                    if time.monotonic() - missing_since > 60:
                        raise SubmissionUncertain('The accepted H3 prompt is absent from queue and history. Check H3; automatic resubmission is disabled.')
                else:
                    missing_since = None
                queued = clip.prompt_id in pending
                message = f'Clip {clip.position+1}/{count}: ' + (f'H3 queue position {pending.index(clip.prompt_id)+1}' if queued else 'H3 is generating')
                if load(job_id).cancel_requested:
                    message = 'Cancellation requested; waiting for the accepted H3 clip to stop safely.'
                await update(job_id, status='queued' if queued else 'running', message=message,
                             queue_position=pending.index(clip.prompt_id)+1 if queued else None)
                disconnected_since = None
            except (httpx.HTTPError, OSError) as exc:
                disconnected_since = disconnected_since or time.monotonic()
                await update(job_id, status='running', message='H3 connection lost; reservation retained. Reconnecting…', error=type(exc).__name__)
                if time.monotonic() - disconnected_since > CONNECTION_TIMEOUT:
                    raise SubmissionUncertain('H3 has been unreachable for 5 minutes. Check the accepted shot to reconnect; no resubmission occurred.') from exc
            await asyncio.sleep(runtime.COMFYUI_POLL_INTERVAL_SEC)

    async def run(job_id, recovery_only=False):
        row = load(job_id)
        shot = ShotSpec.model_validate_json(row.shot_json)
        renderer = get_renderer('h3', runtime)
        if (shot.width,shot.height) != dimensions(shot.request.quality,shot.request.orientation):
            await update(job_id, status='blocked', message='Saved plan needs a generation-size correction',
                         error='Reuse this shot to repair its canvas without another Claude call, review the Blender preview, then generate.', completed_at=runtime._now_utc())
            return
        ensure_segments(job_id, shot)
        count = max(1, shot.request.duration // 5)
        uncertain = False
        accepted_active = False
        async with runtime.gpu_heavy_sem:
            try:
                row = load(job_id)
                if row.status in {'cancelled', 'completed'}:
                    return
                await update(job_id, started_at=row.started_at or runtime._now_utc(), error=None)
                previous, outputs = None, []
                for i in range(count):
                    segment_id = f'{job_id}-{i}'
                    with runtime.SessionLocal() as db:
                        clip = db.get(MotionSegment, segment_id)
                    if clip.status == 'completed' and clip.output_path:
                        previous = runtime.resolve_output_path(clip.output_path)
                        if not previous.is_file():
                            raise RuntimeError('A saved clip file is missing. Restore the artifact before continuing; no generation was resubmitted.')
                        outputs.append(previous)
                        continue
                    if clip.status == 'submitting' and not clip.prompt_id:
                        uncertain = True
                        recovered = await renderer.find_submission(segment_id)
                        if not recovered:
                            raise SubmissionUncertain('Submission acknowledgement is missing and no matching H3 client ID was found. Reservation retained. Check again; do not submit a duplicate.')
                        clip = segment_update(segment_id, status='running', prompt_id=recovered)
                        await update(job_id, prompt_id=recovered)
                        uncertain = False
                    if not clip.prompt_id:
                        if load(job_id).cancel_requested:
                            await update(job_id, status='cancelled', message='Cancelled before the next clip; saved clips and references are preserved.', completed_at=runtime._now_utc())
                            return
                        if recovery_only:
                            await update(job_id, status='interrupted', message='Accepted clips recovered. Continue the remaining clips when ready.', completed_at=runtime._now_utc())
                            return
                        ready = await capabilities(shot.request.quality)
                        if ready['h3'].get('can_recover_memory'):
                            await recover_memory()
                            ready = await capabilities(shot.request.quality)
                        # A held lease is rechecked by the supervisor below; its own
                        # claim must not look like somebody else's admission block.
                        if not load(job_id).claim_id and not ready['h3']['render_ready']:
                            raise HTTPException(409, ready['h3'].get('reason', 'H3 is unavailable'))
                        claim_id = load(job_id).claim_id
                        if not claim_id:
                            await update(job_id, status='preparing', message='Reserving H3 and checking shared memory…')
                            # Persist the deterministic claim intent before the request.
                            await update(job_id, claim_id=job_id)
                            prepared = await runtime.prepare_workload('h3', job_id=job_id, claim_id=job_id,
                                workload_id='motion-h3', model_label='MiniMax H3', frames=shot.frames if count == 1 else 124,
                                width=shot.width, height=shot.height)
                            await update(job_id, claim_id=prepared['claim_id'])
                        else:
                            response = await runtime.http_client.post(f"{runtime.SUPERVISOR_URL.rstrip('/')}/recheck/h3", json={'claim_id':claim_id}, timeout=60)
                            if response.status_code != 200:
                                raise HTTPException(409, response.json().get('detail', 'H3 resource recheck failed'))
                        bounded = segment_shot(shot, i) if shot.request.duration >= 5 else shot
                        context = await media.continuation(previous, runtime.resolve_output_path(f'motion_director/{job_id}'), i, shot.width, shot.height) if previous else None
                        scene_path = await media.scene_window(runtime.resolve_output_path(f'motion_director/scenes/{shot.request.plan_id}/preview.mp4'), runtime.resolve_output_path(f'motion_director/{job_id}'), i, shot.width, shot.height, shot.request.quality) if shot.scene else None
                        if load(job_id).cancel_requested:
                            await update(job_id, status='cancelled', message='Cancelled before H3 submission', completed_at=runtime._now_utc())
                            return
                        segment_update(segment_id, status='submitting')
                        await update(job_id, status='loading', message=f'Clip {i+1}/{count}: uploading references and submitting…')
                        uncertain = True
                        try:
                            if scene_path:
                                prompt_id = await renderer.submit(bounded, assets_for(shot.request), segment_id, context, scene_path=scene_path)
                            else:
                                prompt_id = await renderer.submit(bounded, assets_for(shot.request), segment_id, context)
                        except (httpx.HTTPStatusError, SubmissionRejected):
                            uncertain = False
                            segment_update(segment_id, status='failed')
                            raise
                        uncertain = False
                        accepted_active = True
                        clip = segment_update(segment_id, status='running', prompt_id=prompt_id)
                        await update(job_id, prompt_id=prompt_id, status='running', message=f'Generating clip {i+1}/{count}…')
                    accepted_active = True
                    entry = await track_clip(job_id, clip, renderer, count)
                    accepted_active = False
                    await update(job_id, status='finalizing', message=f'Saving clip {i+1}/{count}…', queue_position=None)
                    source = await renderer.retrieve(entry, segment_id)
                    if shot.request.duration >= 5:
                        relative = f'motion_director/{job_id}/clip-{i}.mp4'
                        previous = runtime.resolve_output_path(relative)
                        if shot.request.quality == 'native':
                            await media.trim(runtime.resolve_output_path(source), previous, 5, bool(i), quality='native')
                        else:
                            await media.trim(runtime.resolve_output_path(source), previous, 5, bool(i))
                    else:
                        relative = source
                        previous = runtime.resolve_output_path(source)
                    segment_update(segment_id, status='completed', source_path=source, output_path=relative)
                    outputs.append(previous)
                await update(job_id, status='finalizing', message='Saving video and restoring resources…')
                output = f'motion_director/{job_id}.mp4'
                if shot.request.duration >= 5:
                    await media.assemble(outputs, runtime.resolve_output_path(output), shot.request.duration)
                else:
                    output = clip.output_path if clip.output_path else relative
                # Repeated retrieval/finalization must not duplicate history records.
                with runtime.SessionLocal() as db:
                    history = db.execute(select(runtime.GenerationHistory).where(runtime.GenerationHistory.job_id == job_id)).scalars().first()
                history_id = history.id if history else runtime.persist_generation_record(job_id=job_id, service='motion_director', payload=shot.model_dump(mode='json'), output_path=output, model_used='MiniMax H3')
                await update(job_id, status='completed', message='Video ready', output_path=output, history_id=history_id,
                             error=None, queue_position=None, completed_at=runtime._now_utc())
            except asyncio.CancelledError:
                await update(job_id, status='interrupted', message='Worker stopped; accepted clips will be reconciled without resubmission.',
                             error='No automatic generation retry.', completed_at=runtime._now_utc())
                raise
            except SubmissionUncertain as exc:
                uncertain = True
                await update(job_id, status='needs_inspection', message='H3 submission needs reconciliation', error=str(exc), queue_position=None, completed_at=runtime._now_utc())
            except Exception as exc:
                with runtime.SessionLocal() as db:
                    failed_clip = db.execute(select(MotionSegment).where(MotionSegment.job_id == job_id, MotionSegment.status == 'failed')).scalars().first()
                if failed_clip:
                    accepted_active = False
                detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                status = 'cancelled' if load(job_id).cancel_requested and failed_clip else 'blocked' if isinstance(exc, HTTPException) else 'failed'
                await update(job_id, status=status, message='Cancelled safely' if status == 'cancelled' else 'Generation blocked' if status == 'blocked' else 'Video stopped safely',
                             error=detail[:2000], queue_position=None, completed_at=runtime._now_utc())
            finally:
                if not uncertain and not accepted_active:
                    await release_claim(job_id)

    def start(job_id, recovery_only=False):
        if job_id in tasks and not tasks[job_id].done():
            return
        task = asyncio.create_task(run(job_id, recovery_only))
        tasks[job_id] = task
        def finished(done):
            if tasks.get(job_id) is done:
                tasks.pop(job_id, None)
            if not done.cancelled() and done.exception():
                runtime.log.error('Motion worker %s failed: %s', job_id, done.exception())
        task.add_done_callback(finished)

    async def recover(job_id):
        row = load(job_id)
        if row.status == 'completed' or job_id in tasks:
            return row
        shot = ShotSpec.model_validate_json(row.shot_json)
        ensure_segments(job_id, shot)
        with runtime.SessionLocal() as db:
            clips = db.execute(select(MotionSegment).where(MotionSegment.job_id == job_id)).scalars().all()
        # Pre-ledger short jobs stored the acknowledgement on the parent only.
        if row.prompt_id and not any(c.prompt_id for c in clips) and shot.request.duration < 5:
            segment_update(clips[0].id, prompt_id=row.prompt_id, status='running')
        if row.prompt_id or any(c.prompt_id or c.status == 'submitting' for c in clips):
            await update(job_id, status='running', completed_at=None, message='Checking accepted H3 work; no new clips will be submitted…')
            start(job_id, recovery_only=True)
        else:
            await update(job_id, status='interrupted', message='Submission was interrupted before H3 acknowledgement. Retry this take when ready.',
                         error='No automatic retry. Existing references, plan and outputs are preserved.', completed_at=runtime._now_utc())
        return load(job_id)

    runtime.start_motion_job = start
    runtime.update_motion_job = update
    runtime.recover_motion_job = recover

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
    async def submit(request: ShotRequest, idempotency_key: str | None = Header(default=None, min_length=8, max_length=64)):
        if idempotency_key:
            with runtime.SessionLocal() as db:
                existing = db.execute(select(MotionJob).where(MotionJob.request_key == idempotency_key)).scalars().first()
            if existing:
                if fingerprint(ShotSpec.model_validate_json(existing.shot_json).request) != fingerprint(request):
                    raise HTTPException(409, 'Submission key already belongs to a different shot')
                return serialize(existing)
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
        async with submission_lock:
            with runtime.SessionLocal() as db:
                active_count = db.query(MotionJob).filter(MotionJob.status.notin_(TERMINAL)).count()
                if active_count >= 4:
                    raise HTTPException(429, "Four shots are already waiting. Let one finish before adding another.")
                row = MotionJob(id=str(uuid.uuid4()), shot_json=shot.model_dump_json(), status="queued", message="Saved in the generation queue",
                                created_at=runtime._now_utc(), updated_at=runtime._now_utc(), dispatch_version=1, request_key=idempotency_key)
                db.add(row)
                for i in range(max(1, request.duration // 5)):
                    db.add(MotionSegment(id=f'{row.id}-{i}', job_id=row.id, position=i, status='waiting'))
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    existing = db.execute(select(MotionJob).where(MotionJob.request_key == idempotency_key)).scalars().first() if idempotency_key else None
                    if not existing:
                        raise
                    if fingerprint(ShotSpec.model_validate_json(existing.shot_json).request) != fingerprint(request):
                        raise HTTPException(409, 'Submission key already belongs to a different shot')
                    return serialize(existing)
        # DB transaction is the durable outbox and acknowledgement. Redis cannot
        # strand dispatch between commit and task creation. The watchdog covers
        # a process interruption before start().
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
        return serialize(await recover(job_id))

    @router.post('/jobs/{job_id}/retry', status_code=202)
    async def retry(job_id: str):
        async with submission_lock:
            row = load(job_id)
            if job_id in tasks or row.status not in TERMINAL - {'completed', 'needs_inspection'}:
                raise HTTPException(409, 'This take is active or needs H3 reconciliation first')
            shot = ShotSpec.model_validate_json(row.shot_json)
            assets_for(shot.request)
            ensure_segments(job_id, shot)
            with runtime.SessionLocal() as db:
                clips = db.execute(select(MotionSegment).where(MotionSegment.job_id == job_id)).scalars().all()
            renderer = get_renderer('h3', runtime)
            for clip in clips:
                if clip.status == 'submitting' and not clip.prompt_id:
                    raise HTTPException(409, 'Submission acknowledgement is uncertain. Check accepted H3 work first.')
                if clip.prompt_id and clip.status != 'completed':
                    entry = await renderer.history(clip.prompt_id)
                    state = entry.get('status', {}) if entry else {}
                    if state.get('completed') or state.get('status_str') == 'success':
                        if state.get('status_str') != 'error':
                            raise HTTPException(409, 'H3 has a completed clip. Check accepted H3 work to recover it.')
                    if state.get('status_str') != 'error' and not any(m[0] in {'execution_error','execution_interrupted'} for m in state.get('messages', [])):
                        raise HTTPException(409, 'H3 has not confirmed the previous prompt stopped. Reconcile it first.')
            if row.prompt_id and not any(c.prompt_id for c in clips):
                raise HTTPException(409, 'An older accepted prompt needs inspection before retrying')
            if db_count_active() >= 4:
                raise HTTPException(429, 'Four shots are already active')
            await release_claim(job_id)
            for clip in clips:
                if clip.status != 'completed':
                    attempts = json.loads(clip.attempts_json or '[]')
                    if clip.prompt_id:
                        attempts.append({'prompt_id':clip.prompt_id, 'status':clip.status, 'at':runtime._now_utc().isoformat()})
                    segment_update(clip.id, prompt_id=None, status='waiting', attempts_json=json.dumps(attempts))
            await update(job_id, status='queued', message='Explicit retry queued; saved clips will be reused', error=None,
                         prompt_id=None, dispatch_version=1, cancel_requested=False, completed_at=None)
            start(job_id)
            return serialize(load(job_id))

    def db_count_active():
        with runtime.SessionLocal() as db:
            return db.query(MotionJob).filter(MotionJob.status.notin_(TERMINAL)).count()

    @router.post('/jobs/{job_id}/cancel')
    async def cancel(job_id: str):
        row = load(job_id)
        if row.status in TERMINAL:
            return serialize(row)
        with runtime.SessionLocal() as db:
            clips = db.execute(select(MotionSegment).where(MotionSegment.job_id == job_id)).scalars().all()
        if not row.claim_id and not any(c.prompt_id or c.status == 'submitting' for c in clips):
            row = await update(job_id, status='cancelled', cancel_requested=True, message='Cancelled before H3 submission', completed_at=runtime._now_utc())
        else:
            row = await update(job_id, cancel_requested=True, message='Cancellation requested; accepted H3 work is tracked until safe to release.')
        return serialize(row)

    return router


async def resume_motion_jobs(runtime):
    global watchdog
    with runtime.SessionLocal() as db:
        rows = db.execute(select(runtime.MotionJob).where(runtime.MotionJob.status != "completed")).scalars().all()
    for row in rows:
        if row.status == 'queued' and row.dispatch_version == 1 and not row.prompt_id:
            runtime.start_motion_job(row.id)
        elif row.status not in TERMINAL or row.status == 'interrupted':
            await runtime.recover_motion_job(row.id)
    async def dispatch_loop():
        while True:
            await asyncio.sleep(5)
            try:
                with runtime.SessionLocal() as db:
                    rows = db.execute(select(runtime.MotionJob).where(runtime.MotionJob.status.notin_(TERMINAL))).scalars().all()
                for row in rows:
                    if row.id not in tasks:
                        if row.status == 'queued' and row.dispatch_version == 1 and not row.prompt_id:
                            runtime.start_motion_job(row.id)
                        else:
                            await runtime.recover_motion_job(row.id)
            except Exception:
                runtime.log.exception('Motion dispatch reconciliation failed; durable jobs retained')
    watchdog = asyncio.create_task(dispatch_loop())


async def shutdown_motion_jobs():
    global watchdog
    if watchdog:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)
        watchdog = None
    pending = list(tasks.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
