"""
Faster-Whisper transcription service.

Tier: always-on
GPU weight: light (~1-2 GB for medium model)
"""

import io
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "medium")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "5"))
MODEL_DIR = os.getenv("WHISPER_MODEL_DIR", "/models/whisper")

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("whisper")

model = None
model_loaded = False


def load_model():
    global model, model_loaded
    from faster_whisper import WhisperModel
    log.info("Loading faster-whisper model: %s (compute=%s)", MODEL_SIZE, COMPUTE_TYPE)
    t0 = time.time()
    model = WhisperModel(
        MODEL_SIZE,
        device="cuda",
        compute_type=COMPUTE_TYPE,
        download_root=MODEL_DIR,
    )
    elapsed = time.time() - t0
    log.info("Model loaded in %.1fs", elapsed)
    model_loaded = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_model()
    yield
    log.info("Shutting down whisper service")


app = FastAPI(title="Faster-Whisper Service", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "alive"}


@app.get("/readyz")
async def readyz():
    if not model_loaded:
        raise HTTPException(503, "Model not loaded")
    return {"status": "ready", "model": MODEL_SIZE}


@app.get("/smoke")
async def smoke():
    """Lightweight smoke test: transcribe 1 second of silence."""
    if not model_loaded:
        raise HTTPException(503, "Model not loaded")
    import numpy as np
    import tempfile
    import soundfile as sf

    # Generate 1s of near-silence (tiny noise to avoid empty result)
    sr = 16000
    samples = np.random.randn(sr).astype(np.float32) * 0.001
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
        sf.write(f.name, samples, sr)
        segments, info = model.transcribe(f.name, beam_size=1, vad_filter=False)
        _ = list(segments)  # consume generator

    return {
        "status": "ok",
        "model": MODEL_SIZE,
        "language_detected": info.language,
        "duration_processed": round(info.duration, 2),
    }


class TranscriptionResult(BaseModel):
    text: str
    language: str
    duration: float
    segments: list


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    if not model_loaded:
        raise HTTPException(503, "Model not loaded")

    content = await audio.read()
    if len(content) == 0:
        raise HTTPException(400, "Empty audio file")

    # Write to temp file (faster-whisper needs file path)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
        f.write(content)
        f.flush()

        t0 = time.time()
        segments_gen, info = model.transcribe(
            f.name,
            beam_size=BEAM_SIZE,
            vad_filter=True,
        )
        segments = []
        full_text_parts = []
        for seg in segments_gen:
            segments.append({
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": seg.text.strip(),
            })
            full_text_parts.append(seg.text.strip())
        elapsed = time.time() - t0

    log.info(
        "Transcribed %.1fs audio in %.1fs (%.1fx realtime)",
        info.duration, elapsed, info.duration / max(elapsed, 0.01),
    )

    return {
        "text": " ".join(full_text_parts),
        "language": info.language,
        "duration": round(info.duration, 2),
        "processing_time": round(elapsed, 2),
        "segments": segments,
    }
