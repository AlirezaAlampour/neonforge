"""NeonForge LatentSync 1.6 inference adapter.

The upstream pipeline runs in an isolated subprocess so every completed job
returns its CUDA allocations to the operating system. Container lifetime and
warm-idle policy are owned by the NeonForge supervisor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
SOURCE_DIR = Path(os.getenv("LATENTSYNC_SOURCE_DIR", "/opt/LatentSync"))
CHECKPOINT_DIR = Path(os.getenv("LATENTSYNC_CHECKPOINT_DIR", "/models/latentsync/checkpoints"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/outputs/lipsync"))
TIMEOUT_SEC = int(os.getenv("LATENTSYNC_TIMEOUT_SEC", "1800"))

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("latentsync")
app = FastAPI(title="NeonForge Lip Sync", version="1.6")
_generation_lock = asyncio.Lock()


def _mem_available_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return round(int(line.split()[1]) / 1048576, 1)
    except OSError:
        pass
    return -1.0


def _preflight() -> tuple[bool, list[str]]:
    required = [
        SOURCE_DIR / "scripts" / "inference.py",
        SOURCE_DIR / "configs" / "unet" / "stage2_512.yaml",
        CHECKPOINT_DIR / "latentsync_unet.pt",
        CHECKPOINT_DIR / "whisper" / "tiny.pt",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    return not missing, missing


def _validate_media_probe(probe: dict, *, video: bool) -> None:
    try:
        duration = float(probe["format"]["duration"])
        if not 0 < duration <= 10:
            raise ValueError("Use a clip and audio between 0 and 10 seconds for the supported local preview.")
        if video:
            stream = next(item for item in probe["streams"] if item.get("codec_type") == "video")
            width, height = int(stream["width"]), int(stream["height"])
            numerator, denominator = map(float, stream["r_frame_rate"].split("/"))
            if not (0 < width <= 1920 and 0 < height <= 1920 and width * height <= 1920 * 1080 and 0 < numerator / denominator <= 30):
                raise ValueError("Use a source video up to 1080p landscape or portrait, at 30 fps or less.")
    except (KeyError, TypeError, StopIteration, ZeroDivisionError) as exc:
        raise HTTPException(422, "Unable to read this media. Export a valid MP4 video or WAV audio file and try again.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def _probe_media(path: Path, *, video: bool) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise HTTPException(422, "Media validation timed out. Try a shorter clip.") from exc
    if process.returncode:
        raise HTTPException(422, "This media could not be decoded. Try an MP4 video or WAV audio file.")
    _validate_media_probe(json.loads(stdout), video=video)


@app.on_event("startup")
async def startup() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/healthz")
async def healthz():
    return {"status": "alive", "backend": "latentsync-1.6", "legacy": False}


@app.get("/readyz")
async def readyz():
    available, missing = _preflight()
    return {
        "status": "idle" if available else "missing_model",
        "backend": "latentsync-1.6",
        "legacy": False,
        "available": available,
        "detail": (
            "LatentSync 1.6 is installed and loads per job. ARM64 uses CPU ONNX face detection; diffusion remains on CUDA."
            if available
            else "LatentSync 1.6 checkpoints are incomplete."
        ),
        "missing": missing,
        "model_loaded": False,
        "uma_available_gb": _mem_available_gb(),
    }


@app.post("/sync")
async def sync(
    video: UploadFile = File(...),
    audio: UploadFile = File(...),
    inference_steps: int = Form(20),
    guidance_scale: float = Form(1.5),
    seed: int = Form(1247),
):
    available, missing = _preflight()
    if not available:
        raise HTTPException(503, f"LatentSync checkpoints are missing: {', '.join(missing)}")
    if not 20 <= inference_steps <= 50:
        raise HTTPException(400, "inference_steps must be between 20 and 50")
    if not 1.0 <= guidance_scale <= 3.0:
        raise HTTPException(400, "guidance_scale must be between 1.0 and 3.0")

    run_id = str(uuid.uuid4())
    video_path = OUTPUT_DIR / f"{run_id}_input{Path(video.filename or '.mp4').suffix or '.mp4'}"
    audio_path = OUTPUT_DIR / f"{run_id}_audio{Path(audio.filename or '.wav').suffix or '.wav'}"
    output_path = OUTPUT_DIR / f"{run_id}_synced.mp4"
    temp_dir = OUTPUT_DIR / f"{run_id}_temp"

    try:
        video_path.write_bytes(await video.read())
        audio_path.write_bytes(await audio.read())
        await _probe_media(video_path, video=True)
        await _probe_media(audio_path, video=False)
        command = [
            "python",
            "-m",
            "scripts.inference",
            "--unet_config_path",
            "configs/unet/stage2_512.yaml",
            "--inference_ckpt_path",
            str(CHECKPOINT_DIR / "latentsync_unet.pt"),
            "--inference_steps",
            str(inference_steps),
            "--guidance_scale",
            str(guidance_scale),
            "--seed",
            str(seed),
            "--enable_deepcache",
            "--video_path",
            str(video_path),
            "--audio_path",
            str(audio_path),
            "--video_out_path",
            str(output_path),
            "--temp_dir",
            str(temp_dir),
        ]
        started = time.monotonic()
        memory_before = _mem_available_gb()
        async with _generation_lock:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=SOURCE_DIR,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=TIMEOUT_SEC)
            except TimeoutError:
                process.kill()
                await process.communicate()
                raise HTTPException(504, f"LatentSync exceeded the {TIMEOUT_SEC}s timeout")

        output = stdout.decode(errors="replace")
        if process.returncode != 0 or not output_path.is_file():
            log.error("LatentSync failed (exit=%s): %s", process.returncode, output[-4000:])
            raise HTTPException(500, f"LatentSync inference failed (exit {process.returncode})")

        duration = round(time.monotonic() - started, 1)
        log.info(
            "LatentSync completed in %.1fs (MemAvailable %.1f -> %.1f GB)",
            duration,
            memory_before,
            _mem_available_gb(),
        )
        return {
            "output_path": f"lipsync/{output_path.name}",
            "processing_time": duration,
            "backend": "latentsync-1.6",
            "seed": seed,
        }
    finally:
        video_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)
        shutil.rmtree(temp_dir, ignore_errors=True)
