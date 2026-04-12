from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import unicodedata
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

from .chunker import chunk_script
from .models import ModelRegistry
from .profiles import get_profile, host_path_to_container

HOST_OUTPUTS_ROOT = Path("/srv/ai/outputs")
CONTAINER_OUTPUTS_ROOT = Path(os.getenv("OUTPUTS_ROOT", "/outputs"))
VOICEOVER_RELATIVE_DIR = Path("voiceover")
VOX_MODEL_ID = "voxcpm2"
VOX_MAX_CHARS = 120
VOX_TARGET_SENTENCES_PER_CHUNK = 1


def _container_output_dir(job_id: str) -> Path:
    return CONTAINER_OUTPUTS_ROOT / VOICEOVER_RELATIVE_DIR / job_id


def _host_output_dir(job_id: str) -> Path:
    return HOST_OUTPUTS_ROOT / VOICEOVER_RELATIVE_DIR / job_id


def _host_output_path_to_container(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.exists():
        return path

    try:
        relative = path.relative_to(HOST_OUTPUTS_ROOT)
    except ValueError:
        return path

    return CONTAINER_OUTPUTS_ROOT / relative


async def _update_job(redis_client, job_key: str, **fields: Any) -> None:
    if redis_client is None:
        return

    payload = {
        key: "" if value is None else str(value)
        for key, value in fields.items()
    }
    if payload:
        await redis_client.hset(job_key, mapping=payload)


def _sanitize_filename_part(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9]+", "_", ascii_value.lower()).strip("_")
    return cleaned or fallback


def _output_filename_timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d_%H%M%S")


def _build_output_basename(model_id: str, profile_name: str) -> str:
    safe_model = _sanitize_filename_part(model_id, "voiceover")
    safe_profile = _sanitize_filename_part(profile_name, "voice")
    timestamp = _output_filename_timestamp()
    return f"{safe_model}_{safe_profile}_{timestamp}"


def _build_output_filename(output_basename: str, extension: str) -> str:
    normalized_extension = extension.lower().lstrip(".") or "wav"
    return f"{output_basename}.{normalized_extension}"


def _read_wave_params(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as wav_file:
        return wav_file.getnchannels(), wav_file.getsampwidth(), wav_file.getframerate()


def _write_silence_wav(path: Path, *, channels: int, sample_width: int, sample_rate: int, duration_ms: int) -> None:
    frame_count = int(sample_rate * (duration_ms / 1000.0))
    silence_frame = b"\x00" * sample_width * channels
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(silence_frame * frame_count)


def _merge_wavs_python(paths: list[Path], destination: Path) -> None:
    if not paths:
        raise RuntimeError("No chunk audio available to stitch")

    channels, sample_width, sample_rate = _read_wave_params(paths[0])

    with wave.open(str(destination), "wb") as output_file:
        output_file.setnchannels(channels)
        output_file.setsampwidth(sample_width)
        output_file.setframerate(sample_rate)

        for path in paths:
            with wave.open(str(path), "rb") as input_file:
                params = (
                    input_file.getnchannels(),
                    input_file.getsampwidth(),
                    input_file.getframerate(),
                )
                if params != (channels, sample_width, sample_rate):
                    raise RuntimeError("Chunk WAV parameters do not match and cannot be merged safely")
                output_file.writeframes(input_file.readframes(input_file.getnframes()))


def _build_sequence_files(chunks: list[dict], rendered_chunks: list[Path], work_dir: Path) -> list[Path]:
    if not rendered_chunks:
        raise RuntimeError("No synthesized chunks were produced")

    channels, sample_width, sample_rate = _read_wave_params(rendered_chunks[0])
    sequence: list[Path] = []
    rendered_iter = iter(rendered_chunks)
    pause_index = 0

    for chunk in chunks:
        if chunk.get("is_pause"):
            pause_index += 1
            pause_path = work_dir / f"pause_{pause_index:04d}.wav"
            _write_silence_wav(
                pause_path,
                channels=channels,
                sample_width=sample_width,
                sample_rate=sample_rate,
                duration_ms=int(chunk.get("pause_ms", 0)),
            )
            sequence.append(pause_path)
            continue

        sequence.append(next(rendered_iter))

    return sequence


def _stitch_with_sox(sequence_files: list[Path], merged_path: Path, final_wav_path: Path) -> bool:
    if shutil.which("sox") is None:
        return False

    try:
        subprocess.run(
            ["sox", *[str(path) for path in sequence_files], str(merged_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["sox", str(merged_path), str(final_wav_path), "norm", "-3"],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _convert_to_mp3_if_possible(final_wav_path: Path, final_mp3_path: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        return final_wav_path

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(final_wav_path),
                "-codec:a",
                "libmp3lame",
                "-qscale:a",
                "2",
                str(final_mp3_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        final_wav_path.unlink(missing_ok=True)
        return final_mp3_path
    except (OSError, subprocess.CalledProcessError):
        return final_wav_path


async def run_voiceover_job(job_id, profile_id, script, model_id, output_format, speed, redis_client) -> None:
    job_key = f"voiceover:{job_id}"
    completed_chunks = 0

    try:
        profile = get_profile(profile_id)
        if profile is None:
            await _update_job(redis_client, job_key, status="failed", error="Voice profile not found")
            return

        model = ModelRegistry.get_model(model_id)
        if model is None:
            await _update_job(redis_client, job_key, status="failed", error="Requested model was not found")
            return

        chunk_options: dict[str, int] = {}
        if model_id == VOX_MODEL_ID:
            chunk_options = {
                "max_chars": VOX_MAX_CHARS,
                "target_sentences_per_chunk": VOX_TARGET_SENTENCES_PER_CHUNK,
            }

        chunks = chunk_script(script, **chunk_options)
        synthesis_chunks = [chunk for chunk in chunks if not chunk.get("is_pause")]
        total_chunks = len(synthesis_chunks)
        if total_chunks == 0:
            await _update_job(redis_client, job_key, status="failed", error="Script did not produce any synthesis chunks")
            return

        work_dir = _container_output_dir(str(job_id))
        work_dir.mkdir(parents=True, exist_ok=True)

        await _update_job(
            redis_client,
            job_key,
            status="processing",
            total_chunks=total_chunks,
            completed_chunks=0,
            error="",
        )

        rendered_chunk_paths: list[Path] = []
        reference_audio_path = profile.reference_audio_path
        model_options = {"speed": speed}

        for chunk_index, chunk in enumerate(chunks, start=1):
            if chunk.get("is_pause"):
                await asyncio.sleep(int(chunk.get("pause_ms", 0)) / 1000.0)
                continue

            try:
                wav_bytes = await asyncio.to_thread(
                    model.synthesize,
                    str(chunk.get("text", "")),
                    reference_audio_path,
                    model_options,
                )
            except Exception as exc:
                await _update_job(
                    redis_client,
                    job_key,
                    status="failed",
                    error=str(exc),
                    completed_chunks=completed_chunks,
                    total_chunks=total_chunks,
                )
                return

            chunk_path = work_dir / f"chunk_{len(rendered_chunk_paths) + 1:04d}.wav"
            chunk_path.write_bytes(wav_bytes)
            rendered_chunk_paths.append(chunk_path)
            completed_chunks += 1
            await _update_job(
                redis_client,
                job_key,
                completed_chunks=completed_chunks,
                total_chunks=total_chunks,
            )

        await _update_job(redis_client, job_key, status="stitching")

        sequence_files = _build_sequence_files(chunks, rendered_chunk_paths, work_dir)
        merged_path = work_dir / "merged.wav"
        output_basename = _build_output_basename(model_id, profile.name)
        final_wav_path = work_dir / _build_output_filename(output_basename, "wav")
        final_mp3_path = work_dir / _build_output_filename(output_basename, "mp3")

        stitched_with_sox = await asyncio.to_thread(_stitch_with_sox, sequence_files, merged_path, final_wav_path)
        if not stitched_with_sox:
            await asyncio.to_thread(_merge_wavs_python, sequence_files, merged_path)
            shutil.copyfile(merged_path, final_wav_path)

        final_output_path = final_wav_path
        if str(output_format).lower() == "mp3":
            final_output_path = await asyncio.to_thread(_convert_to_mp3_if_possible, final_wav_path, final_mp3_path)

        merged_path.unlink(missing_ok=True)

        host_output_path = _host_output_dir(str(job_id)) / final_output_path.name
        await _update_job(
            redis_client,
            job_key,
            status="done",
            output_path=str(host_output_path),
            completed_chunks=total_chunks,
            total_chunks=total_chunks,
            error="",
        )

        for path in work_dir.glob("chunk_*.wav"):
            path.unlink(missing_ok=True)
        for path in work_dir.glob("pause_*.wav"):
            path.unlink(missing_ok=True)
        merged_path.unlink(missing_ok=True)
    except Exception as exc:
        await _update_job(redis_client, job_key, status="failed", error=str(exc), completed_chunks=completed_chunks)
