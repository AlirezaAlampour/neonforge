"""
Fish Speech adapter service for NeonForge.

This service keeps the internal NeonForge contract stable (`/synthesize`,
`/healthz`, `/readyz`) while delegating runtime-specific details to a
Fish Speech-compatible upstream server.
"""

import base64
import json
import logging
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, model_validator

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
OUTPUTS_ROOT = Path(os.getenv("OUTPUTS_ROOT", "/outputs"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(OUTPUTS_ROOT / "tts" / "fish_speech")))
RUNTIME_URL = os.getenv("FISH_SPEECH_RUNTIME_URL", "").strip()
RUNTIME_HEALTH_PATH = os.getenv("FISH_SPEECH_RUNTIME_HEALTH_PATH", "/v1/health")
RUNTIME_TTS_PATH = os.getenv("FISH_SPEECH_RUNTIME_TTS_PATH", "/v1/tts")
REQUEST_TIMEOUT_SEC = float(os.getenv("FISH_SPEECH_TIMEOUT_SEC", "300"))

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s [fish_speech] %(message)s")
log = logging.getLogger("fish_speech")


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1)
    speaker_name: str | None = None
    reference_text: str | None = None
    output_format: str = "wav"
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_options(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        raw_options = payload.get("options")
        if isinstance(raw_options, str):
            try:
                payload["options"] = json.loads(raw_options)
            except json.JSONDecodeError as exc:
                raise ValueError("options must be valid JSON") from exc
        elif raw_options is None:
            payload["options"] = {}
        return payload


async def _read_upload(upload: UploadFile | None) -> bytes | None:
    if upload is None or not upload.filename:
        return None
    try:
        content = await upload.read()
    finally:
        await upload.close()
    return content or None


def _runtime_required() -> str:
    if not RUNTIME_URL:
        raise HTTPException(503, "FISH_SPEECH_RUNTIME_URL is not configured.")
    return RUNTIME_URL.rstrip("/")


def _output_extension(output_format: str, content_type: str | None) -> str:
    normalized = (output_format or "").strip().lower()
    if normalized in {"wav", "mp3", "opus", "pcm"}:
        return normalized
    guessed = mimetypes.guess_extension(content_type or "")
    if guessed:
        return guessed.lstrip(".")
    return "wav"


def _relative_output_path(path: Path) -> str:
    return str(path.resolve().relative_to(OUTPUTS_ROOT.resolve()))


async def parse_synthesize_request(request: Request) -> tuple[SynthesizeRequest, bytes | None]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        payload = SynthesizeRequest.model_validate(
            {
                "text": str(form.get("text") or ""),
                "speaker_name": str(form.get("speaker_name") or "") or None,
                "reference_text": str(form.get("reference_text") or "") or None,
                "output_format": str(form.get("output_format") or "wav"),
                "options": str(form.get("options") or "") or None,
            }
        )
        reference_audio = form.get("reference_audio")
        return payload, await _read_upload(reference_audio if isinstance(reference_audio, UploadFile) else None)

    try:
        payload = SynthesizeRequest.model_validate(await request.json())
    except Exception as exc:
        raise HTTPException(400, f"Invalid Fish Speech request: {exc}") from exc
    return payload, None


async def _check_runtime_health() -> dict[str, Any]:
    runtime_url = _runtime_required()
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        response = await client.get(f"{runtime_url}{RUNTIME_HEALTH_PATH}")
        response.raise_for_status()
        return response.json() if response.content else {"status": "ok"}


app = FastAPI(title="Fish Speech Adapter")


@app.get("/healthz")
async def healthz():
    return {"status": "alive", "runtime_configured": bool(RUNTIME_URL)}


@app.get("/readyz")
async def readyz():
    runtime_url = _runtime_required()
    try:
        payload = await _check_runtime_health()
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"Fish Speech runtime is unavailable: {exc}") from exc
    return {"status": "ready", "runtime_url": runtime_url, "runtime": payload}


@app.post("/synthesize")
async def synthesize(request: Request):
    runtime_url = _runtime_required()
    payload, reference_audio = await parse_synthesize_request(request)

    runtime_payload: dict[str, Any] = {
        "text": payload.text,
        "format": payload.output_format,
    }

    if reference_audio:
        runtime_payload["references"] = [
            {
                "audio": base64.b64encode(reference_audio).decode("ascii"),
                "text": payload.reference_text or "",
            }
        ]
    elif payload.speaker_name:
        runtime_payload["reference_id"] = payload.speaker_name

    options = payload.options
    if "chunk_length" in options:
        runtime_payload["chunk_length"] = int(options["chunk_length"])
    if "latency" in options:
        runtime_payload["latency"] = str(options["latency"])
    if "seed" in options and options["seed"] not in ("", None):
        runtime_payload["seed"] = int(options["seed"])
    if "normalize" in options:
        runtime_payload["normalize"] = bool(options["normalize"])

    started = time.time()
    async with httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT_SEC, connect=10.0)) as client:
        response = await client.post(f"{runtime_url}{RUNTIME_TTS_PATH}", json=runtime_payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip() or str(exc)
            raise HTTPException(exc.response.status_code, f"Fish Speech runtime error: {detail}") from exc

    extension = _output_extension(payload.output_format, response.headers.get("content-type"))
    out_path = OUTPUT_DIR / f"{uuid.uuid4()}.{extension}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(response.content)

    return {
        "output_path": _relative_output_path(out_path),
        "output_format": payload.output_format,
        "processing_time": round(time.time() - started, 2),
        "runtime": "fish_speech",
    }
