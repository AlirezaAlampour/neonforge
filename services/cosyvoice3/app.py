from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import soundfile as sf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
MODEL_DIR = Path(os.getenv("COSYVOICE3_MODEL_DIR", "/models/voice/cosyvoice3"))
MODEL_DIR_CANDIDATES = (
    "Fun-CosyVoice3-0.5B",
    "Fun-CosyVoice3-0.5B-2512",
)
REQUIRED_MODEL_FILES = (
    "cosyvoice3.yaml",
    "campplus.onnx",
    "speech_tokenizer_v3.onnx",
    "llm.pt",
    "flow.pt",
    "hift.pt",
)
OPTIONAL_MODEL_FILES = ("spk2info.pt",)
REQUIRED_MODEL_DIRS = ("CosyVoice-BlankEN",)

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("cosyvoice3")

SMOKE_TEXT = "This is a short NeonForge smoke test."
SMOKE_PROMPT_TEXT = "You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。"
SMOKE_PROMPT_AUDIO_CANDIDATES = (
    Path("/opt/CosyVoice/asset/zero_shot_prompt.wav"),
    Path("/tmp/CosyVoice/asset/zero_shot_prompt.wav"),
)

_model: Any | None = None
_load_error: str | None = None
_dependency_error: str | None = None
_load_lock = asyncio.Lock()
_load_task: asyncio.Task[Any] | None = None
_load_started_at: float | None = None


def _resolve_model_dir() -> Path:
    if MODEL_DIR.is_dir():
        for candidate in MODEL_DIR_CANDIDATES:
            candidate_path = MODEL_DIR / candidate
            if candidate_path.is_dir():
                return candidate_path
        return MODEL_DIR
    raise RuntimeError(f"CosyVoice 3 model directory does not exist: {MODEL_DIR}")


def _model_inventory(model_dir: Path | None) -> dict[str, Any]:
    if model_dir is None:
        return {"root": None, "files": {}, "directories": {}}

    return {
        "root": model_dir,
        "files": {name: model_dir / name for name in REQUIRED_MODEL_FILES},
        "optional_files": {name: model_dir / name for name in OPTIONAL_MODEL_FILES},
        "directories": {name: model_dir / name for name in REQUIRED_MODEL_DIRS},
    }


def _model_ready_error(model_dir: Path | None) -> str | None:
    if model_dir is None:
        return None

    inventory = _model_inventory(model_dir)
    for name, path in inventory["files"].items():
        if not path.is_file():
            return f"Missing required CosyVoice 3 file: {path}"
    for name, path in inventory["directories"].items():
        if not path.is_dir():
            return f"Missing required CosyVoice 3 directory: {path}"
    return None


def _ensure_dependencies() -> None:
    global _dependency_error

    if importlib.util.find_spec("cosyvoice") is None:
        _dependency_error = "CosyVoice Python package is not installed in this runtime"
        raise RuntimeError(_dependency_error)

    _dependency_error = None


def _runtime_status() -> tuple[Path | None, str | None]:
    try:
        _ensure_dependencies()
        resolved_model_dir = _resolve_model_dir()
        model_error = _model_ready_error(resolved_model_dir)
        if model_error is not None:
            raise RuntimeError(model_error)
        return resolved_model_dir, None
    except RuntimeError as exc:
        return None, str(exc)


def _resolve_smoke_prompt_audio() -> Path:
    for candidate in SMOKE_PROMPT_AUDIO_CANDIDATES:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(path) for path in SMOKE_PROMPT_AUDIO_CANDIDATES)
    raise RuntimeError(f"CosyVoice 3 smoke prompt audio is missing. Checked: {searched}")


def _wav_response(audio: Any, sample_rate: int) -> Response:
    buffer = io.BytesIO()
    sf.write(buffer, audio, sample_rate, format="WAV")
    return Response(content=buffer.getvalue(), media_type="audio/wav")


def load_model():
    global _model, _load_error

    if _model is not None:
        return _model

    resolved_model_dir, runtime_error = _runtime_status()
    if runtime_error is not None or resolved_model_dir is None:
        _load_error = runtime_error or "CosyVoice 3 runtime is not configured"
        raise RuntimeError(_load_error)

    from cosyvoice.cli.cosyvoice import AutoModel

    log.info("Loading CosyVoice 3 from %s", resolved_model_dir)
    _model = AutoModel(model_dir=str(resolved_model_dir))
    _load_error = None
    return _model


def destroy_model() -> None:
    global _model, _load_task, _load_started_at
    _model = None
    _load_task = None
    _load_started_at = None


async def _ensure_model():
    global _load_task, _load_started_at

    if _model is not None:
        return _model

    if _load_task is not None and not _load_task.done():
        return await _load_task

    async with _load_lock:
        if _model is not None:
            return _model
        if _load_task is None or _load_task.done():
            _load_started_at = time.monotonic()
            _load_task = asyncio.create_task(asyncio.to_thread(load_model))
        task = _load_task

    return await task


async def _kick_off_model_load() -> None:
    global _load_task, _load_started_at

    if _model is not None:
        return

    async with _load_lock:
        if _model is not None:
            return
        if _load_task is None or _load_task.done():
            _load_started_at = time.monotonic()
            _load_task = asyncio.create_task(asyncio.to_thread(load_model))


