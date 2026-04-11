"""
Premium clone TTS adapter scaffold for NeonForge.

TODO(runtime): the upstream runtime contract for this slot is intentionally left
minimal. The adapter currently supports two safe modes:
1. JSON upstream responses that already return {"output_path": "..."}.
2. Raw audio responses, which the adapter persists into /outputs and converts
   into the NeonForge JSON result shape.
"""

import logging
import mimetypes
import os
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
OUTPUTS_ROOT = Path(os.getenv("OUTPUTS_ROOT", "/outputs"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(OUTPUTS_ROOT / "tts" / "premium_clone_tts")))
UPSTREAM_URL = os.getenv("PREMIUM_CLONE_TTS_UPSTREAM_URL", "").strip()
UPSTREAM_HEALTH_PATH = os.getenv("PREMIUM_CLONE_TTS_HEALTH_PATH", "/healthz")
UPSTREAM_SYNTH_PATH = os.getenv("PREMIUM_CLONE_TTS_SYNTH_PATH", "/synthesize")
REQUEST_TIMEOUT_SEC = float(os.getenv("PREMIUM_CLONE_TTS_TIMEOUT_SEC", "300"))

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s [premium_clone_tts] %(message)s")
log = logging.getLogger("premium_clone_tts")

app = FastAPI(title="Premium Clone TTS Adapter")


def _upstream_required() -> str:
    if not UPSTREAM_URL:
        raise HTTPException(
            503,
            "PREMIUM_CLONE_TTS_UPSTREAM_URL is not configured. "
            "TODO: point this provider slot at a runtime that can write shared outputs "
            "or return raw audio bytes.",
        )
    return UPSTREAM_URL.rstrip("/")


def _relative_output_path(path: Path) -> str:
    return str(path.resolve().relative_to(OUTPUTS_ROOT.resolve()))


def _extension_from_content_type(content_type: str | None) -> str:
    guessed = mimetypes.guess_extension(content_type or "")
    if guessed:
        return guessed.lstrip(".")
    return "wav"


@app.get("/healthz")
async def healthz():
    return {"status": "alive", "upstream_configured": bool(UPSTREAM_URL)}


@app.get("/readyz")
async def readyz():
    upstream = _upstream_required()
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        try:
            response = await client.get(f"{upstream}{UPSTREAM_HEALTH_PATH}")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"Premium clone upstream is unavailable: {exc}") from exc
    return {"status": "ready", "upstream_url": upstream}


@app.post("/synthesize")
async def synthesize(request: Request):
    upstream = _upstream_required()
    content_type = request.headers.get("content-type", "application/json")
    body = await request.body()
    started = time.time()

    async with httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT_SEC, connect=10.0)) as client:
        response = await client.post(
            f"{upstream}{UPSTREAM_SYNTH_PATH}",
            content=body,
            headers={"content-type": content_type},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip() or str(exc)
            raise HTTPException(exc.response.status_code, f"Premium clone upstream error: {detail}") from exc

    response_content_type = response.headers.get("content-type", "")
    if "application/json" in response_content_type:
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(502, "Premium clone upstream returned a non-object JSON payload.")
        payload.setdefault("processing_time", round(time.time() - started, 2))
        return JSONResponse(payload)

    if response_content_type.startswith("audio/") or response_content_type == "application/octet-stream":
        extension = _extension_from_content_type(response_content_type)
        out_path = OUTPUT_DIR / f"{uuid.uuid4()}.{extension}"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(response.content)
        return {
            "output_path": _relative_output_path(out_path),
            "processing_time": round(time.time() - started, 2),
            "runtime": "premium_clone_tts",
        }

    raise HTTPException(
        502,
        "Premium clone upstream returned an unsupported response type. "
        "TODO: extend the adapter once the runtime contract is finalized.",
    )
