"""
Lip-sync service.

Default backend: video-retalking
Tier: warm
GPU weight: medium (~3-5 GB, multiple sub-models)

WARNING: Wav2Lip-HQ is unmaintained and incompatible with modern
CUDA / ARM64. This service uses video-retalking by default.
video-retalking is also research-grade software -- monitor closely.

See COMPATIBILITY_NOTES.md for a comparison of video-retalking vs
MuseTalk vs SadTalker.

Alternative backends (configurable via LIPSYNC_BACKEND env):
  - video-retalking (default) — proven quality, SIGGRAPH Asia 2022
  - sadtalker (experimental)

Model lifecycle:
  - Container starts with NO models loaded
  - First request triggers load_model() which pre-checks model files
  - After idle timeout, destroy_model() releases all GPU memory
  - Inference runs as an isolated subprocess for crash safety
"""

import asyncio
import gc
import logging
import os
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import torch
from fastapi import FastAPI, HTTPException, UploadFile, File

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
IDLE_TIMEOUT = int(os.getenv("LIPSYNC_IDLE_TIMEOUT", "1800"))
BACKEND = os.getenv("LIPSYNC_BACKEND", "video-retalking")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/outputs/lipsync"))
MODEL_DIR = Path(os.getenv("MODEL_DIR", "/models/lipsync"))

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("lipsync")

_model_loaded = False
_last_activity = time.time()
_load_lock = asyncio.Lock()

# For backends that load in-process models (SadTalker), track object refs here.
_pipeline = None


def _mem_used_gb() -> float:
    try:
        with open("/proc/meminfo") as f:
            mi = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mi[parts[0].rstrip(":")] = int(parts[1])
            total = mi.get("MemTotal", 0)
            avail = mi.get("MemAvailable", 0)
            return round((total - avail) / 1048576, 1)
    except OSError:
        return -1.0


def load_model():
    """Load / validate the lip-sync pipeline based on configured backend."""
    global _model_loaded, _pipeline
    mem_before = _mem_used_gb()
    log.info("Loading lip-sync backend: %s (UMA used: %.1f GB)", BACKEND, mem_before)
    t0 = time.time()

    if BACKEND == "video-retalking":
        # video-retalking runs inference as a subprocess, so "loading"
        # means verifying checkpoints are present. The actual GPU memory
        # is allocated/freed per invocation by the subprocess.
        required = ["checkpoints"]
        missing = []
        for d in required:
            p = MODEL_DIR / "video-retalking" / d
            if not p.exists():
                missing.append(str(p))
        if missing:
            log.warning("Missing model directories: %s", missing)

        _model_loaded = True
        log.info("video-retalking backend ready in %.1fs", time.time() - t0)

    elif BACKEND == "sadtalker":
        _model_loaded = True
        log.info("SadTalker backend ready in %.1fs", time.time() - t0)

    else:
        raise ValueError(f"Unknown lip-sync backend: {BACKEND}")

    mem_after = _mem_used_gb()
    log.info("Lip-sync load complete (UMA: %.1f -> %.1f GB)", mem_before, mem_after)


def destroy_model():
    """Fully destroy all model state and release GPU memory."""
    global _model_loaded, _pipeline
    if not _model_loaded:
        return

    mem_before = _mem_used_gb()
    log.info("Destroying lip-sync models (UMA used: %.1f GB)...", mem_before)

    # Destroy in-process pipeline if any
    if _pipeline is not None:
        try:
            if isinstance(_pipeline, torch.nn.Module):
                _pipeline.cpu()
        except Exception:
            pass
        del _pipeline
        _pipeline = None

    _model_loaded = False

    gc.collect()
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()

    mem_after = _mem_used_gb()
    log.info(
        "Lip-sync models destroyed (UMA: %.1f -> %.1f GB, freed ~%.1f GB)",
        mem_before, mem_after, mem_before - mem_after,
    )


async def _ensure_model():
    global _last_activity
    _last_activity = time.time()
    if _model_loaded:
        return
    async with _load_lock:
        if _model_loaded:
            return
        load_model()


async def _idle_watcher():
    while True:
        await asyncio.sleep(60)
        if _model_loaded and (time.time() - _last_activity) > IDLE_TIMEOUT:
            log.info("Idle timeout (%ds), destroying lip-sync models", IDLE_TIMEOUT)
            destroy_model()


@asynccontextmanager
async def lifespan(app: FastAPI):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(_idle_watcher())
    yield
    task.cancel()
    destroy_model()


app = FastAPI(title="Lip-sync Service", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "alive", "backend": BACKEND}


@app.get("/readyz")
async def readyz():
    return {
        "status": "ready" if _model_loaded else "idle",
        "backend": BACKEND,
        "model_loaded": _model_loaded,
        "uma_used_gb": _mem_used_gb(),
    }


@app.get("/smoke")
async def smoke():
    await _ensure_model()
    return {"status": "ok", "backend": BACKEND, "uma_used_gb": _mem_used_gb()}


@app.post("/sync")
async def sync(
    video: UploadFile = File(...),
    audio: UploadFile = File(...),
):
    global _last_activity
    _last_activity = time.time()

    await _ensure_model()

    out_id = str(uuid.uuid4())
    vid_path = OUTPUT_DIR / f"{out_id}_input.mp4"
    aud_path = OUTPUT_DIR / f"{out_id}_audio.wav"
    out_path = OUTPUT_DIR / f"{out_id}_synced.mp4"

    try:
        vid_data = await video.read()
        aud_data = await audio.read()
        vid_path.write_bytes(vid_data)
        aud_path.write_bytes(aud_data)

        t0 = time.time()

        if BACKEND == "video-retalking":
            cmd = [
                "python", "/app/video-retalking/inference.py",
                "--face", str(vid_path),
                "--audio", str(aud_path),
                "--outfile", str(out_path),
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                log.error("video-retalking failed: %s", result.stderr[-500:])
                raise RuntimeError(f"video-retalking exit code {result.returncode}")

        elif BACKEND == "sadtalker":
            cmd = [
                "python", "/app/sadtalker/inference.py",
                "--driven_audio", str(aud_path),
                "--source_image", str(vid_path),
                "--result_dir", str(OUTPUT_DIR),
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                raise RuntimeError(f"SadTalker exit code {result.returncode}")

        elapsed = time.time() - t0
        log.info("Lip-sync completed in %.1fs -> %s", elapsed, out_path.name)

        return {
            "output_path": f"lipsync/{out_id}_synced.mp4",
            "processing_time": round(elapsed, 2),
            "backend": BACKEND,
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Lip-sync timed out (600s)")
    except Exception as e:
        log.error("Lip-sync failed: %s", e)
        raise HTTPException(500, f"Lip-sync failed: {e}")
    finally:
        vid_path.unlink(missing_ok=True)
        aud_path.unlink(missing_ok=True)
