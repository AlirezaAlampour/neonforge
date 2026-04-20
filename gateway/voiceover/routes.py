from __future__ import annotations

import importlib.util
import mimetypes
import os
import shutil
import subprocess
import tempfile
import uuid
import wave
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .models import ModelRegistry
from .profiles import (
    VoiceProfile,
    get_profile,
    host_path_to_container,
    load_registry,
    save_profile,
)
from .runner import run_voiceover_job

router = APIRouter(prefix="/api/v1/voiceover", tags=["voiceover"])

VOICEOVER_OUTPUT_EXTENSIONS = {".wav", ".mp3"}
VOICE_PROFILE_INPUT_EXTENSIONS = {".wav", ".mp3", ".m4a"}
RECORDED_VOICE_PROFILE_INPUT_EXTENSIONS = VOICE_PROFILE_INPUT_EXTENSIONS | {".webm", ".ogg", ".mp4"}
TEMP_REFERENCE_INPUT_EXTENSIONS = VOICE_PROFILE_INPUT_EXTENSIONS | {".webm", ".ogg", ".mp4"}
REFERENCE_AUDIO_EXTENSION = ".wav"
HOST_OUTPUTS_ROOT = Path("/srv/ai/outputs")
CONTAINER_OUTPUTS_ROOT = Path(os.getenv("OUTPUTS_ROOT", "/outputs"))
VOICEOVER_OUTPUTS_RELATIVE_DIR = Path("voiceover")
VOICEOVER_TEMP_REFERENCE_RELATIVE_DIR = Path("voiceover_temp_references")
VOX_MODEL_ID = "voxcpm2"
VOX_MODE_DESIGN = "design"
VOX_MODE_CLONE = "clone"
VOX_MODE_CONTINUATION = "continuation"
VALID_VOX_MODES = {VOX_MODE_DESIGN, VOX_MODE_CLONE, VOX_MODE_CONTINUATION}
BROWSER_RECORDED_PROFILE_SOURCE = "browser-recording"


