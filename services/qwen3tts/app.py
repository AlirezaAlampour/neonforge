from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import soundfile as sf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
MODEL_DIR = Path(os.getenv("QWEN3TTS_MODEL_DIR", "/models/voice/qwen3tts"))
CLONE_MODEL_CANDIDATES = ("Qwen3-TTS-12Hz-1.7B-Base", "Qwen3-TTS-12Hz-0.6B-Base")
DESIGN_MODEL_CANDIDATES = ("Qwen3-TTS-12Hz-1.7B-VoiceDesign",)
MODEL_WEIGHT_GLOBS = ("*.safetensors", "pytorch_model*.bin")
SPEECH_TOKENIZER_CONFIG = Path("speech_tokenizer/config.json")
SMOKE_REF_TEXT = "This is a short Qwen3-TTS smoke test reference."
SMOKE_DESIGN_INSTRUCT = "Warm, steady, natural documentary narrator."
SMOKE_OUTPUT_TEXT = "NeonForge is verifying that Qwen three TTS can load and synthesize locally."

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("qwen3tts")

_clone_model: Any | None = None
_design_model: Any | None = None
_clone_load_error: str | None = None
_design_load_error: str | None = None
_dependency_error: str | None = None
_clone_load_lock = asyncio.Lock()
_design_load_lock = asyncio.Lock()


def _detect_language_label(text: str) -> str:
    for character in text:
        codepoint = ord(character)
        if 0x4E00 <= codepoint <= 0x9FFF:
            return "Chinese"
        if 0x3040 <= codepoint <= 0x30FF:
            return "Japanese"
        if 0xAC00 <= codepoint <= 0xD7AF:
            return "Korean"
        if 0x0400 <= codepoint <= 0x04FF:
            return "Russian"
    return "English"


def _resolve_subdir(candidates: tuple[str, ...]) -> Path | None:
    if not MODEL_DIR.is_dir():
        return None

    for candidate in candidates:
        path = MODEL_DIR / candidate
        if path.is_dir():
            return path
    return None


def _has_model_weights(path: Path) -> bool:
    for pattern in MODEL_WEIGHT_GLOBS:
        if any(path.glob(pattern)):
            return True
    return False


def _snapshot_layout_error(path: Path | None, *, label: str) -> str | None:
    if path is None:
        return f"{label} directory is missing under {MODEL_DIR}"

    config_path = path / "config.json"
    if not config_path.is_file():
        return f"{label} snapshot is missing config.json: {config_path}"

    speech_tokenizer_config = path / SPEECH_TOKENIZER_CONFIG
    if not speech_tokenizer_config.is_file():
        return f"{label} snapshot is missing speech_tokenizer/config.json: {speech_tokenizer_config}"

    if not _has_model_weights(path):
        return f"{label} snapshot is missing model weights under {path}"

    return None


def _runtime_inventory() -> dict[str, Path | None]:
    return {
        "root": MODEL_DIR if MODEL_DIR.is_dir() else None,
        "clone_model": _resolve_subdir(CLONE_MODEL_CANDIDATES),
        "design_model": _resolve_subdir(DESIGN_MODEL_CANDIDATES),
    }


def _ensure_dependencies() -> None:
    global _dependency_error

    if importlib.util.find_spec("qwen_tts") is None:
        _dependency_error = "qwen-tts is not installed in this runtime"
        raise RuntimeError(_dependency_error)

    _dependency_error = None


def _ready_error() -> str | None:
    try:
        _ensure_dependencies()
    except RuntimeError as exc:
        return str(exc)

    inventory = _runtime_inventory()
    if inventory["root"] is None:
        return f"Qwen3-TTS model directory does not exist: {MODEL_DIR}"
    clone_error = _snapshot_layout_error(inventory["clone_model"], label="Clone model")
    if clone_error is not None:
        return clone_error
    design_error = _snapshot_layout_error(inventory["design_model"], label="VoiceDesign model")
    if design_error is not None:
        return design_error
    return None


