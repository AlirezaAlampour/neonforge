from __future__ import annotations

import io
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import soundfile as sf
import torch
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
MODEL_PATH = Path(os.getenv("VOXCPM2_MODEL_PATH", "/models/voxcpm2/openbmb/VoxCPM2"))
CFG_VALUE = 2.0
INFERENCE_TIMESTEPS = 10
VOX_MODE_DESIGN = "design"
VOX_MODE_CLONE = "clone"
VOX_MODE_CONTINUATION = "continuation"
VALID_VOX_MODES = {VOX_MODE_DESIGN, VOX_MODE_CLONE, VOX_MODE_CONTINUATION}

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("voxcpm2")

model = None
model_loaded = False
load_error: str | None = None


def _mem_used_gb() -> float:
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            meminfo = {}
            for line in handle:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)
        return round((total - available) / 1048576, 1)
    except OSError:
        return -1.0


def load_model() -> None:
    global model, model_loaded, load_error

    if model_loaded:
        return

    if not MODEL_PATH.exists():
        load_error = f"Model path does not exist: {MODEL_PATH}"
        raise RuntimeError(load_error)

    from voxcpm import VoxCPM

    mem_before = _mem_used_gb()
    log.info("Loading VoxCPM2 from %s (UMA used before: %.1f GB)...", MODEL_PATH, mem_before)
    started_at = time.time()

    try:
        model = VoxCPM.from_pretrained(
            str(MODEL_PATH),
            load_denoiser=False,
            local_files_only=True,
            optimize=False,
        )
    except Exception as exc:
        load_error = str(exc)
        raise

    model_loaded = True
    load_error = None
    mem_after = _mem_used_gb()
    log.info(
        "VoxCPM2 loaded in %.1fs (UMA: %.1f -> %.1f GB, delta +%.1f GB)",
        time.time() - started_at,
        mem_before,
        mem_after,
        mem_after - mem_before,
    )