class CreateVoiceoverJobRequest(BaseModel):
    voice_profile_id: str | None = None
    temp_reference_id: str | None = None
    script: str
    model_id: str
    output_format: str = "wav"
    speed: float = 1.0
    vox_mode: str | None = None
    prompt_text: str | None = None
    style_text: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _output_host_path_to_container(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.exists():
        return path

    try:
        relative = path.relative_to(HOST_OUTPUTS_ROOT)
    except ValueError:
        return path

    return CONTAINER_OUTPUTS_ROOT / relative


def _get_redis_client():
    import app as gateway_app

    return gateway_app.rdb


async def _read_job(job_id: str) -> dict[str, Any] | None:
    redis_client = _get_redis_client()
    if redis_client is None:
        return None

    payload = await redis_client.hgetall(f"voiceover:{job_id}")
    if not payload:
        return None

    data: dict[str, Any] = dict(payload)
    for key in ("total_chunks", "completed_chunks"):
        if data.get(key) not in (None, ""):
            data[key] = int(data[key])
    if data.get("output_path"):
        data["filename"] = Path(str(data["output_path"])).name
        data["output_url"] = f"/api/v1/voiceover/output/{job_id}"
    return data


def _duration_with_sox(path: Path) -> float | None:
    if shutil.which("sox") is None:
        return None

    try:
        result = subprocess.run(
            ["sox", "--info", "-D", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def _duration_with_mutagen(path: Path) -> float | None:
    if importlib.util.find_spec("mutagen") is None:
        return None

    from mutagen import File as MutagenFile

    try:
        audio = MutagenFile(str(path))
        if audio and audio.info and getattr(audio.info, "length", None) is not None:
            return float(audio.info.length)
    except Exception:
        return None

    return None


def _duration_with_wave(path: Path) -> float | None:
    if path.suffix.lower() != ".wav":
        return None

    try:
        with wave.open(str(path), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
        return frames / float(rate) if rate else None
    except Exception:
        return None


def _detect_duration(path: Path) -> float | None:
    return _duration_with_sox(path) or _duration_with_mutagen(path) or _duration_with_wave(path)


def _normalize_reference_audio_to_wav(input_path: Path, output_path: Path, *, decode_error_message: str) -> None:
    if shutil.which("ffmpeg") is None:
        raise HTTPException(503, "Reference audio normalization is unavailable because ffmpeg is not installed")

    try:
        # Keep a high-quality PCM WAV master at ingest time. If a specific
        # runtime later needs a different layout, adapt it in the model path.
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(input_path),
                "-vn",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise HTTPException(503, "Reference audio normalization is unavailable because ffmpeg could not be started") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(422, decode_error_message) from exc


def _serialize_profile(profile: VoiceProfile) -> dict[str, Any]:
    return asdict(profile)


def _voiceover_output_dir() -> Path:
    return CONTAINER_OUTPUTS_ROOT / VOICEOVER_OUTPUTS_RELATIVE_DIR


def _voiceover_temp_reference_dir() -> Path:
    path = CONTAINER_OUTPUTS_ROOT / VOICEOVER_TEMP_REFERENCE_RELATIVE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _temp_reference_path(reference_id: str) -> Path:
    cleaned_reference_id = reference_id.strip()
    if not cleaned_reference_id or Path(cleaned_reference_id).name != cleaned_reference_id:
        raise HTTPException(422, "Temporary reference id is invalid")
    return _voiceover_temp_reference_dir() / f"{cleaned_reference_id}{REFERENCE_AUDIO_EXTENSION}"


def _get_temp_reference_path(reference_id: str | None) -> Path | None:
    if not reference_id:
        return None

    candidate = _temp_reference_path(reference_id)
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def _container_output_path_to_host(path_value: str | Path) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        return HOST_OUTPUTS_ROOT / VOICEOVER_OUTPUTS_RELATIVE_DIR / path

    try:
        relative = path.relative_to(CONTAINER_OUTPUTS_ROOT)
    except ValueError:
        return path

    return HOST_OUTPUTS_ROOT / relative


def _find_voiceover_output(job_id: str) -> Path | None:
    job_dir = _voiceover_output_dir() / job_id
    if not job_dir.exists() or not job_dir.is_dir():
        return None

    candidates: list[Path] = []
    for child in job_dir.iterdir():
        if not child.is_file():
            continue
        if child.suffix.lower() not in VOICEOVER_OUTPUT_EXTENSIONS:
            continue
        if child.name == "merged.wav" or child.name.startswith("chunk_") or child.name.startswith("pause_"):
            continue
        candidates.append(child)

    if candidates:
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return candidates[0]

    return None


async def _prepare_reference_audio_upload(
    audio_file: UploadFile,
    *,
    allowed_extensions: set[str],
    unsupported_extension_message: str,
    decode_error_message: str,
) -> tuple[bytes, str]:
    extension = Path(audio_file.filename or "").suffix.lower()
    if extension not in allowed_extensions:
        raise HTTPException(422, unsupported_extension_message)

    audio_bytes = await audio_file.read()
    if not audio_bytes:
        raise HTTPException(422, "Audio file is empty")

    temp_input_path: Path | None = None
    temp_output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temp_file:
            temp_file.write(audio_bytes)
            temp_input_path = Path(temp_file.name)

        with tempfile.NamedTemporaryFile(delete=False, suffix=REFERENCE_AUDIO_EXTENSION) as temp_file:
            temp_output_path = Path(temp_file.name)

        _normalize_reference_audio_to_wav(
            temp_input_path,
            temp_output_path,
            decode_error_message=decode_error_message,
        )

        duration = _detect_duration(temp_output_path) or _detect_duration(temp_input_path)
        if duration is not None and duration > 30:
            raise HTTPException(422, "Reference audio must be 30 seconds or shorter")

        normalized_filename = f"{Path(audio_file.filename or 'reference').stem or 'reference'}{REFERENCE_AUDIO_EXTENSION}"
        return temp_output_path.read_bytes(), normalized_filename
    finally:
        if temp_input_path is not None:
            temp_input_path.unlink(missing_ok=True)
        if temp_output_path is not None:
            temp_output_path.unlink(missing_ok=True)


async def _transcribe_audio_with_whisper(audio_name: str, audio_bytes: bytes, media_type: str) -> str:
    whisper_url = os.getenv("WHISPER_URL", "http://whisper:8000").rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
            response = await client.post(
                f"{whisper_url}/transcribe",
                files={"audio": (audio_name, audio_bytes, media_type)},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise HTTPException(503, "Reference audio transcription is unavailable right now") from exc

    transcript = str(payload.get("text") or "").strip()
    if not transcript:
        raise HTTPException(422, "Reference audio could not be transcribed")
    return transcript


def _resolve_job_output_path(job_id: str, job: dict[str, Any] | None = None) -> Path | None:
    if job and job.get("output_path"):
        output_path = _output_host_path_to_container(str(job["output_path"]))
        if output_path.exists() and output_path.is_file():
            return output_path

    return _find_voiceover_output(job_id)


def _stage_temp_reference_for_job(job_id: str, temp_reference_path: Path) -> Path:
    staged_dir = _voiceover_output_dir() / job_id / "inputs"
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staged_dir / "recorded_reference.wav"
    shutil.copyfile(temp_reference_path, staged_path)
    return staged_path


def _serialize_recent_voiceover(job_id: str, output_path: Path) -> dict[str, Any]:
    created_at = datetime.fromtimestamp(output_path.stat().st_mtime, tz=timezone.utc).isoformat()
    host_output_path = _container_output_path_to_host(output_path)
    return {
        "job_id": job_id,
        "filename": output_path.name,
        "created_at": created_at,
        "output_path": str(host_output_path),
        "output_url": f"/api/v1/voiceover/output/{job_id}",
    }


@router.post("/profiles")
async def create_voice_profile(
    name: str = Form(...),
    notes: str = Form(""),
    recording_source: str = Form("upload"),
    audio_file: UploadFile = File(...),
):
    cleaned_recording_source = recording_source.strip().lower() or "upload"
    if cleaned_recording_source == BROWSER_RECORDED_PROFILE_SOURCE:
        allowed_extensions = RECORDED_VOICE_PROFILE_INPUT_EXTENSIONS
        unsupported_extension_message = "Recorded audio must be a WAV, MP3, M4A, MP4, OGG, or WebM"
        decode_error_message = "Recorded audio could not be decoded as WAV, MP3, M4A, MP4, OGG, or WebM"
    elif cleaned_recording_source == "upload":
        allowed_extensions = VOICE_PROFILE_INPUT_EXTENSIONS
        unsupported_extension_message = "Audio file must be a WAV, MP3, or M4A"
        decode_error_message = "Audio file could not be decoded as WAV, MP3, or M4A"
    else:
        raise HTTPException(422, "Recording source is invalid")

    normalized_audio_bytes, normalized_filename = await _prepare_reference_audio_upload(
        audio_file,
        allowed_extensions=allowed_extensions,
        unsupported_extension_message=unsupported_extension_message,
        decode_error_message=decode_error_message,
    )
    media_type = mimetypes.guess_type(normalized_filename)[0] or "audio/wav"
    reference_transcript: str | None = None
    try:
        reference_transcript = await _transcribe_audio_with_whisper(
            normalized_filename,
            normalized_audio_bytes,
            media_type,
        )
    except HTTPException:
        reference_transcript = None

    profile = save_profile(
        name=name,
        audio_bytes=normalized_audio_bytes,
        stored_filename=normalized_filename,
        notes=notes,
        reference_transcript=reference_transcript,
    )

    return _serialize_profile(profile)


@router.post("/temp-reference")
async def create_temp_reference(audio_file: UploadFile = File(...)):
    normalized_audio_bytes, normalized_filename = await _prepare_reference_audio_upload(
        audio_file,
        allowed_extensions=TEMP_REFERENCE_INPUT_EXTENSIONS,
        unsupported_extension_message="Audio file must be a WAV, MP3, M4A, MP4, OGG, or WebM",
        decode_error_message="Audio file could not be decoded as WAV, MP3, M4A, MP4, OGG, or WebM",
    )
    media_type = mimetypes.guess_type(normalized_filename)[0] or "audio/wav"
    transcript = await _transcribe_audio_with_whisper(normalized_filename, normalized_audio_bytes, media_type)

    temp_reference_id = str(uuid.uuid4())
    temp_reference_path = _temp_reference_path(temp_reference_id)
    temp_reference_path.write_bytes(normalized_audio_bytes)

    return {
        "temp_reference_id": temp_reference_id,
        "transcript": transcript,
    }


@router.delete("/temp-reference/{temp_reference_id}")
async def delete_temp_reference(temp_reference_id: str):
    temp_reference_path = _get_temp_reference_path(temp_reference_id)
    if temp_reference_path is None:
        raise HTTPException(404, "Temporary reference clip not found")

    temp_reference_path.unlink(missing_ok=True)
    return {"deleted": True}


@router.get("/profiles")
async def list_voice_profiles():
    profiles = sorted(load_registry(), key=lambda item: item.created_at, reverse=True)
    return [_serialize_profile(profile) for profile in profiles]


@router.delete("/profiles/{profile_id}")
async def remove_voice_profile(profile_id: str):
    from .profiles import delete_profile

    deleted = delete_profile(profile_id)
    if not deleted:
        raise HTTPException(404, "Voice profile not found")
    return {"deleted": True}


@router.get("/profiles/{profile_id}/sample")
async def voice_profile_sample(profile_id: str):
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(404, "Voice profile not found")

    audio_path = host_path_to_container(profile.reference_audio_path)
    if not audio_path.exists():
        raise HTTPException(404, "Reference audio file is missing")

    media_type = mimetypes.guess_type(audio_path.name)[0] or "audio/wav"
    return FileResponse(audio_path, filename=audio_path.name, media_type=media_type)


@router.get("/models")
async def list_voiceover_models():
    items = []
    for model in ModelRegistry.all_models():
        items.append(
            {
                "model_id": model.model_id,
                "display_name": model.display_name,
                "supports_reference_audio": model.supports_reference_audio,
                "available": model.is_available(),
            }
        )
    return items


@router.get("/outputs")
async def list_recent_voiceovers(limit: int = 25):
    clamped_limit = max(1, min(limit, 100))
    output_root = _voiceover_output_dir()
    if not output_root.exists():
        return []

    items: list[tuple[float, dict[str, Any]]] = []
    for child in output_root.iterdir():
        if not child.is_dir():
            continue

        output_path = _find_voiceover_output(child.name)
        if output_path is None:
            continue

        items.append((output_path.stat().st_mtime, _serialize_recent_voiceover(child.name, output_path)))

    items.sort(key=lambda entry: entry[0], reverse=True)
    return [item for _, item in items[:clamped_limit]]


@router.post("/jobs")
async def create_voiceover_job(request: CreateVoiceoverJobRequest, background_tasks: BackgroundTasks):
    redis_client = _get_redis_client()
    if redis_client is None:
        raise HTTPException(503, "Redis is unavailable")

    model = ModelRegistry.get_model(request.model_id)
    if model is None:
        raise HTTPException(422, "Selected TTS model does not exist")
    if not model.is_available():
        raise HTTPException(422, model.availability_error())

    is_vox_model = request.model_id == VOX_MODEL_ID
    vox_mode = str(request.vox_mode or VOX_MODE_CLONE).strip().lower() or VOX_MODE_CLONE
    if is_vox_model and vox_mode not in VALID_VOX_MODES:
        raise HTTPException(422, "Vox mode must be design, clone, or continuation")
    if request.temp_reference_id and (not is_vox_model or vox_mode != VOX_MODE_CONTINUATION):
        raise HTTPException(422, "Temporary recorded references are only supported for Vox continuation mode")

    cleaned_prompt_text = str(request.prompt_text or "").strip() or None
    cleaned_style_text = str(request.style_text or "").strip() or None

    profile: VoiceProfile | None = None
    temp_reference_path = _get_temp_reference_path(request.temp_reference_id)
    if request.temp_reference_id and temp_reference_path is None:
        raise HTTPException(422, "Recorded reference clip does not exist")

    if is_vox_model:
        if vox_mode == VOX_MODE_CLONE:
            if not request.voice_profile_id:
                raise HTTPException(422, "Vox clone mode requires a saved voice profile")

            profile = get_profile(request.voice_profile_id)
            if profile is None:
                raise HTTPException(422, "Selected voice profile does not exist")
        elif vox_mode == VOX_MODE_CONTINUATION and temp_reference_path is None:
            if not request.voice_profile_id:
                raise HTTPException(422, "Vox continuation mode requires a saved voice profile or recorded reference clip")

            profile = get_profile(request.voice_profile_id)
            if profile is None:
                raise HTTPException(422, "Selected voice profile does not exist")

        if vox_mode == VOX_MODE_CONTINUATION and not cleaned_prompt_text:
            raise HTTPException(422, "Vox continuation mode requires the exact transcript of the reference clip")
    else:
        if not request.voice_profile_id:
            raise HTTPException(422, "This model requires a saved voice profile")

        profile = get_profile(request.voice_profile_id)
        if profile is None:
            raise HTTPException(422, "Selected voice profile does not exist")

    script = request.script.strip()
    if not script:
        raise HTTPException(422, "Script must not be empty")
    if len(script) > 50000:
        raise HTTPException(422, "Script must be under 50000 characters")

    output_format = request.output_format.lower().strip() or "wav"
    if output_format not in {"wav", "mp3"}:
        raise HTTPException(422, "Output format must be wav or mp3")

    speed = float(request.speed)
    if speed < 0.8 or speed > 1.25:
        raise HTTPException(422, "Speed must be between 0.8 and 1.25")

    job_id = str(uuid.uuid4())
    staged_reference_path = _stage_temp_reference_for_job(job_id, temp_reference_path) if temp_reference_path is not None else None
    await redis_client.hset(
        f"voiceover:{job_id}",
        mapping={
            "status": "pending",
            "total_chunks": "0",
            "completed_chunks": "0",
            "error": "",
            "output_path": "",
            "created_at": _now_iso(),
        },
    )

    background_tasks.add_task(
        run_voiceover_job,
        job_id,
        profile.id if profile else None,
        script,
        request.model_id,
        output_format,
        speed,
        redis_client,
        vox_mode if is_vox_model else None,
        cleaned_prompt_text if is_vox_model else None,
        cleaned_style_text if is_vox_model else None,
        str(staged_reference_path) if staged_reference_path is not None else None,
        "Recorded Reference" if temp_reference_path is not None else None,
    )

    return {"job_id": job_id, "status": "pending"}


@router.get("/jobs/{job_id}")
async def get_voiceover_job(job_id: str):
    job = await _read_job(job_id)
    if job is None:
        raise HTTPException(404, "Voiceover job not found")
    return job


@router.delete("/output/{job_id}")
async def delete_voiceover_output(job_id: str):
    job_dir = _voiceover_output_dir() / job_id
    output_path = _find_voiceover_output(job_id)
    if output_path is None or not job_dir.exists() or not job_dir.is_dir():
        raise HTTPException(404, "Voiceover output not found")

    try:
        shutil.rmtree(job_dir)
    except OSError as exc:
        raise HTTPException(500, f"Failed to delete voiceover output: {exc}") from exc

    redis_client = _get_redis_client()
    if redis_client is not None:
        await redis_client.delete(f"voiceover:{job_id}")

    return {"deleted": True}


@router.get("/output/{job_id}")
async def stream_voiceover_output(job_id: str):
    job = await _read_job(job_id)
    output_path = _resolve_job_output_path(job_id, job)
    if output_path is None:
        if job is None:
            raise HTTPException(404, "Voiceover job not found")
        raise HTTPException(404, "Voiceover output is not ready")

    media_type = mimetypes.guess_type(output_path.name)[0] or "audio/wav"
    return FileResponse(output_path, filename=output_path.name, media_type=media_type)