def _load_state() -> dict[str, Any]:
    if _model is not None:
        return {"state": "loaded", "seconds": None, "error": None}

    if _load_task is None:
        return {"state": "idle", "seconds": None, "error": None}

    seconds = None
    if _load_started_at is not None:
        seconds = round(max(0.0, time.monotonic() - _load_started_at), 1)

    if not _load_task.done():
        return {"state": "loading", "seconds": seconds, "error": None}

    try:
        _load_task.result()
    except Exception as exc:
        return {"state": "failed", "seconds": seconds, "error": str(exc) or "CosyVoice 3 failed to load"}

    return {"state": "loaded" if _model is not None else "idle", "seconds": seconds, "error": None}


app = FastAPI(title="CosyVoice 3 Service")


@app.get("/healthz")
async def healthz():
    resolved_model_dir: Path | None = None
    resolve_error: str | None = None
    try:
        _ensure_dependencies()
        resolved_model_dir = _resolve_model_dir()
    except RuntimeError as exc:
        resolve_error = str(exc)

    runtime_error = _model_ready_error(resolved_model_dir) or resolve_error or _load_error
    inventory = _model_inventory(resolved_model_dir)
    load_state = _load_state()
    return {
        "status": "alive",
        "model_dir": str(MODEL_DIR),
        "resolved_model_dir": str(resolved_model_dir) if resolved_model_dir is not None else None,
        "inventory": {
            "root": str(inventory["root"]) if isinstance(inventory["root"], Path) else None,
            "files": {name: str(path) for name, path in inventory["files"].items()},
            "optional_files": {name: str(path) for name, path in inventory["optional_files"].items()},
            "directories": {name: str(path) for name, path in inventory["directories"].items()},
        },
        "model_loaded": _model is not None,
        "load_state": load_state["state"],
        "load_seconds": load_state["seconds"],
        "dependency_error": _dependency_error,
        "error": runtime_error or load_state["error"],
    }


@app.get("/readyz")
async def readyz():
    resolved_model_dir, runtime_error = _runtime_status()
    if runtime_error is not None or resolved_model_dir is None:
        raise HTTPException(503, runtime_error or "CosyVoice 3 runtime is not configured")

    if _model is None:
        await _kick_off_model_load()
        load_state = _load_state()
        if load_state["state"] == "failed":
            raise HTTPException(503, load_state["error"] or "CosyVoice 3 failed to load")
        if load_state["state"] != "loaded":
            message = "CosyVoice 3 is still loading"
            if load_state["seconds"] is not None:
                message = f"{message} ({load_state['seconds']}s elapsed)"
            raise HTTPException(503, message)

    return {
        "status": "ready",
        "model_dir": str(MODEL_DIR),
        "resolved_model_dir": str(resolved_model_dir),
        "model_loaded": True,
    }


@app.get("/smoke")
async def smoke():
    model = await _ensure_model()

    try:
        prompt_audio = _resolve_smoke_prompt_audio()
        generated = await asyncio.to_thread(
            lambda: next(
                model.inference_zero_shot(
                    SMOKE_TEXT,
                    SMOKE_PROMPT_TEXT,
                    str(prompt_audio),
                    stream=False,
                )
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("CosyVoice 3 smoke generation failed: %s", exc)
        raise HTTPException(503, f"CosyVoice 3 smoke generation failed: {exc}") from exc

    audio = _first_generated_audio(generated)
    return _wav_response(audio, int(getattr(model, "sample_rate", 24000)))


async def _parse_synthesize_request(request: Request) -> tuple[str, str, str, tuple[str, bytes] | None]:
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        text = str(form.get("text") or "").strip()
        prompt_text = str(form.get("prompt_text") or "").strip()
        voice_mode = str(form.get("voice_mode") or "clone").strip().lower() or "clone"
        reference_audio = form.get("reference_audio") or form.get("ref_audio")
        if hasattr(reference_audio, "read"):
            reference_bytes = await reference_audio.read()
            if reference_bytes:
                filename = getattr(reference_audio, "filename", "") or "reference.wav"
                return text, prompt_text, voice_mode, (filename, reference_bytes)
        return text, prompt_text, voice_mode, None

    payload = await request.json()
    return (
        str(payload.get("text") or "").strip(),
        str(payload.get("prompt_text") or "").strip(),
        str(payload.get("voice_mode") or "clone").strip().lower() or "clone",
        None,
    )


def _first_generated_audio(result: Any) -> Any:
    if isinstance(result, dict) and "tts_speech" in result:
        return result["tts_speech"]
    return result


@app.post("/synthesize")
async def synthesize(request: Request):
    model = await _ensure_model()
    text, prompt_text, voice_mode, reference_audio = await _parse_synthesize_request(request)

    if not text:
        raise HTTPException(400, "Empty text")
    if voice_mode != "clone":
        raise HTTPException(400, "Only clone mode is supported")
    if reference_audio is None:
        raise HTTPException(400, "clone mode requires reference_audio")
    if not prompt_text:
        raise HTTPException(400, "clone mode requires prompt_text")

    filename, reference_bytes = reference_audio
    suffix = Path(filename).suffix or ".wav"
    tmp_audio_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_audio:
            temp_audio.write(reference_bytes)
            tmp_audio_path = Path(temp_audio.name)

        generated = await asyncio.to_thread(
            lambda: next(
                model.inference_zero_shot(
                    text,
                    prompt_text,
                    str(tmp_audio_path),
                    stream=False,
                )
            )
        )
        audio = _first_generated_audio(generated)
        return _wav_response(audio, int(getattr(model, "sample_rate", 24000)))
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("CosyVoice 3 synthesis failed: %s", exc)
        raise HTTPException(500, f"CosyVoice 3 synthesis failed: {exc}") from exc
    finally:
        if tmp_audio_path is not None:
            tmp_audio_path.unlink(missing_ok=True)