def _common_model_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    try:
        import torch

        kwargs["dtype"] = torch.bfloat16
        kwargs["device_map"] = "cuda:0" if torch.cuda.is_available() else "cpu"
    except Exception:
        pass

    if importlib.util.find_spec("flash_attn") is not None:
        kwargs["attn_implementation"] = "flash_attention_2"

    return kwargs


def _build_model(model_dir: Path):
    from qwen_tts import Qwen3TTSModel

    kwargs = _common_model_kwargs()
    return Qwen3TTSModel.from_pretrained(str(model_dir), **kwargs)


def _load_clone_model():
    global _clone_model, _clone_load_error

    if _clone_model is not None:
        return _clone_model

    runtime_error = _ready_error()
    if runtime_error is not None:
        _clone_load_error = runtime_error
        raise RuntimeError(runtime_error)

    inventory = _runtime_inventory()
    assert inventory["clone_model"] is not None
    log.info("Loading Qwen3-TTS clone model from %s", inventory["clone_model"])
    _clone_model = _build_model(inventory["clone_model"])
    _clone_load_error = None
    return _clone_model


def _load_design_model():
    global _design_model, _design_load_error

    if _design_model is not None:
        return _design_model

    runtime_error = _ready_error()
    if runtime_error is not None:
        _design_load_error = runtime_error
        raise RuntimeError(runtime_error)

    inventory = _runtime_inventory()
    assert inventory["design_model"] is not None
    log.info("Loading Qwen3-TTS design model from %s", inventory["design_model"])
    _design_model = _build_model(inventory["design_model"])
    _design_load_error = None
    return _design_model


async def _ensure_clone_model():
    if _clone_model is not None:
        return _clone_model

    async with _clone_load_lock:
        if _clone_model is not None:
            return _clone_model
        return await asyncio.to_thread(_load_clone_model)


async def _ensure_design_model():
    if _design_model is not None:
        return _design_model

    async with _design_load_lock:
        if _design_model is not None:
            return _design_model
        return await asyncio.to_thread(_load_design_model)


async def _ensure_all_models():
    clone_model = await _ensure_clone_model()
    design_model = await _ensure_design_model()
    return clone_model, design_model


def _wav_response(audio: Any, sample_rate: int) -> Response:
    buffer = io.BytesIO()
    sf.write(buffer, audio, sample_rate, format="WAV")
    return Response(content=buffer.getvalue(), media_type="audio/wav")


app = FastAPI(title="Qwen3-TTS Service")


@app.get("/healthz")
async def healthz():
    inventory = _runtime_inventory()
    return {
        "status": "alive",
        "model_dir": str(MODEL_DIR),
        "inventory": {key: str(value) if isinstance(value, Path) else None for key, value in inventory.items()},
        "clone_validation_error": _snapshot_layout_error(inventory["clone_model"], label="Clone model"),
        "design_validation_error": _snapshot_layout_error(inventory["design_model"], label="VoiceDesign model"),
        "clone_model_loaded": _clone_model is not None,
        "design_model_loaded": _design_model is not None,
        "dependency_error": _dependency_error,
        "clone_error": _clone_load_error,
        "design_error": _design_load_error,
        "ready_error": _ready_error(),
    }


@app.get("/readyz")
async def readyz():
    runtime_error = _ready_error()
    if runtime_error is not None:
        raise HTTPException(503, runtime_error)

    try:
        await _ensure_all_models()
    except Exception as exc:
        message = str(exc) or "Qwen3-TTS failed to load"
        raise HTTPException(503, message) from exc

    inventory = _runtime_inventory()
    return {
        "status": "ready",
        "model_dir": str(MODEL_DIR),
        "inventory": {key: str(value) if isinstance(value, Path) else None for key, value in inventory.items()},
        "clone_model_loaded": True,
        "design_model_loaded": True,
    }


