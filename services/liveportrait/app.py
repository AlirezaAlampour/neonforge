"""
LivePortrait face animation service.

Tier: warm (model loaded on first request, fully destroyed after idle timeout)
GPU weight: medium (~2-4 GB)

Takes a source face image + driving video, produces an animated output.
Model is truly destroyed (not just cache-flushed) after idle timeout.
"""

import asyncio
import gc
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import torch
from fastapi import FastAPI, HTTPException, UploadFile, File

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
IDLE_TIMEOUT = int(os.getenv("LIVEPORTRAIT_IDLE_TIMEOUT", "1800"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/outputs/liveportrait"))
MODEL_DIR = Path(os.getenv("MODEL_DIR", "/models/liveportrait"))

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("liveportrait")

_pipeline = None
_model_loaded = False
_last_activity = time.time()
_load_lock = asyncio.Lock()


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
    global _pipeline, _model_loaded
    mem_before = _mem_used_gb()
    log.info("Loading LivePortrait from %s (UMA used: %.1f GB)...", MODEL_DIR, mem_before)
    t0 = time.time()
    try:
        from liveportrait.api import LivePortraitPipeline
        _pipeline = LivePortraitPipeline(
            model_dir=str(MODEL_DIR),
            device="cuda",
        )
        _model_loaded = True
        mem_after = _mem_used_gb()
        log.info(
            "LivePortrait loaded in %.1fs (UMA: %.1f -> %.1f GB, delta +%.1f GB)",
            time.time() - t0, mem_before, mem_after, mem_after - mem_before,
        )
    except ImportError:
        log.warning(
            "LivePortrait package not found via pip. "
            "Ensure it is cloned into /app/liveportrait or installed."
        )
        raise


def destroy_model():
    """Truly destroy the model: delete object graph, gc, clear CUDA, verify."""
    global _pipeline, _model_loaded
    if _pipeline is None:
        return

    mem_before = _mem_used_gb()
    log.info("Destroying LivePortrait model (UMA used: %.1f GB)...", mem_before)

    # Move sub-models to CPU before deletion to release CUDA allocator pages
    try:
        for attr_name in dir(_pipeline):
            attr = getattr(_pipeline, attr_name, None)
            if isinstance(attr, torch.nn.Module):
                attr.cpu()
    except Exception as e:
        log.debug("CPU offload before delete (non-fatal): %s", e)

    del _pipeline
    _pipeline = None
    _model_loaded = False

    gc.collect()
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()

    mem_after = _mem_used_gb()
    log.info(
        "LivePortrait destroyed (UMA: %.1f -> %.1f GB, freed ~%.1f GB)",
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
            log.info("Idle timeout (%ds), destroying LivePortrait", IDLE_TIMEOUT)
            destroy_model()


@asynccontextmanager
async def lifespan(app: FastAPI):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(_idle_watcher())
    yield
    task.cancel()
    destroy_model()


app = FastAPI(title="LivePortrait Service", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "alive"}


@app.get("/readyz")
async def readyz():
    return {
        "status": "ready" if _model_loaded else "idle",
        "model_loaded": _model_loaded,
        "uma_used_gb": _mem_used_gb(),
    }


@app.get("/smoke")
async def smoke():
    await _ensure_model()
    return {"status": "ok", "model_loaded": _model_loaded, "uma_used_gb": _mem_used_gb()}


@app.post("/animate")
async def animate(
    source_image: UploadFile = File(...),
    driving_video: UploadFile = File(...),
):
    global _last_activity
    _last_activity = time.time()

    await _ensure_model()

    out_id = str(uuid.uuid4())
    src_path = OUTPUT_DIR / f"{out_id}_src.png"
    drv_path = OUTPUT_DIR / f"{out_id}_drv.mp4"
    out_path = OUTPUT_DIR / f"{out_id}_out.mp4"

    try:
        src_data = await source_image.read()
        drv_data = await driving_video.read()
        src_path.write_bytes(src_data)
        drv_path.write_bytes(drv_data)

        t0 = time.time()
        _pipeline.run(
            source_image=str(src_path),
            driving_video=str(drv_path),
            output_path=str(out_path),
        )
        elapsed = time.time() - t0
        log.info("Animation completed in %.1fs -> %s", elapsed, out_path.name)

        return {
            "output_path": f"liveportrait/{out_id}_out.mp4",
            "processing_time": round(elapsed, 2),
        }
    except Exception as e:
        log.error("Animation failed: %s", e)
        raise HTTPException(500, f"Animation failed: {e}")
    finally:
        src_path.unlink(missing_ok=True)
        drv_path.unlink(missing_ok=True)
