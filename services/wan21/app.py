"""
Wan 2.1 video generation service.

Tier: lazy-start / singleton
GPU weight: HEAVY (8-40+ GB depending on model variant)

This service is started on demand by the supervisor sidecar and stopped
after idle timeout. Only one generation runs at a time (enforced by the
gateway semaphore + internal lock).

Model lifecycle:
  - Container starts with NO model loaded.
  - First /generate request triggers load_model().
  - After WAN21_IDLE_TIMEOUT of inactivity, destroy_model() fully
    releases the model: sub-modules moved to CPU, Python object graph
    deleted, gc.collect(), CUDA cache cleared, memory verified via
    /proc/meminfo.
  - The idle_manager.py (host-side) may also stop the entire container.

Model variants:
  - 1.3B: ~3 GB weights, ~8-15 GB total during generation
  - 14B:  ~28 GB weights, ~40-80 GB total during generation
  Default: 1.3B (safe for 128 GB UMA with other services)
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
MODEL_VARIANT = os.getenv("WAN21_MODEL_VARIANT", "1.3B")
MAX_VIDEO_LENGTH = int(os.getenv("WAN21_MAX_VIDEO_LENGTH", "10"))
MAX_RESOLUTION = int(os.getenv("WAN21_MAX_RESOLUTION", "512"))
IDLE_TIMEOUT = int(os.getenv("WAN21_IDLE_TIMEOUT", "300"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/outputs/wan21"))
MODEL_DIR = Path(os.getenv("MODEL_DIR", "/models/wan21"))

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("wan21")

_pipe = None
_model_loaded = False
_last_activity = time.time()
_generation_lock = asyncio.Lock()
_load_lock = asyncio.Lock()


def _mem_used_gb() -> float:
    """Read current memory usage from /proc/meminfo (UMA-aware)."""
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
        return -1


def load_model():
    global _pipe, _model_loaded
    mem_before = _mem_used_gb()
    log.info(
        "Loading Wan 2.1 %s (UMA used before: %.1f GB)...",
        MODEL_VARIANT, mem_before,
    )
    t0 = time.time()

    try:
        from diffusers import DiffusionPipeline

        model_map = {
            "1.3B": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
            "14B": "Wan-AI/Wan2.1-T2V-14B-Diffusers",
        }
        model_id = model_map.get(MODEL_VARIANT)
        if not model_id:
            raise ValueError(f"Unknown Wan2.1 variant: {MODEL_VARIANT}")

        local_path = MODEL_DIR / MODEL_VARIANT
        if local_path.exists():
            log.info("Loading from local path: %s", local_path)
            source = str(local_path)
        else:
            log.info("Loading from HuggingFace: %s", model_id)
            source = model_id

        _pipe = DiffusionPipeline.from_pretrained(
            source,
            torch_dtype=torch.float16,
        )
        _pipe.to("cuda")

        _pipe.enable_attention_slicing()
        try:
            _pipe.enable_vae_slicing()
        except AttributeError:
            pass

        elapsed = time.time() - t0
        mem_after = _mem_used_gb()
        log.info(
            "Wan 2.1 %s loaded in %.1fs (UMA: %.1f -> %.1f GB, delta +%.1f GB)",
            MODEL_VARIANT, elapsed, mem_before, mem_after, mem_after - mem_before,
        )
        _model_loaded = True

    except Exception as e:
        log.error("Failed to load Wan 2.1: %s", e)
        raise


def destroy_model():
    """Truly destroy the pipeline: CPU offload, delete, gc, CUDA clear, verify."""
    global _pipe, _model_loaded
    if _pipe is None:
        return

    mem_before = _mem_used_gb()
    log.info("Destroying Wan 2.1 %s (UMA used: %.1f GB)...", MODEL_VARIANT, mem_before)

    # 1. Move all sub-modules to CPU to release CUDA allocator pages
    try:
        for name, module in _pipe.components.items():
            if isinstance(module, torch.nn.Module):
                module.cpu()
                log.debug("Moved %s to CPU", name)
    except Exception as e:
        log.debug("CPU offload (non-fatal): %s", e)

    # 2. Delete the Python pipeline object
    del _pipe
    _pipe = None
    _model_loaded = False

    # 3. Force two gc passes (first frees, second cleans weak-refs)
    gc.collect()
    gc.collect()

    # 4. Return CUDA allocator cache to OS
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()

    # 5. Verify
    mem_after = _mem_used_gb()
    freed = mem_before - mem_after
    log.info(
        "Wan 2.1 destroyed (UMA: %.1f -> %.1f GB, freed ~%.1f GB)",
        mem_before, mem_after, freed,
    )
    if freed < 1.0:
        log.warning(
            "Less than 1 GB freed after model destruction — "
            "possible tensor leak or external reference."
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
        await asyncio.sleep(30)
        if _model_loaded and (time.time() - _last_activity) > IDLE_TIMEOUT:
            log.info("Wan 2.1 idle timeout (%ds), destroying model", IDLE_TIMEOUT)
            destroy_model()


@asynccontextmanager
async def lifespan(app: FastAPI):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(_idle_watcher())
    yield
    task.cancel()
    destroy_model()


app = FastAPI(title="Wan 2.1 Video Generation", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "alive", "variant": MODEL_VARIANT}


@app.get("/readyz")
async def readyz():
    return {
        "status": "ready" if _model_loaded else "idle",
        "variant": MODEL_VARIANT,
        "model_loaded": _model_loaded,
        "uma_used_gb": _mem_used_gb(),
    }


@app.get("/smoke")
async def smoke():
    """Load model as smoke test. Does NOT run video generation."""
    await _ensure_model()
    return {
        "status": "ok",
        "variant": MODEL_VARIANT,
        "uma_used_gb": _mem_used_gb(),
    }


class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    num_frames: int = 16
    width: int = 512
    height: int = 512
    num_inference_steps: int = 25
    guidance_scale: float = 7.5
    seed: int = -1


@app.post("/generate")
async def generate(req: GenerateRequest):
    global _last_activity
    _last_activity = time.time()

    if req.width > MAX_RESOLUTION or req.height > MAX_RESOLUTION:
        raise HTTPException(
            400,
            f"Resolution capped at {MAX_RESOLUTION}x{MAX_RESOLUTION}",
        )

    fps = 8
    max_frames = MAX_VIDEO_LENGTH * fps
    if req.num_frames > max_frames:
        raise HTTPException(
            400,
            f"Max frames: {max_frames} ({MAX_VIDEO_LENGTH}s at {fps}fps)",
        )

    await _ensure_model()

    out_id = str(uuid.uuid4())
    out_path = OUTPUT_DIR / f"{out_id}.mp4"

    async with _generation_lock:
        t0 = time.time()
        mem_before = _mem_used_gb()
        log.info(
            "Starting generation: %dx%d, %d frames, %d steps (UMA: %.1f GB)",
            req.width, req.height, req.num_frames,
            req.num_inference_steps, mem_before,
        )

        try:
            generator = None
            if req.seed >= 0:
                generator = torch.Generator(device="cuda").manual_seed(req.seed)

            import functools
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                functools.partial(
                    _pipe,
                    prompt=req.prompt,
                    negative_prompt=req.negative_prompt or None,
                    num_frames=req.num_frames,
                    width=req.width,
                    height=req.height,
                    num_inference_steps=req.num_inference_steps,
                    guidance_scale=req.guidance_scale,
                    generator=generator,
                ),
            )

            from diffusers.utils import export_to_video
            export_to_video(result.frames[0], str(out_path), fps=fps)

            elapsed = time.time() - t0
            mem_after = _mem_used_gb()
            log.info(
                "Generation complete in %.1fs (UMA: %.1f -> %.1f GB) -> %s",
                elapsed, mem_before, mem_after, out_path.name,
            )

            return {
                "output_path": f"wan21/{out_id}.mp4",
                "processing_time": round(elapsed, 2),
                "num_frames": req.num_frames,
                "resolution": f"{req.width}x{req.height}",
                "uma_used_gb": mem_after,
            }

        except torch.cuda.OutOfMemoryError:
            gc.collect()
            torch.cuda.empty_cache()
            raise HTTPException(
                503,
                "CUDA out of memory (UMA exhausted). "
                "Try smaller resolution or fewer frames.",
            )
        except Exception as e:
            log.error("Generation failed: %s", e)
            raise HTTPException(500, f"Generation failed: {e}")
