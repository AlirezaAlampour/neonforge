"""
API Gateway — routes requests, enforces job queue, memory gating.

Design:
  - All client requests enter through this gateway.
  - GPU jobs are serialized: heavy=1, medium=1 concurrency.
  - A Redis-backed queue tracks job state for observability.
  - UMA memory checks gate new GPU jobs (default hard limit: 80%).
  - Per-tier memory reservations prevent overcommit.
  - Orchestration (start/stop lazy containers) is delegated to
    the supervisor sidecar — this gateway has NO Docker socket access.
  - Persistent generation history + preset profiles are stored in SQLite.
"""

import asyncio
import json
import logging
import mimetypes
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, String, Text, UniqueConstraint, create_engine, desc, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
SUPERVISOR_URL = os.getenv("SUPERVISOR_URL", "http://supervisor:8000")
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://comfyui:8188")

OUTPUTS_ROOT = Path(os.getenv("OUTPUTS_ROOT", "/outputs"))
HISTORY_DB_PATH = Path(os.getenv("HISTORY_DB_PATH", str(OUTPUTS_ROOT / "neonforge_history.sqlite3")))
ASSETS_ROOT = Path(os.getenv("ASSETS_ROOT", "/app/data/assets"))
VOICE_ASSETS_DIR = Path(os.getenv("VOICE_ASSETS_DIR", str(ASSETS_ROOT / "voices")))
LORA_ASSETS_DIR = Path(os.getenv("LORA_ASSETS_DIR", str(ASSETS_ROOT / "loras")))

# UMA memory gating
MEMORY_WARN_PCT = int(os.getenv("MEMORY_WARN_THRESHOLD", "70"))
MEMORY_HARD_PCT = int(os.getenv("MEMORY_HARD_LIMIT", "80"))

# Per-tier memory reservations (GB that must remain free for tier admission)
MEM_RESERVE_HEAVY_GB = float(os.getenv("MEM_RESERVE_HEAVY_GB", "40"))
MEM_RESERVE_MEDIUM_GB = float(os.getenv("MEM_RESERVE_MEDIUM_GB", "10"))
MEM_RESERVE_LIGHT_GB = float(os.getenv("MEM_RESERVE_LIGHT_GB", "2"))

SERVICE_URLS = {
    "whisper": os.getenv("WHISPER_URL", "http://whisper:8000"),
    "f5tts": os.getenv("F5TTS_URL", "http://f5tts:8000"),
    "liveportrait": os.getenv("LIVEPORTRAIT_URL", "http://liveportrait:8000"),
    "lipsync": os.getenv("LIPSYNC_URL", "http://lipsync:8000"),
    "wan21": os.getenv("WAN21_URL", "http://wan21:8000"),
}

MODEL_NAME_BY_SERVICE = {
    "whisper": "Faster-Whisper",
    "f5tts": "F5-TTS",
    "liveportrait": "LivePortrait",
    "lipsync": os.getenv("LIPSYNC_BACKEND", "video-retalking"),
    "wan21": f"Wan 2.1 {os.getenv('WAN21_MODEL_VARIANT', '1.3B')}",
    "reactor": "ComfyUI/ReActor",
}

GPU_HEAVY_SERVICES = {"wan21"}
GPU_MEDIUM_SERVICES = {"liveportrait", "lipsync", "f5tts", "reactor"}
GPU_LIGHT_SERVICES = {"whisper"}

VOICE_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
    ".m4a",
    ".webm",
}
LORA_EXTENSIONS = {
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".bin",
}

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("gateway")


