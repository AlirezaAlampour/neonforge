from __future__ import annotations

import gc
import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

SERVICE_ROOT = Path(__file__).resolve().parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from tts.providers.misotts_provider import MisoTTSProvider

LOG_LEVEL = "INFO"

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("misotts")

provider: MisoTTSProvider | None = None
provider_loaded = False
provider_loading = False
load_error: str | None = None
MIN_CUDA_FREE_GB = float(os.getenv("MISOTTS_MIN_CUDA_FREE_GB", "12"))


def load_provider() -> MisoTTSProvider:
    global provider

    if provider is None:
        provider = MisoTTSProvider()
    return provider


def _release_failed_provider_state() -> None:
    global provider, provider_loaded

    provider = None
    provider_loaded = False
    gc.collect()

    try:
        import torch
    except Exception:
        return

    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, "ipc_collect"):
                torch.cuda.ipc_collect()
    except Exception:
        return


def _provider_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    exception_name = exc.__class__.__name__
    exception_module = exc.__class__.__module__

    if isinstance(exc, FileNotFoundError):
        return message

    if isinstance(exc, ImportError):
        return (
            f"{message or 'MisoTTS dependencies could not be imported.'} "
            "Install the optional MisoTTS runtime dependencies first."
        ).strip()

    if "huggingface_hub" in exception_module or exception_name in {
        "HfHubHTTPError",
        "LocalEntryNotFoundError",
        "EntryNotFoundError",
        "RepositoryNotFoundError",
    }:
        return (
            "Failed to download or resolve the MisoTTS model weights from Hugging Face. "
            "Check MISOTTS_MODEL_ID, network access, and any required Hugging Face credentials."
        )

    lower_message = message.lower()
    if any(marker in lower_message for marker in ("gated repo", "please log in", "meta-llama/llama-3.2-1b")):
        return (
            "Failed to download or resolve the MisoTTS model weights from Hugging Face. "
            "This runtime also needs access to the gated repo meta-llama/Llama-3.2-1B. "
            "Accept the Hugging Face license and provide credentials to the misotts service."
        )

    if "out of memory" in lower_message:
        return (
            "MisoTTS ran out of CUDA memory while loading or generating audio. "
            "Free GPU/UMA memory and retry."
        )

    return message or "MisoTTS failed unexpectedly"


def _set_load_state(runtime: MisoTTSProvider) -> None:
    global provider_loaded
    if hasattr(runtime, "is_loaded"):
        provider_loaded = bool(runtime.is_loaded())
        return
    provider_loaded = True


def _top_gpu_process() -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    top_process: dict[str, Any] | None = None
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        pid_text, process_name, used_memory_text, *_rest = [part.strip() for part in line.split(",")]
        try:
            candidate = {
                "pid": int(pid_text),
                "process_name": process_name,
                "used_memory_mib": int(used_memory_text),
            }
        except ValueError:
            continue
        if top_process is None or candidate["used_memory_mib"] > top_process["used_memory_mib"]:
            top_process = candidate

    return top_process


def _gpu_diagnostics() -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "cuda_available": False,
        "gpu_device": None,
        "gpu_free_gb": None,
        "gpu_total_gb": None,
        "top_gpu_process": None,
        "actionable_hint": None,
    }

    try:
        import torch
    except Exception:
        return diagnostics

    cuda_available = bool(getattr(torch.cuda, "is_available", lambda: False)())
    diagnostics["cuda_available"] = cuda_available
    if not cuda_available:
        return diagnostics

    try:
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        diagnostics["gpu_free_gb"] = round(free_bytes / float(1024**3), 2)
        diagnostics["gpu_total_gb"] = round(total_bytes / float(1024**3), 2)
    except Exception:
        pass

    try:
        diagnostics["gpu_device"] = torch.cuda.get_device_name(0)
    except Exception:
        pass

    diagnostics["top_gpu_process"] = _top_gpu_process()
    if diagnostics["gpu_free_gb"] is not None and diagnostics["gpu_free_gb"] < MIN_CUDA_FREE_GB:
        diagnostics["actionable_hint"] = "Stop Qwen/Vox/Jupyter/LLM service to free GPU memory."
    elif cuda_available:
        diagnostics["actionable_hint"] = (
            "If free VRAM drops too low, stop Qwen/Vox/Jupyter/LLM service to free GPU memory."
        )

    return diagnostics


def _health_payload(status_label: str) -> dict[str, Any]:
    return {
        "status": status_label,
        "runtime_status": "loading" if provider_loading else "ready" if provider_loaded else "error" if load_error else "idle",
        "model_loaded": provider_loaded,
        "error": load_error,
        "diagnostics": _gpu_diagnostics(),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="MisoTTS Service", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return _health_payload("alive")


@app.get("/health")
async def health():
    return await healthz()


@app.get("/readyz")
async def readyz():
    return _health_payload("ready")


@app.get("/v1/health")
async def runtime_health():
    return _health_payload("ok")


@app.post("/synthesize")
async def synthesize(
    request: Request,
    text: str = Form(""),
    speaker: int = Form(0),
    max_audio_length_ms: int = Form(0),
    prompt_text: str | None = Form(None),
    output_wav_path: str | None = Form(None),
    reference_audio: UploadFile | None = File(None),
):
    global load_error, provider_loading

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        text = str(payload.get("text") or "")
        speaker = int(payload.get("speaker", 0))
        max_audio_length_ms = int(payload.get("max_audio_length_ms", 0) or 0)
        prompt_text = str(payload.get("prompt_text") or "").strip() or None
        output_wav_path = str(payload.get("output_wav_path") or "").strip() or None

    cleaned_text = text.strip()
    if not cleaned_text:
        raise HTTPException(400, "Empty text")

    if reference_audio is not None and not (prompt_text or "").strip():
        raise HTTPException(422, "Prompt audio requires prompt_text")

    reference_payload: tuple[str, bytes] | None = None
    if reference_audio is not None:
        reference_bytes = await reference_audio.read()
        if reference_bytes:
            reference_payload = (reference_audio.filename or "prompt.wav", reference_bytes)

    should_report_loading = provider is None or not provider_loaded
    if should_report_loading:
        provider_loading = True

    try:
        runtime = load_provider()
        result = runtime.synthesize(
            text=cleaned_text,
            speaker=int(speaker),
            max_audio_length_ms=int(max_audio_length_ms or 0) or None,
            prompt_text=(prompt_text or "").strip() or None,
            reference_audio=reference_payload,
            output_wav_path=(output_wav_path or "").strip() or None,
        )
        _set_load_state(runtime)
        load_error = None
    except Exception as exc:
        _release_failed_provider_state()
        load_error = _provider_error_message(exc)
        raise HTTPException(503, load_error) from exc
    finally:
        if should_report_loading:
            provider_loading = False

    if result.output_path:
        return {
            "output_path": result.output_path,
            "sample_rate": result.sample_rate,
        }

    if result.audio_bytes is None:
        raise HTTPException(500, "MisoTTS did not return audio bytes")

    return Response(content=result.audio_bytes, media_type="audio/wav")