def destroy_model() -> None:
    global model, model_loaded

    if model is None:
        return

    mem_before = _mem_used_gb()
    log.info("Destroying VoxCPM2 model (UMA used before: %.1f GB)...", mem_before)

    try:
        if hasattr(model, "tts_model") and hasattr(model.tts_model, "cpu"):
            model.tts_model.cpu()
    except Exception as exc:
        log.debug("CPU offload before delete failed (non-fatal): %s", exc)

    del model
    model = None
    model_loaded = False

    torch.cuda.empty_cache()

    mem_after = _mem_used_gb()
    log.info(
        "VoxCPM2 model destroyed (UMA: %.1f -> %.1f GB, freed ~%.1f GB)",
        mem_before,
        mem_after,
        mem_before - mem_after,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        load_model()
    except Exception as exc:
        log.exception("Failed to load VoxCPM2 runtime: %s", exc)
    yield
    destroy_model()


app = FastAPI(title="VoxCPM2 Service", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {
        "status": "alive",
        "model_path": str(MODEL_PATH),
        "model_loaded": model_loaded,
        "error": load_error,
    }


@app.get("/readyz")
async def readyz():
    if not model_loaded or model is None:
        raise HTTPException(503, load_error or "VoxCPM2 model is not loaded")
    return {
        "status": "ready",
        "model_path": str(MODEL_PATH),
        "sample_rate": int(model.tts_model.sample_rate),
    }


@app.get("/v1/health")
async def runtime_health():
    if not model_loaded or model is None:
        raise HTTPException(503, load_error or "VoxCPM2 runtime is not ready")
    return {
        "status": "ok",
        "model_path": str(MODEL_PATH),
        "sample_rate": int(model.tts_model.sample_rate),
    }


async def _parse_synthesize_request(
    request: Request,
) -> tuple[str, str | None, float, tuple[str, bytes] | None, str | None]:
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        text = str(form.get("text") or "").strip()
        prompt_text = str(form.get("prompt_text") or "").strip() or None
        vox_mode = str(form.get("vox_mode") or "").strip().lower() or None

        try:
            speed = float(form.get("speed", 1.0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "Invalid speed value") from exc

        reference_audio = form.get("reference_audio")
        if hasattr(reference_audio, "read"):
            reference_bytes = await reference_audio.read()
            if reference_bytes:
                filename = getattr(reference_audio, "filename", "") or "reference.wav"
                return text, prompt_text, speed, (filename, reference_bytes), vox_mode

        return text, prompt_text, speed, None, vox_mode

    payload = await request.json()
    text = str(payload.get("text") or "").strip()
    prompt_text = str(payload.get("prompt_text") or "").strip() or None
    speed = float(payload.get("speed", 1.0))
    vox_mode = str(payload.get("vox_mode") or "").strip().lower() or None
    return text, prompt_text, speed, None, vox_mode


@app.post("/synthesize")
async def synthesize(request: Request):
    if not model_loaded or model is None:
        raise HTTPException(503, load_error or "VoxCPM2 runtime is not ready")

    text, prompt_text, speed, reference_audio, vox_mode = await _parse_synthesize_request(request)
    if not text:
        raise HTTPException(400, "Empty text")

    if vox_mode is not None and vox_mode not in VALID_VOX_MODES:
        raise HTTPException(400, "vox_mode must be design, clone, or continuation")

    effective_mode = vox_mode
    if effective_mode is None:
        if prompt_text:
            effective_mode = VOX_MODE_CONTINUATION
        elif reference_audio is not None:
            effective_mode = VOX_MODE_CLONE
        else:
            effective_mode = VOX_MODE_DESIGN

    if effective_mode == VOX_MODE_DESIGN:
        if reference_audio is not None:
            raise HTTPException(400, "design mode does not accept reference_audio")
        if prompt_text:
            raise HTTPException(400, "design mode does not accept prompt_text")
    elif effective_mode == VOX_MODE_CLONE:
        if reference_audio is None:
            raise HTTPException(400, "clone mode requires reference_audio")
        if prompt_text:
            raise HTTPException(400, "clone mode does not accept prompt_text")
    elif effective_mode == VOX_MODE_CONTINUATION:
        if reference_audio is None:
            raise HTTPException(400, "continuation mode requires reference_audio")
        if not prompt_text:
            raise HTTPException(400, "continuation mode requires prompt_text")

    if speed != 1.0:
        log.info("Ignoring unsupported VoxCPM2 speed override: %.2f", speed)

    tmp_audio_path: Path | None = None
    try:
        generate_kwargs = {
            "text": text,
            "cfg_value": CFG_VALUE,
            "inference_timesteps": INFERENCE_TIMESTEPS,
            "normalize": False,
            "denoise": False,
        }

        if reference_audio is not None:
            filename, reference_bytes = reference_audio
            suffix = Path(filename).suffix or ".wav"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_audio:
                temp_audio.write(reference_bytes)
                tmp_audio_path = Path(temp_audio.name)

            generate_kwargs["reference_wav_path"] = str(tmp_audio_path)
            if effective_mode == VOX_MODE_CONTINUATION and prompt_text:
                generate_kwargs["prompt_wav_path"] = str(tmp_audio_path)
                generate_kwargs["prompt_text"] = prompt_text

        started_at = time.time()
        with torch.inference_mode():
            wav = model.generate(**generate_kwargs)
        elapsed = time.time() - started_at

        buffer = io.BytesIO()
        sf.write(buffer, wav, int(model.tts_model.sample_rate), format="WAV")
        log.info("Synthesized %d chars in %.1fs (%s mode)", len(text), elapsed, effective_mode)
        return Response(content=buffer.getvalue(), media_type="audio/wav")
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("VoxCPM2 synthesis failed: %s", exc)
        raise HTTPException(500, f"VoxCPM2 synthesis failed: {exc}") from exc
    finally:
        if tmp_audio_path is not None:
            tmp_audio_path.unlink(missing_ok=True)
