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
"""

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
SUPERVISOR_URL = os.getenv("SUPERVISOR_URL", "http://supervisor:8000")

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

GPU_HEAVY_SERVICES = {"wan21"}
GPU_MEDIUM_SERVICES = {"liveportrait", "lipsync", "f5tts"}
GPU_LIGHT_SERVICES = {"whisper"}

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("gateway")

# ---------------------------------------------------------------------------
# Memory helpers (UMA-aware)
# ---------------------------------------------------------------------------

def read_meminfo() -> dict:
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
    except OSError:
        pass
    return info


def get_memory_status() -> dict:
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


def memory_allows_job(tier: str) -> tuple[bool, dict, str]:
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
        else:
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
    rdb = aioredis.from_url(REDIS_URL, decode_responses=True)
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
    log.info(
        "Gateway v2 started (hard_limit=%d%%, heavy_reserve=%.0fGB, "
        "medium_reserve=%.0fGB, gpu_concurrency=1/1)",
        MEMORY_HARD_PCT, MEM_RESERVE_HEAVY_GB, MEM_RESERVE_MEDIUM_GB,
    )
    yield
    await http_client.aclose()
    await rdb.aclose()


app = FastAPI(title="DGX Spark AI Gateway", version="2.0.0", lifespan=lifespan)

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
    return {"status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/readyz")
async def readyz():
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
    files: dict = None,
    data: dict = None,
    json_body: dict = None,
    is_gpu_heavy: bool = False,
    is_gpu_medium: bool = False,
):
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
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

    async def do_request():
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc).isoformat()
        await store_job(job)
        await record_service_activity(service)
        try:
            if files:
                resp = await http_client.post(url, files=files, data=data or {})
            elif json_body is not None:
                resp = await http_client.post(url, json=json_body)
            else:
                body = await request.body()
                resp = await http_client.post(
                    url, content=body,
                    headers={"content-type": request.headers.get("content-type", "application/json")},
                )
            resp.raise_for_status()
            result = resp.json()
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc).isoformat()
            job.result_path = result.get("output_path")
            await store_job(job)
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
    else:
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
    ref_text: str = Form(""),
    speed: float = Form(1.0),
):
    """TTS with optional file upload for reference audio (used by the web UI)."""
    ref_audio_path = None
    if ref_audio and ref_audio.filename:
        upload_dir = Path("/outputs/uploads")
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4()}_{ref_audio.filename}"
        filepath = upload_dir / filename
        content = await ref_audio.read()
        filepath.write_bytes(content)
        ref_audio_path = str(filepath)

    payload = {"text": text, "speed": speed}
    if ref_audio_path:
        payload["ref_audio_path"] = ref_audio_path
    if ref_text:
        payload["ref_text"] = ref_text

    return await proxy_to_service(
        "f5tts", "/synthesize", request,
        json_body=payload, is_gpu_medium=True,
    )


@app.post("/api/v1/liveportrait/animate")
async def liveportrait_animate(
    request: Request, source_image: UploadFile = File(...), driving_video: UploadFile = File(...),
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


@app.get("/api/v1/outputs/{filepath:path}")
async def get_output(filepath: str):
    full = Path("/outputs") / filepath
    if not full.exists():
        raise HTTPException(404, "File not found")
    if not full.resolve().is_relative_to(Path("/outputs").resolve()):
        raise HTTPException(403, "Access denied")
    return FileResponse(full)
