from __future__ import annotations

import asyncio
import gc
import io
import logging
import math
import os
import sys
import tempfile
import threading
import time
import uuid
import wave
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
BREEZE_SOURCE_DIR = Path(os.getenv("BREEZE_TTS_SOURCE_DIR", "/opt/breeze-tts-src"))
MODEL_ID = os.getenv("BREEZE_TTS_MODEL_ID", "BreezeBlue/Breeze-TTS-2")
MODEL_PATH = Path(os.getenv("BREEZE_TTS_MODEL_PATH", "/models/breeze_tts/Breeze-TTS-2"))
HF_CACHE_DIR = Path(os.getenv("HF_HOME", "/cache/hf"))
SERVICE_ENABLED = os.getenv("BREEZE_TTS_SERVICE_ENABLED", "true").lower() == "true"
AUTO_DOWNLOAD = os.getenv("BREEZE_TTS_AUTO_DOWNLOAD", "true").lower() == "true"
SAMPLE_RATE = 24_000
DEFAULT_SEED = 42
DEFAULT_INSTRUCTION_CFG_SCALE = 4.0
DEFAULT_CLONE_CFG_SCALE = 1.0
MAX_NEW_TOKENS = 1500
MAX_SEQ_LEN = 2048
REPETITION_PENALTY = 1.1
VALID_MODES = {"design", "clone", "direction"}

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("breeze_tts")


@dataclass(slots=True)
class RuntimeBundle:
    tokenizer: Any
    model: Any
    audio_tokenizer: Any
    runtime: Any
    checkpoint_path: Path


_runtime: RuntimeBundle | None = None
_runtime_status = "disabled" if not SERVICE_ENABLED else "loading"
_load_error: str | None = None
_load_started_at: float | None = None
_load_finished_at: float | None = None
_load_task: asyncio.Task[None] | None = None
_state_lock = threading.Lock()
_inference_lock = asyncio.Lock()


