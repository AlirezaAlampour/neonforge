"""
F5-TTS text-to-speech service.

Tier: warm (model loaded on first request, fully destroyed after idle timeout)
GPU weight: medium (~2-3 GB)

Model lifecycle:
  - Container starts with NO model loaded (healthz=alive, readyz=idle)
  - First request triggers load_model()
  - After IDLE_TIMEOUT seconds of inactivity, destroy_model() is called
  - destroy_model() deletes the Python object graph, runs gc.collect(),
    clears the CUDA allocator cache, and verifies memory was freed via
    /proc/meminfo — NOT just torch.cuda.empty_cache().
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
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
IDLE_TIMEOUT = int(os.getenv("F5TTS_IDLE_TIMEOUT", "1800"))
SAMPLE_RATE = int(os.getenv("F5TTS_SAMPLE_RATE", "24000"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/outputs/tts"))

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("f5tts")

# Mutable state — protected by the GIL for flag reads, reload-on-demand
_model = None
_model_loaded = False
_last_activity = time.time()
_load_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# UMA memory helpers
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Model lifecycle — true load / true destroy
# ---------------------------------------------------------------------------
def load_model():
    global _model, _model_loaded
    mem_before = _mem_used_gb()
    log.info("Loading F5-TTS model (UMA used before: %.1f GB)...", mem_before)
    t0 = time.time()
    try:
        from f5_tts.api import F5TTS as F5TTSModel
        _model = F5TTSModel(device="cuda")
        _model_loaded = True
        mem_after = _mem_used_gb()
        log.info(
            "F5-TTS loaded in %.1fs (UMA: %.1f -> %.1f GB, delta +%.1f GB)",
            time.time() - t0, mem_before, mem_after, mem_after - mem_before,
        )
    except ImportError:
        log.error(
            "f5-tts package not installed or import failed. "
            "This may be an ARM64 compatibility issue."
        )
        raise


def destroy_model():
    """Truly destroy the model: delete object graph, gc, clear CUDA, verify."""
    global _model, _model_loaded
    if _model is None:
        return

    mem_before = _mem_used_gb()
    log.info("Destroying F5-TTS model (UMA used before: %.1f GB)...", mem_before)

    # 1. Move model off CUDA and delete the Python object
    try:
        # Some models hold internal CUDA tensors in submodules.
        # Explicitly move to CPU first to release CUDA allocator pages.
        if hasattr(_model, 'model') and hasattr(_model.model, 'cpu'):
            _model.model.cpu()
        if hasattr(_model, 'vocoder') and hasattr(_model.vocoder, 'cpu'):
            _model.vocoder.cpu()
    except Exception as e:
        log.debug("CPU offload before delete (non-fatal): %s", e)

    del _model
    _model = None
    _model_loaded = False

    # 2. Force Python garbage collection to break reference cycles
    gc.collect()
    gc.collect()  # second pass catches weak-ref pointers cleaned by first

    # 3. Release CUDA allocator cached blocks back to the OS
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()

    # 4. Verify
    mem_after = _mem_used_gb()
    log.info(
        "F5-TTS model destroyed (UMA: %.1f -> %.1f GB, freed ~%.1f GB)",
        mem_before, mem_after, mem_before - mem_after,
    )


async def _ensure_model():
    """Load model if not already loaded (async-safe via lock)."""
    global _last_activity
    _last_activity = time.time()
    if _model_loaded:
        return
    async with _load_lock:
        if _model_loaded:
            return  # another coroutine loaded while we waited
        load_model()


# ---------------------------------------------------------------------------
# Idle watcher — destroys model after timeout
# ---------------------------------------------------------------------------
async def _idle_watcher():
    while True:
        await asyncio.sleep(60)
        if _model_loaded and (time.time() - _last_activity) > IDLE_TIMEOUT:
            log.info("Idle timeout (%ds), destroying F5-TTS model", IDLE_TIMEOUT)
            destroy_model()


@asynccontextmanager
async def lifespan(app: FastAPI):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(_idle_watcher())
    yield
    task.cancel()
    destroy_model()


app = FastAPI(title="F5-TTS Service", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
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
    """Trigger model load as a smoke test."""
    await _ensure_model()
    return {"status": "ok", "model_loaded": _model_loaded, "uma_used_gb": _mem_used_gb()}


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------
class SynthesizeRequest(BaseModel):
    text: str
    ref_audio_path: str | None = None
    ref_text: str | None = None
    speed: float = 1.0


@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    global _last_activity
    _last_activity = time.time()

    await _ensure_model()

    if not req.text or len(req.text.strip()) == 0:
        raise HTTPException(400, "Empty text")

    t0 = time.time()
    out_id = str(uuid.uuid4())
    out_path = OUTPUT_DIR / f"{out_id}.wav"

    try:
        wav, sr, _ = _model.infer(
            ref_file=req.ref_audio_path or "",
            ref_text=req.ref_text or "",
            gen_text=req.text,
            speed=req.speed,
        )
        import soundfile as sf
        sf.write(str(out_path), wav, sr)
        elapsed = time.time() - t0
        log.info("Synthesized %d chars in %.1fs -> %s", len(req.text), elapsed, out_path.name)

        return {
            "output_path": f"tts/{out_id}.wav",
            "sample_rate": sr,
            "duration": round(len(wav) / sr, 2),
            "processing_time": round(elapsed, 2),
        }
    except Exception as e:
        log.error("Synthesis failed: %s", e)
        raise HTTPException(500, f"Synthesis failed: {e}")