@app.get("/smoke")
async def smoke():
    clone_model, design_model = await _ensure_all_models()

    try:
        ref_wavs, ref_sample_rate = await asyncio.to_thread(
            design_model.generate_voice_design,
            text=SMOKE_REF_TEXT,
            language="English",
            instruct=SMOKE_DESIGN_INSTRUCT,
        )
        wavs, sample_rate = await asyncio.to_thread(
            clone_model.generate_voice_clone,
            text=SMOKE_OUTPUT_TEXT,
            language="English",
            ref_audio=(ref_wavs[0], ref_sample_rate),
            ref_text=SMOKE_REF_TEXT,
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Qwen3-TTS smoke generation failed: %s", exc)
        raise HTTPException(503, f"Qwen3-TTS smoke generation failed: {exc}") from exc

    return _wav_response(wavs[0], sample_rate)


async def _parse_synthesize_request(request: Request) -> tuple[str, str, str, tuple[str, bytes] | None]:
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        text = str(form.get("text") or "").strip()
        voice_mode = str(form.get("voice_mode") or "clone").strip().lower() or "clone"
        prompt_text = str(form.get("prompt_text") or "").strip()
        style_text = str(form.get("style_text") or "").strip()
        reference_audio = form.get("reference_audio") or form.get("ref_audio")
        if hasattr(reference_audio, "read"):
            reference_bytes = await reference_audio.read()
            if reference_bytes:
                filename = getattr(reference_audio, "filename", "") or "reference.wav"
                return text, voice_mode, prompt_text or style_text, (filename, reference_bytes)
        return text, voice_mode, prompt_text or style_text, None

    payload = await request.json()
    text = str(payload.get("text") or "").strip()
    voice_mode = str(payload.get("voice_mode") or "clone").strip().lower() or "clone"
    prompt_or_style = str(payload.get("prompt_text") or payload.get("style_text") or "").strip()
    return text, voice_mode, prompt_or_style, None


@app.post("/synthesize")
async def synthesize(request: Request):
    text, voice_mode, prompt_or_style, reference_audio = await _parse_synthesize_request(request)

    if not text:
        raise HTTPException(400, "Empty text")

    language = _detect_language_label(text)

    if voice_mode == "clone":
        if reference_audio is None:
            raise HTTPException(400, "clone mode requires reference_audio")
        if not prompt_or_style:
            raise HTTPException(400, "clone mode requires prompt_text")

        model = await _ensure_clone_model()
        filename, reference_bytes = reference_audio
        suffix = Path(filename).suffix or ".wav"
        tmp_audio_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_audio:
                temp_audio.write(reference_bytes)
                tmp_audio_path = Path(temp_audio.name)

            wavs, sample_rate = await asyncio.to_thread(
                model.generate_voice_clone,
                text=text,
                language=language,
                ref_audio=str(tmp_audio_path),
                ref_text=prompt_or_style,
            )
            return _wav_response(wavs[0], sample_rate)
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("Qwen3-TTS clone synthesis failed: %s", exc)
            raise HTTPException(500, f"Qwen3-TTS clone synthesis failed: {exc}") from exc
        finally:
            if tmp_audio_path is not None:
                tmp_audio_path.unlink(missing_ok=True)

    if voice_mode == "design":
        if not prompt_or_style:
            raise HTTPException(400, "design mode requires style_text")

        model = await _ensure_design_model()
        try:
            wavs, sample_rate = await asyncio.to_thread(
                model.generate_voice_design,
                text=text,
                language=language,
                instruct=prompt_or_style,
            )
            return _wav_response(wavs[0], sample_rate)
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("Qwen3-TTS design synthesis failed: %s", exc)
            raise HTTPException(500, f"Qwen3-TTS design synthesis failed: {exc}") from exc

    raise HTTPException(400, "voice_mode must be clone or design")