def _mem_available_gb() -> float:
    try:
        with Path("/proc/meminfo").open(encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return -1.0


def _checkpoint_is_complete(path: Path) -> bool:
    return (path / "config.json").is_file() and (path / "audio_tokenizer").is_dir()


def _resolve_checkpoint() -> Path:
    if _checkpoint_is_complete(MODEL_PATH):
        return MODEL_PATH
    if not AUTO_DOWNLOAD:
        raise FileNotFoundError(
            f"Breeze TTS 2 checkpoint is missing at {MODEL_PATH}; enable BREEZE_TTS_AUTO_DOWNLOAD or download {MODEL_ID}."
        )

    from huggingface_hub import snapshot_download

    MODEL_PATH.mkdir(parents=True, exist_ok=True)
    log.info("Downloading %s to %s using shared Hugging Face storage", MODEL_ID, MODEL_PATH)
    downloaded_path = Path(
        snapshot_download(
            repo_id=MODEL_ID,
            local_dir=MODEL_PATH,
            cache_dir=HF_CACHE_DIR,
        )
    )
    if not _checkpoint_is_complete(downloaded_path):
        raise RuntimeError(f"Downloaded Breeze checkpoint is incomplete: {downloaded_path}")
    return downloaded_path


def _load_runtime() -> RuntimeBundle:
    if not BREEZE_SOURCE_DIR.is_dir():
        raise FileNotFoundError(f"Official Breeze inference source is missing: {BREEZE_SOURCE_DIR}")
    source_value = str(BREEZE_SOURCE_DIR)
    if source_value not in sys.path:
        sys.path.insert(0, source_value)

    import torch
    from breeze_infer.runtime import load_runtime, resolve_device, update_generation_config_for_breeze
    from models.fast_streaming import FastBreezeStreamingRuntime, FastStreamingConfig

    if not torch.cuda.is_available():
        raise RuntimeError("Breeze TTS 2 requires a CUDA-capable NVIDIA GPU")

    checkpoint_path = _resolve_checkpoint()
    device = resolve_device()
    tokenizer, model, audio_tokenizer = load_runtime(
        checkpoint_path,
        device=device,
        attn_implementation="eager",
    )
    update_generation_config_for_breeze(model)
    config = FastStreamingConfig(
        max_new_tokens=MAX_NEW_TOKENS,
        max_seq_len=MAX_SEQ_LEN,
        fast_all=False,
        fast_text_encoder=False,
        fast_backbone_prefill=False,
        fast_backbone_decode=False,
        fast_depth_decoder=False,
        fast_codec=False,
        repetition_penalty=REPETITION_PENALTY,
    )
    runtime = FastBreezeStreamingRuntime(model, audio_tokenizer, config, tokenizer=tokenizer)
    if runtime.fast_enabled:
        raise RuntimeError("Breeze fast CUDA-graph inference must remain disabled on the DGX Spark eager runtime")
    return RuntimeBundle(tokenizer, model, audio_tokenizer, runtime, checkpoint_path)


def _load_runtime_once() -> None:
    global _runtime, _runtime_status, _load_error, _load_started_at, _load_finished_at

    if not SERVICE_ENABLED:
        return
    with _state_lock:
        if _runtime is not None or _runtime_status == "ready":
            return
        _runtime_status = "loading"
        _load_error = None
        _load_started_at = time.time()

    mem_before = _mem_available_gb()
    log.info("Loading Breeze TTS 2 eager runtime (MemAvailable %.1f GB)", mem_before)
    try:
        bundle = _load_runtime()
    except Exception as exc:
        with _state_lock:
            _runtime = None
            _runtime_status = "error"
            _load_error = str(exc)
            _load_finished_at = time.time()
        log.exception("Breeze TTS 2 failed to load: %s", exc)
        return

    with _state_lock:
        _runtime = bundle
        _runtime_status = "ready"
        _load_error = None
        _load_finished_at = time.time()
    log.info(
        "Breeze TTS 2 ready from %s in %.1fs (MemAvailable %.1f -> %.1f GB)",
        bundle.checkpoint_path,
        (_load_finished_at or time.time()) - (_load_started_at or time.time()),
        mem_before,
        _mem_available_gb(),
    )


async def _background_load() -> None:
    await asyncio.to_thread(_load_runtime_once)


async def _ensure_runtime() -> RuntimeBundle:
    global _load_task

    if not SERVICE_ENABLED:
        raise HTTPException(503, "Breeze TTS 2 service is disabled")
    if _runtime is not None and _runtime_status == "ready":
        return _runtime
    if _load_task is None or _load_task.done():
        _load_task = asyncio.create_task(_background_load())
    await _load_task
    if _runtime is None:
        raise HTTPException(503, _load_error or "Breeze TTS 2 runtime is unavailable")
    return _runtime


def _destroy_runtime() -> None:
    global _runtime, _runtime_status
    if _runtime is None:
        return
    try:
        if hasattr(_runtime.model, "cpu"):
            _runtime.model.cpu()
    except Exception as exc:
        log.debug("Breeze CPU offload during shutdown failed: %s", exc)
    _runtime = None
    _runtime_status = "loading" if SERVICE_ENABLED else "disabled"
    gc.collect()
    try:
        import torch

        torch.cuda.empty_cache()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _load_task
    if SERVICE_ENABLED:
        _load_task = asyncio.create_task(_background_load())
    yield
    if _load_task is not None and not _load_task.done():
        _load_task.cancel()
    _destroy_runtime()


app = FastAPI(title="NeonForge Breeze TTS 2 Service", lifespan=lifespan)


def _health_payload() -> dict[str, Any]:
    return {
        "status": "alive",
        "runtime_status": _runtime_status,
        "model_id": MODEL_ID,
        "model_path": str(MODEL_PATH),
        "model_loaded": _runtime is not None,
        "attention": "eager",
        "fast_path": False,
        "sample_rate": SAMPLE_RATE,
        "error": _load_error,
        "mem_available_gb": _mem_available_gb(),
    }


@app.get("/healthz")
async def healthz():
    return _health_payload()


@app.get("/readyz")
async def readyz():
    payload = _health_payload()
    if _runtime_status != "ready" or _runtime is None:
        raise HTTPException(503, _load_error or f"Breeze runtime is {_runtime_status}")
    return payload


@app.get("/v1/health")
async def runtime_health():
    return await readyz()


def _validate_request(
    *,
    mode: str,
    instruction: str | None,
    has_reference_audio: bool,
    ref_text: str,
    cfg_scale: float | None,
    seed: int,
) -> tuple[str, str | None, str, float, int]:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in VALID_MODES:
        raise ValueError("mode must be design, clone, or direction")
    normalized_instruction = instruction.strip() if instruction and instruction.strip() else None
    normalized_ref_text = ref_text.strip()
    if has_reference_audio != bool(normalized_ref_text):
        raise ValueError("reference audio and its exact transcript must be provided together")
    if seed < 0 or seed > 4_294_967_295:
        raise ValueError("seed must be between 0 and 4294967295")

    if normalized_mode == "design":
        if has_reference_audio:
            raise ValueError("Voice Design does not accept reference audio")
        if not normalized_instruction:
            raise ValueError("Voice Design requires a voice description")
        effective_cfg = DEFAULT_INSTRUCTION_CFG_SCALE if cfg_scale is None else cfg_scale
    elif normalized_mode == "clone":
        if not has_reference_audio:
            raise ValueError("Voice Clone requires reference audio and its exact transcript")
        if normalized_instruction:
            raise ValueError("Voice Clone does not accept an instruction; use Voice Direction instead")
        effective_cfg = DEFAULT_CLONE_CFG_SCALE if cfg_scale is None else cfg_scale
    else:
        if not has_reference_audio:
            raise ValueError("Voice Direction requires reference audio and its exact transcript")
        if not normalized_instruction:
            raise ValueError("Voice Direction requires an instruction")
        effective_cfg = DEFAULT_INSTRUCTION_CFG_SCALE if cfg_scale is None else cfg_scale

    if not math.isfinite(effective_cfg) or effective_cfg <= 0:
        raise ValueError("cfg_scale must be greater than 0")
    return normalized_mode, normalized_instruction, normalized_ref_text, float(effective_cfg), int(seed)


def _float_audio_to_pcm16(audio: Any) -> bytes:
    import numpy as np

    normalized = np.asarray(audio, dtype=np.float32)
    normalized = np.clip(normalized, -1.0, 1.0)
    return (normalized * 32767.0).astype("<i2", copy=False).tobytes()


def _render_wav(
    bundle: RuntimeBundle,
    *,
    text: str,
    mode: str,
    instruction: str | None,
    reference_path: Path | None,
    ref_text: str,
    cfg_scale: float,
    seed: int,
) -> bytes:
    from breeze_infer.runtime import set_all_seeds
    from breeze_infer.templates import get_template, prepare_inputs, select_template_name

    request_id = f"neonforge-{uuid.uuid4().hex}"
    request: dict[str, Any] = {"id": request_id, "text": text, "speaker": "S0"}
    if instruction:
        request["instruction"] = instruction
    if reference_path is not None:
        request["ref_audio_path"] = str(reference_path)
        request["ref_text"] = ref_text

    template_name = select_template_name(request)
    set_all_seeds(seed)
    inputs = prepare_inputs(
        bundle.tokenizer,
        bundle.audio_tokenizer,
        bundle.model,
        [request],
        get_template(template_name),
        guidance_scale=cfg_scale,
        guidance_scale_ref=None,
        guidance_scale_ins=None,
    )

    pcm = bytearray()
    set_all_seeds(seed)
    for chunk in bundle.runtime.iter_audio_chunks(inputs, request_id=request_id, seed=seed):
        pcm.extend(_float_audio_to_pcm16(chunk.audio))
    if not pcm:
        raise RuntimeError("Breeze returned no audio")

    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(int(bundle.runtime.sample_rate or SAMPLE_RATE))
        wav_file.writeframes(bytes(pcm))
    return output.getvalue()


@app.post("/synthesize")
async def synthesize(
    text: str = Form(...),
    mode: str = Form("design"),
    instruction: str | None = Form(None),
    ref_text: str = Form(""),
    cfg_scale: float | None = Form(None),
    seed: int = Form(DEFAULT_SEED),
    reference_audio: UploadFile | None = File(None),
    ref_audio: UploadFile | None = File(None),
):
    cleaned_text = text.strip()
    if not cleaned_text:
        raise HTTPException(400, "text must not be empty")

    upload = reference_audio or ref_audio
    reference_bytes = await upload.read() if upload is not None else b""
    try:
        normalized_mode, normalized_instruction, normalized_ref_text, effective_cfg, normalized_seed = _validate_request(
            mode=mode,
            instruction=instruction,
            has_reference_audio=bool(reference_bytes),
            ref_text=ref_text,
            cfg_scale=cfg_scale,
            seed=seed,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    bundle = await _ensure_runtime()
    reference_path: Path | None = None
    if reference_bytes:
        suffix = Path(upload.filename or "reference.wav").suffix if upload is not None else ".wav"
        with tempfile.NamedTemporaryFile(prefix="breeze_ref_", suffix=suffix or ".wav", delete=False) as temporary:
            temporary.write(reference_bytes)
            reference_path = Path(temporary.name)

    try:
        async with _inference_lock:
            started_at = time.time()
            wav_bytes = await asyncio.to_thread(
                _render_wav,
                bundle,
                text=cleaned_text,
                mode=normalized_mode,
                instruction=normalized_instruction,
                reference_path=reference_path,
                ref_text=normalized_ref_text,
                cfg_scale=effective_cfg,
                seed=normalized_seed,
            )
            log.info(
                "Synthesized %d chars in %.1fs mode=%s seed=%d cfg=%.2f",
                len(cleaned_text),
                time.time() - started_at,
                normalized_mode,
                normalized_seed,
                effective_cfg,
            )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Breeze synthesis failed: %s", exc)
        raise HTTPException(500, f"Breeze synthesis failed: {exc}") from exc
    finally:
        if reference_path is not None:
            reference_path.unlink(missing_ok=True)

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Sample-Rate": str(int(bundle.runtime.sample_rate or SAMPLE_RATE)),
            "X-Breeze-Mode": normalized_mode,
            "Cache-Control": "no-store",
        },
    )