# ---------------------------------------------------------------------------
# Database (SQLAlchemy + SQLite)
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class GenerationHistory(Base):
    __tablename__ = "generation_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    service: Mapped[str] = mapped_column(String(64), index=True)
    model_used: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parameters_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PresetProfile(Base):
    __tablename__ = "preset_profiles"
    __table_args__ = (
        UniqueConstraint("name", "tool", name="uq_preset_name_tool"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    tool: Mapped[str] = mapped_column(String(64), index=True)
    state_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_db_url = f"sqlite:///{HISTORY_DB_PATH}"
db_engine = create_engine(_db_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


# ---------------------------------------------------------------------------
# Memory helpers (UMA-aware)
# ---------------------------------------------------------------------------

def read_meminfo() -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
    except OSError:
        pass
    return info


def get_memory_status() -> dict[str, Any]:
    mi = read_meminfo()
    total_kb = mi.get("MemTotal", 0)
    available_kb = mi.get("MemAvailable", 0)
    swap_total_kb = mi.get("SwapTotal", 0)
    swap_free_kb = mi.get("SwapFree", 0)
    used_kb = total_kb - available_kb
    used_pct = (used_kb / total_kb * 100) if total_kb else 0
    swap_used_kb = swap_total_kb - swap_free_kb
    return {
        "total_gb": round(total_kb / 1048576, 1),
        "available_gb": round(available_kb / 1048576, 1),
        "used_gb": round(used_kb / 1048576, 1),
        "used_pct": round(used_pct, 1),
        "swap_total_gb": round(swap_total_kb / 1048576, 1),
        "swap_free_gb": round(swap_free_kb / 1048576, 1),
        "swap_used_gb": round(swap_used_kb / 1048576, 1),
        "swap_used_pct": round(
            (swap_used_kb / swap_total_kb * 100) if swap_total_kb else 0, 1
        ),
        "thresholds": {
            "warn_pct": MEMORY_WARN_PCT,
            "hard_pct": MEMORY_HARD_PCT,
            "reserve_heavy_gb": MEM_RESERVE_HEAVY_GB,
            "reserve_medium_gb": MEM_RESERVE_MEDIUM_GB,
            "reserve_light_gb": MEM_RESERVE_LIGHT_GB,
        },
    }


def memory_allows_job(tier: str) -> tuple[bool, dict[str, Any], str]:
    """Check if UMA allows a new job for the given tier."""
    status = get_memory_status()
    avail_gb = status["available_gb"]

    if status["used_pct"] >= MEMORY_HARD_PCT:
        return False, status, f"UMA usage {status['used_pct']}% >= hard limit {MEMORY_HARD_PCT}%"

    reserve = {
        "heavy": MEM_RESERVE_HEAVY_GB,
        "medium": MEM_RESERVE_MEDIUM_GB,
        "light": MEM_RESERVE_LIGHT_GB,
    }.get(tier, MEM_RESERVE_MEDIUM_GB)

    if avail_gb < reserve:
        return False, status, (
            f"Available {avail_gb:.1f} GB < {tier} tier reservation {reserve:.1f} GB"
        )

    return True, status, "ok"


# ---------------------------------------------------------------------------
# Filesystem safety helpers
# ---------------------------------------------------------------------------

def _ensure_within_root(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise HTTPException(403, "Access denied")
    return resolved


def resolve_output_path(relative_or_abs_path: str) -> Path:
    candidate = Path(relative_or_abs_path)
    if candidate.is_absolute():
        return _ensure_within_root(candidate, OUTPUTS_ROOT)
    return _ensure_within_root(OUTPUTS_ROOT / candidate, OUTPUTS_ROOT)


def normalize_output_path(relative_or_abs_path: str) -> str:
    resolved = resolve_output_path(relative_or_abs_path)
    return str(resolved.relative_to(OUTPUTS_ROOT.resolve()))


def resolve_asset_path(input_path: str, asset_root: Path) -> Path:
    candidate = Path(input_path)
    if not candidate.is_absolute():
        candidate = asset_root / candidate
    resolved = _ensure_within_root(candidate, asset_root)
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(400, f"Asset file not found: {input_path}")
    return resolved


# ---------------------------------------------------------------------------
# History + Preset helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_json_loads(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _extract_prompt(service: str, payload: dict[str, Any]) -> Optional[str]:
    if service in {"f5tts"}:
        return payload.get("text")
    if service in {"wan21", "reactor"}:
        return payload.get("prompt")
    if service in {"liveportrait", "lipsync"}:
        return payload.get("prompt") or payload.get("description")
    return payload.get("prompt")


def _compact_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    compact = dict(payload)
    # Workflows can be huge; keep history metadata compact.
    if "workflow" in compact:
        compact["workflow"] = "<omitted>"
    if "prompt_graph" in compact:
        compact["prompt_graph"] = "<omitted>"
    return compact


def persist_generation_record(
    *,
    job_id: str,
    service: str,
    payload: dict[str, Any],
    output_path: str,
    model_used: Optional[str] = None,
):
    normalized_output = normalize_output_path(output_path)
    prompt = _extract_prompt(service, payload)
    params = _compact_parameters(payload)

    record = GenerationHistory(
        id=str(uuid.uuid4()),
        job_id=job_id,
        service=service,
        model_used=model_used or MODEL_NAME_BY_SERVICE.get(service, service),
        prompt=prompt,
        parameters_json=json.dumps(params, ensure_ascii=False),
        output_path=normalized_output,
        created_at=_now_utc(),
    )
    with SessionLocal() as db:
        db.add(record)
        db.commit()


def serialize_generation(record: GenerationHistory) -> dict[str, Any]:
    return {
        "id": record.id,
        "job_id": record.job_id,
        "service": record.service,
        "model_used": record.model_used,
        "prompt": record.prompt,
        "parameters": _safe_json_loads(record.parameters_json, {}),
        "timestamp": record.created_at.isoformat(),
        "output_path": record.output_path,
        "download_url": f"/api/v1/history/{record.id}/download",
        "preview_url": f"/api/v1/outputs/{record.output_path}",
    }


def serialize_preset(record: PresetProfile) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "tool": record.tool,
        "state": _safe_json_loads(record.state_json, {}),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def list_asset_files(asset_dir: Path, extensions: set[str]) -> list[dict[str, Any]]:
    if not asset_dir.exists():
        return []

    items: list[dict[str, Any]] = []
    for path in sorted(asset_dir.rglob("*")):
        if not path.is_file():
            continue
        if extensions and path.suffix.lower() not in extensions:
            continue
        resolved = _ensure_within_root(path, asset_dir)
        stat = resolved.stat()
        items.append(
            {
                "name": resolved.name,
                "path": str(resolved),
                "relative_path": str(resolved.relative_to(asset_dir.resolve())),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return items


# ---------------------------------------------------------------------------
# Job Queue
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobRecord(BaseModel):
    job_id: str
    service: str
    status: JobStatus
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result_path: Optional[str] = None
    error: Optional[str] = None


class PresetUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    tool: str = Field(min_length=1, max_length=64)
    state: dict[str, Any] = Field(default_factory=dict)


class ReactorGenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    lora_path: str | None = None
    lora_strength: float = 0.75
    workflow: dict[str, Any] | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


# Both tiers serialized to 1 concurrent GPU job
gpu_heavy_sem = asyncio.Semaphore(1)
gpu_medium_sem = asyncio.Semaphore(1)

rdb: Optional[aioredis.Redis] = None
http_client: Optional[httpx.AsyncClient] = None


async def store_job(job: JobRecord):
    if rdb:
        await rdb.set(f"job:{job.job_id}", job.model_dump_json(), ex=86400)


async def get_job(job_id: str) -> Optional[JobRecord]:
    if rdb:
        data = await rdb.get(f"job:{job_id}")
        if data:
            return JobRecord.model_validate_json(data)
    return None


async def record_service_activity(service: str):
    if rdb:
        await rdb.set(f"activity:{service}", str(time.time()), ex=7200)


# ---------------------------------------------------------------------------
# Supervisor delegation (NO Docker socket in this container)
# ---------------------------------------------------------------------------

async def ensure_service_running(service: str) -> bool:
    """Ask the supervisor sidecar to start a lazy service."""
    url = SERVICE_URLS.get(service)
    if not url:
        return False

    try:
        resp = await http_client.get(f"{url}/healthz", timeout=3.0)
        if resp.status_code == 200:
            return True
    except (httpx.ConnectError, httpx.TimeoutException):
        pass

    log.info("Requesting supervisor to start: %s", service)
    try:
        resp = await http_client.post(
            f"{SUPERVISOR_URL}/start/{service}",
            params={"wait_ready": "true"},
            timeout=httpx.Timeout(360.0, connect=10.0),
        )
        if resp.status_code == 200:
            data = resp.json()
            log.info("Supervisor response for %s: %s", service, data)
            return data.get("ready", False) or data.get("alive", False)

        log.error("Supervisor failed to start %s: HTTP %d", service, resp.status_code)
        return False
    except Exception as e:
        log.error("Supervisor communication failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global rdb, http_client

    OUTPUTS_ROOT.mkdir(parents=True, exist_ok=True)
    HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=db_engine)

    rdb = aioredis.from_url(REDIS_URL, decode_responses=True)
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))

    log.info(
        "Gateway v3 started (history_db=%s, hard_limit=%d%%, heavy_reserve=%.0fGB, "
        "medium_reserve=%.0fGB, gpu_concurrency=1/1)",
        HISTORY_DB_PATH,
        MEMORY_HARD_PCT,
        MEM_RESERVE_HEAVY_GB,
        MEM_RESERVE_MEDIUM_GB,
    )
    yield

    if http_client:
        await http_client.aclose()
    if rdb:
        await rdb.aclose()


app = FastAPI(title="DGX Spark AI Gateway", version="3.0.0", lifespan=lifespan)

# CORS — allow frontend to call gateway directly if not using the Next.js proxy
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz():
    return {"status": "alive", "timestamp": _now_utc().isoformat()}


@app.get("/readyz")
async def readyz():
    if not rdb:
        return {"status": "degraded", "redis": False}

    try:
        await rdb.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {"status": "ready" if redis_ok else "degraded", "redis": redis_ok}


@app.get("/memory")
async def memory():
    return get_memory_status()


@app.get("/services/status")
async def services_status():
    results = {}
    for name, url in SERVICE_URLS.items():
        try:
            resp = await http_client.get(f"{url}/healthz", timeout=3.0)
            alive = resp.status_code == 200
        except Exception:
            alive = False

        ready = False
        if alive:
            try:
                resp = await http_client.get(f"{url}/readyz", timeout=3.0)
                ready = resp.status_code == 200
            except Exception:
                pass

        last_activity = None
        if rdb:
            ts = await rdb.get(f"activity:{name}")
            if ts:
                last_activity = float(ts)

        results[name] = {"alive": alive, "ready": ready, "last_activity": last_activity}
    return results


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = await get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------

async def proxy_to_service(
    service: str,
    path: str,
    request: Request,
    files: dict | None = None,
    data: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    is_gpu_heavy: bool = False,
    is_gpu_medium: bool = False,
):
    job_id = str(uuid.uuid4())
    now = _now_utc().isoformat()
    job = JobRecord(job_id=job_id, service=service, status=JobStatus.QUEUED, created_at=now)
    await store_job(job)

    tier = "heavy" if is_gpu_heavy else "medium" if is_gpu_medium else "light"
    allowed, mem, reason = memory_allows_job(tier)
    if not allowed and tier != "light":
        job.status = JobStatus.FAILED
        job.error = reason
        await store_job(job)
        raise HTTPException(503, detail={"error": reason, "memory": mem, "job_id": job_id})

    if service in GPU_HEAVY_SERVICES:
        ready = await ensure_service_running(service)
        if not ready:
            job.status = JobStatus.FAILED
            job.error = "Service failed to start via supervisor"
            await store_job(job)
            raise HTTPException(503, f"Service {service} failed to start")

    sem = gpu_heavy_sem if is_gpu_heavy else gpu_medium_sem if is_gpu_medium else None
    url = f"{SERVICE_URLS[service]}{path}"

    payload_for_history: dict[str, Any] = {}
    if json_body is not None:
        payload_for_history = dict(json_body)
    elif data:
        payload_for_history = dict(data)

    if files:
        payload_for_history.update(
            {
                f"{field}_filename": file_tuple[0]
                for field, file_tuple in files.items()
                if isinstance(file_tuple, tuple) and len(file_tuple) >= 1
            }
        )

    async def do_request():
        nonlocal payload_for_history

        job.status = JobStatus.RUNNING
        job.started_at = _now_utc().isoformat()
        await store_job(job)
        await record_service_activity(service)

        try:
            if files:
                resp = await http_client.post(url, files=files, data=data or {})
            elif json_body is not None:
                resp = await http_client.post(url, json=json_body)
            else:
                body = await request.body()
                content_type = request.headers.get("content-type", "application/json")
                if not payload_for_history and "application/json" in content_type and body:
                    try:
                        payload_for_history = json.loads(body.decode("utf-8"))
                    except json.JSONDecodeError:
                        payload_for_history = {}
                resp = await http_client.post(
                    url,
                    content=body,
                    headers={"content-type": content_type},
                )

            resp.raise_for_status()
            result = resp.json()

            job.status = JobStatus.COMPLETED
            job.completed_at = _now_utc().isoformat()
            job.result_path = result.get("output_path")
            await store_job(job)

            if job.result_path:
                try:
                    persist_generation_record(
                        job_id=job_id,
                        service=service,
                        payload=payload_for_history,
                        output_path=job.result_path,
                    )
                except Exception as hist_error:
                    log.warning("History persistence failed for job %s: %s", job_id, hist_error)

            result["job_id"] = job_id
            return result
        except httpx.HTTPStatusError as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            await store_job(job)
            raise HTTPException(e.response.status_code, str(e))
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            await store_job(job)
            raise HTTPException(502, f"Backend error: {e}")

    if sem:
        async with sem:
            return await do_request()
    return await do_request()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/v1/whisper/transcribe")
async def whisper_transcribe(request: Request, audio: UploadFile = File(...)):
    content = await audio.read()
    files = {"audio": (audio.filename, content, audio.content_type)}
    return await proxy_to_service("whisper", "/transcribe", request, files=files)


@app.post("/api/v1/tts/synthesize")
async def tts_synthesize(request: Request):
    return await proxy_to_service("f5tts", "/synthesize", request, is_gpu_medium=True)


@app.post("/api/v1/tts/synthesize-with-audio")
async def tts_synthesize_with_audio(
    request: Request,
    text: str = Form(...),
    ref_audio: UploadFile = File(None),
    saved_voice_path: str = Form(""),
    ref_text: str = Form(""),
    speed: float = Form(1.0),
):
    """Send reference audio as raw bytes so the backend avoids path-based loading."""
    files = {}
    data = {"text": text, "speed": speed, "ref_text": ref_text}

    if ref_audio and ref_audio.filename and saved_voice_path:
        raise HTTPException(400, "Choose either uploaded ref_audio or saved_voice_path, not both")

    # CASE 1: Uploaded file - Read bytes directly
    if ref_audio and ref_audio.filename:
        content = await ref_audio.read()
        files["ref_audio"] = (ref_audio.filename, content, ref_audio.content_type)

    # CASE 2: Saved asset - Read from local assets and send as bytes
    elif saved_voice_path:
        asset_full_path = resolve_asset_path(saved_voice_path, VOICE_ASSETS_DIR)
        with open(asset_full_path, "rb") as f:
            guessed_type = mimetypes.guess_type(asset_full_path.name)[0] or "application/octet-stream"
            files["ref_audio"] = (asset_full_path.name, f.read(), guessed_type)

    return await proxy_to_service(
        "f5tts",
        "/synthesize",
        request,
        files=files if files else None,
        data=data,
        is_gpu_medium=True,
    )

@app.post("/api/v1/liveportrait/animate")
async def liveportrait_animate(
    request: Request,
    source_image: UploadFile = File(...),
    driving_video: UploadFile = File(...),
):
    src_data = await source_image.read()
    drv_data = await driving_video.read()
    files = {
        "source_image": (source_image.filename, src_data, source_image.content_type),
        "driving_video": (driving_video.filename, drv_data, driving_video.content_type),
    }
    return await proxy_to_service("liveportrait", "/animate", request, files=files, is_gpu_medium=True)


@app.post("/api/v1/lipsync/sync")
async def lipsync_sync(request: Request, video: UploadFile = File(...), audio: UploadFile = File(...)):
    vid_data = await video.read()
    aud_data = await audio.read()
    files = {
        "video": (video.filename, vid_data, video.content_type),
        "audio": (audio.filename, aud_data, audio.content_type),
    }
    return await proxy_to_service("lipsync", "/sync", request, files=files, is_gpu_medium=True)


@app.post("/api/v1/wan21/generate")
async def wan21_generate(request: Request):
    return await proxy_to_service("wan21", "/generate", request, is_gpu_heavy=True)


@app.post("/api/v1/reactor/generate")
async def reactor_generate(req: ReactorGenerateRequest):
    """
    Boilerplate ReActor endpoint for ComfyUI integration.

    This queues a prompt to ComfyUI's `/prompt` API while carrying the selected
    LoRA path in payload metadata so custom workflow nodes can consume it.
    """
    if not req.prompt.strip():
        raise HTTPException(400, "prompt is required")

    lora_path = None
    if req.lora_path:
        lora_path = str(resolve_asset_path(req.lora_path, LORA_ASSETS_DIR))

    payload_snapshot = {
        "prompt": req.prompt,
        "negative_prompt": req.negative_prompt,
        "lora_path": lora_path,
        "lora_strength": req.lora_strength,
        **req.parameters,
    }

    comfy_payload = {
        "client_id": str(uuid.uuid4()),
        "extra_data": {
            "neonforge": payload_snapshot,
        },
        "prompt": req.workflow
        or {
            "neonforge_reactor": {
                "class_type": "NeonForgeReActorInput",
                "inputs": payload_snapshot,
            }
        },
    }

    job_id = str(uuid.uuid4())
    job = JobRecord(
        job_id=job_id,
        service="reactor",
        status=JobStatus.QUEUED,
        created_at=_now_utc().isoformat(),
    )
    await store_job(job)

    allowed, mem, reason = memory_allows_job("medium")
    if not allowed:
        job.status = JobStatus.FAILED
        job.error = reason
        await store_job(job)
        raise HTTPException(503, detail={"error": reason, "memory": mem, "job_id": job_id})

    async with gpu_medium_sem:
        job.status = JobStatus.RUNNING
        job.started_at = _now_utc().isoformat()
        await store_job(job)
        await record_service_activity("reactor")

        try:
            resp = await http_client.post(
                f"{COMFYUI_URL.rstrip('/')}/prompt",
                json=comfy_payload,
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
            resp.raise_for_status()
            data = resp.json()

            output_path = data.get("output_path") if isinstance(data, dict) else None
            job.status = JobStatus.COMPLETED
            job.completed_at = _now_utc().isoformat()
            job.result_path = output_path
            await store_job(job)

            if output_path:
                try:
                    persist_generation_record(
                        job_id=job_id,
                        service="reactor",
                        payload=payload_snapshot,
                        output_path=output_path,
                        model_used=MODEL_NAME_BY_SERVICE["reactor"],
                    )
                except Exception as hist_error:
                    log.warning("History persistence failed for reactor job %s: %s", job_id, hist_error)

            return {
                "job_id": job_id,
                "output_path": output_path,
                "queue_response": data,
            }
        except httpx.HTTPStatusError as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            await store_job(job)
            raise HTTPException(e.response.status_code, str(e))
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            await store_job(job)
            raise HTTPException(502, f"ComfyUI proxy error: {e}")


@app.get("/api/v1/outputs/{filepath:path}")
async def get_output(filepath: str):
    full = resolve_output_path(filepath)
    if not full.exists() or not full.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(full)


# ---------------------------------------------------------------------------
# History APIs
# ---------------------------------------------------------------------------

@app.get("/api/v1/history")
async def list_history(limit: int = 200, service: Optional[str] = None):
    clamped_limit = max(1, min(limit, 1000))
    with SessionLocal() as db:
        stmt = select(GenerationHistory)
        if service:
            stmt = stmt.where(GenerationHistory.service == service)
        stmt = stmt.order_by(desc(GenerationHistory.created_at)).limit(clamped_limit)
        rows = db.execute(stmt).scalars().all()

    return {"items": [serialize_generation(row) for row in rows]}


@app.get("/api/v1/history/{history_id}/download")
async def download_history_file(history_id: str):
    with SessionLocal() as db:
        row = db.get(GenerationHistory, history_id)

    if not row:
        raise HTTPException(404, "History item not found")

    full = resolve_output_path(row.output_path)
    if not full.exists() or not full.is_file():
        raise HTTPException(404, "Output file is missing")

    return FileResponse(full, filename=full.name, media_type="application/octet-stream")


@app.delete("/api/v1/history/{history_id}")
async def delete_history_item(history_id: str):
    with SessionLocal() as db:
        row = db.get(GenerationHistory, history_id)
        if not row:
            raise HTTPException(404, "History item not found")

        file_deleted = False
        try:
            full = resolve_output_path(row.output_path)
            if full.exists() and full.is_file():
                full.unlink()
                file_deleted = True
        except HTTPException:
            # Path is invalid/outside root; still delete DB record.
            pass

        db.delete(row)
        db.commit()

    return {"deleted": True, "file_deleted": file_deleted}


# ---------------------------------------------------------------------------
# Asset APIs
# ---------------------------------------------------------------------------

@app.get("/api/v1/assets/voices")
async def list_voice_assets():
    return {
        "root": str(VOICE_ASSETS_DIR),
        "items": list_asset_files(VOICE_ASSETS_DIR, VOICE_EXTENSIONS),
    }


@app.get("/api/v1/assets/loras")
async def list_lora_assets():
    return {
        "root": str(LORA_ASSETS_DIR),
        "items": list_asset_files(LORA_ASSETS_DIR, LORA_EXTENSIONS),
    }


# ---------------------------------------------------------------------------
# Preset APIs
# ---------------------------------------------------------------------------

@app.get("/api/v1/presets")
async def list_presets(tool: Optional[str] = None, limit: int = 200):
    clamped_limit = max(1, min(limit, 1000))

    with SessionLocal() as db:
        stmt = select(PresetProfile)
        if tool:
            stmt = stmt.where(PresetProfile.tool == tool)
        stmt = stmt.order_by(desc(PresetProfile.updated_at)).limit(clamped_limit)
        rows = db.execute(stmt).scalars().all()

    return {"items": [serialize_preset(row) for row in rows]}


@app.get("/api/v1/presets/{preset_id}")
async def get_preset(preset_id: str):
    with SessionLocal() as db:
        row = db.get(PresetProfile, preset_id)

    if not row:
        raise HTTPException(404, "Preset not found")

    return serialize_preset(row)


@app.post("/api/v1/presets")
async def upsert_preset(payload: PresetUpsertRequest):
    now = _now_utc()

    with SessionLocal() as db:
        stmt = select(PresetProfile).where(
            PresetProfile.name == payload.name,
            PresetProfile.tool == payload.tool,
        )
        existing = db.execute(stmt).scalar_one_or_none()

        if existing:
            existing.state_json = json.dumps(payload.state, ensure_ascii=False)
            existing.updated_at = now
            db.commit()
            db.refresh(existing)
            return serialize_preset(existing)

        record = PresetProfile(
            id=str(uuid.uuid4()),
            name=payload.name,
            tool=payload.tool,
            state_json=json.dumps(payload.state, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return serialize_preset(record)


@app.delete("/api/v1/presets/{preset_id}")
async def delete_preset(preset_id: str):
    with SessionLocal() as db:
        row = db.get(PresetProfile, preset_id)
        if not row:
            raise HTTPException(404, "Preset not found")

        db.delete(row)
        db.commit()

    return {"deleted": True}
