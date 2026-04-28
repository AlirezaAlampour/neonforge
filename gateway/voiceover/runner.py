from __future__ import annotations

import asyncio
import audioop
import json
import logging
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
FISH_MODEL_ID = "fish_speech"
VOX_MODEL_ID = "voxcpm2"
VOX_MODE_DESIGN = "design"
VOX_MODE_CLONE = "clone"
VOX_MODE_CONTINUATION = "continuation"
VOX_MAX_CHARS = 650
VOX_TARGET_SENTENCES_PER_CHUNK = 4
VOX_SINGLE_PASS_MAX_CHARS = 1200
VOX_CONTINUATION_SINGLE_PASS_MAX_CHARS = 1800
VOX_SILENCE_WINDOW_MS = 10
VOX_HEAD_GUARD_MS = 20
VOX_TAIL_GUARD_MS = 60
VOX_EDGE_FADE_MS = 8
VOX_CROSSFADE_MS = 12
VOX_MIN_CLIP_MS = 150
VOX_SILENCE_THRESHOLD_RATIO = 0.008
log = logging.getLogger("voiceover.runner")


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


def _sanitize_filename_part(value: str, fallback: str, *, max_length: int = 48) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9]+", "_", ascii_value.lower()).strip("_")
    cleaned = cleaned or fallback
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[:max_length].rstrip("_") or fallback


def _script_slug(script: str, *, max_words: int = 8, max_length: int = 72) -> str:
    normalized = unicodedata.normalize("NFKD", script)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    words = re.findall(r"[a-z0-9]+", ascii_value)
    if not words:
        return "untitled"
    return _sanitize_filename_part(" ".join(words[:max_words]), "untitled", max_length=max_length)


def _output_filename_timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d_%H%M%S")


def _build_output_basename(model_id: str, profile_name: str, script: str) -> str:
    safe_model = _sanitize_filename_part(model_id, "voiceover", max_length=28)
    safe_profile = _sanitize_filename_part(profile_name, "voice", max_length=28)
    safe_script = _script_slug(script)
    timestamp = _output_filename_timestamp()
    return f"{safe_model}_{safe_profile}_{safe_script}_{timestamp}"


def _build_output_filename(output_basename: str, extension: str) -> str:
    normalized_extension = extension.lower().lstrip(".") or "wav"
    return f"{output_basename}.{normalized_extension}"


def _single_chunk(text: str) -> list[dict]:
    return [{"text": text.strip(), "pause_ms": 0, "is_pause": False, "soft_split": False}]


def _build_voiceover_chunks(script: str, *, model_id: str, vox_mode: str | None = None) -> list[dict]:
    if model_id != VOX_MODEL_ID:
        return chunk_script(script)

    normalized = script.strip()
    if not normalized:
        return []

    single_pass_limit = (
        VOX_CONTINUATION_SINGLE_PASS_MAX_CHARS
        if vox_mode == VOX_MODE_CONTINUATION
        else VOX_SINGLE_PASS_MAX_CHARS
    )
    if len(normalized) <= single_pass_limit:
        return _single_chunk(normalized)

    return chunk_script(
        script,
        max_chars=VOX_MAX_CHARS,
        target_sentences_per_chunk=VOX_TARGET_SENTENCES_PER_CHUNK,
    )


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


def _write_wav_frames(path: Path, *, channels: int, sample_width: int, sample_rate: int, frames: bytes) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)


def _read_wav_frames(path: Path) -> tuple[tuple[int, int, int], bytes]:
    with wave.open(str(path), "rb") as wav_file:
        params = (wav_file.getnchannels(), wav_file.getsampwidth(), wav_file.getframerate())
        frames = wav_file.readframes(wav_file.getnframes())
    return params, frames


def _pcm_frame_width(channels: int, sample_width: int) -> int:
    return channels * sample_width


def _pcm_frames_for_ms(sample_rate: int, duration_ms: int) -> int:
    if duration_ms <= 0:
        return 0
    return max(1, int(round(sample_rate * (duration_ms / 1000.0))))


def _silence_pcm(channels: int, sample_width: int, sample_rate: int, duration_ms: int) -> bytes:
    frame_count = int(sample_rate * (duration_ms / 1000.0))
    silence_frame = b"\x00" * sample_width * channels
    return silence_frame * frame_count


def _vox_silence_threshold(sample_width: int) -> int:
    max_amplitude = max(1, (1 << (8 * sample_width - 1)) - 1)
    return max(64, int(max_amplitude * VOX_SILENCE_THRESHOLD_RATIO))


def _vox_find_speech_bounds(frames: bytes, *, channels: int, sample_width: int, sample_rate: int) -> tuple[int, int]:
    frame_width = _pcm_frame_width(channels, sample_width)
    total_frames = len(frames) // frame_width
    if total_frames <= 0:
        return 0, 0

    window_frames = min(total_frames, _pcm_frames_for_ms(sample_rate, VOX_SILENCE_WINDOW_MS))
    threshold = _vox_silence_threshold(sample_width)

    first_speech_frame: int | None = None
    for start_frame in range(0, total_frames, window_frames):
        end_frame = min(total_frames, start_frame + window_frames)
        fragment = frames[start_frame * frame_width:end_frame * frame_width]
        if audioop.rms(fragment, sample_width) > threshold:
            first_speech_frame = start_frame
            break

    if first_speech_frame is None:
        return 0, total_frames

    last_speech_frame = total_frames
    for start_frame in range(max(0, total_frames - window_frames), -1, -window_frames):
        end_frame = min(total_frames, start_frame + window_frames)
        fragment = frames[start_frame * frame_width:end_frame * frame_width]
        if audioop.rms(fragment, sample_width) > threshold:
            last_speech_frame = end_frame
            break

    start_frame = max(0, first_speech_frame - _pcm_frames_for_ms(sample_rate, VOX_HEAD_GUARD_MS))
    end_frame = min(total_frames, last_speech_frame + _pcm_frames_for_ms(sample_rate, VOX_TAIL_GUARD_MS))

    if end_frame - start_frame < _pcm_frames_for_ms(sample_rate, VOX_MIN_CLIP_MS):
        return 0, total_frames

    return start_frame, end_frame


def _apply_edge_fade(frames: bytes, *, channels: int, sample_width: int, sample_rate: int) -> bytes:
    frame_width = _pcm_frame_width(channels, sample_width)
    total_frames = len(frames) // frame_width
    fade_frames = min(total_frames // 2, _pcm_frames_for_ms(sample_rate, VOX_EDGE_FADE_MS))
    if fade_frames <= 0:
        return frames

    output = bytearray(len(frames))
    for frame_index in range(total_frames):
        start = frame_index * frame_width
        end = start + frame_width
        frame = frames[start:end]

        gain = 1.0
        if frame_index < fade_frames:
            gain = min(gain, (frame_index + 1) / fade_frames)

        frames_to_end = total_frames - frame_index
        if frames_to_end <= fade_frames:
            gain = min(gain, frames_to_end / fade_frames)

        if gain < 0.999:
            frame = audioop.mul(frame, sample_width, gain)

        output[start:end] = frame

    return bytes(output)


def _append_crossfaded(assembled: bytearray, next_frames: bytes, *, channels: int, sample_width: int, sample_rate: int) -> None:
    frame_width = _pcm_frame_width(channels, sample_width)
    if not assembled or not next_frames:
        assembled.extend(next_frames)
        return

    overlap_frames = min(
        _pcm_frames_for_ms(sample_rate, VOX_CROSSFADE_MS),
        len(assembled) // frame_width,
        len(next_frames) // frame_width,
    )
    if overlap_frames <= 0:
        assembled.extend(next_frames)
        return

    overlap_bytes = overlap_frames * frame_width
    left_overlap = bytes(assembled[-overlap_bytes:])
    right_overlap = next_frames[:overlap_bytes]
    mixed_overlap = bytearray(overlap_bytes)

    for frame_index in range(overlap_frames):
        start = frame_index * frame_width
        end = start + frame_width
        left_gain = (overlap_frames - frame_index) / (overlap_frames + 1)
        right_gain = (frame_index + 1) / (overlap_frames + 1)
        left_frame = audioop.mul(left_overlap[start:end], sample_width, left_gain)
        right_frame = audioop.mul(right_overlap[start:end], sample_width, right_gain)
        mixed_overlap[start:end] = audioop.add(left_frame, right_frame, sample_width)

    assembled[-overlap_bytes:] = mixed_overlap
    assembled.extend(next_frames[overlap_bytes:])


def _prepare_vox_chunk(path: Path, expected_params: tuple[int, int, int]) -> bytes:
    params, frames = _read_wav_frames(path)
    if params != expected_params:
        raise RuntimeError("Chunk WAV parameters do not match and cannot be merged safely")

    channels, sample_width, sample_rate = params
    start_frame, end_frame = _vox_find_speech_bounds(
        frames,
        channels=channels,
        sample_width=sample_width,
        sample_rate=sample_rate,
    )
    frame_width = _pcm_frame_width(channels, sample_width)
    trimmed_frames = frames[start_frame * frame_width:end_frame * frame_width] or frames
    return _apply_edge_fade(
        trimmed_frames,
        channels=channels,
        sample_width=sample_width,
        sample_rate=sample_rate,
    )


def _stitch_vox_wavs(chunks: list[dict], rendered_chunks: list[Path], destination: Path) -> None:
    if not rendered_chunks:
        raise RuntimeError("No chunk audio available to stitch")

    channels, sample_width, sample_rate = _read_wave_params(rendered_chunks[0])
    expected_params = (channels, sample_width, sample_rate)
    rendered_iter = iter(rendered_chunks)
    assembled = bytearray()
    previous_was_audio = False

    for chunk in chunks:
        if chunk.get("is_pause"):
            assembled.extend(
                _silence_pcm(
                    channels=channels,
                    sample_width=sample_width,
                    sample_rate=sample_rate,
                    duration_ms=int(chunk.get("pause_ms", 0)),
                )
            )
            previous_was_audio = False
            continue

        prepared_frames = _prepare_vox_chunk(next(rendered_iter), expected_params)
        if previous_was_audio:
            _append_crossfaded(
                assembled,
                prepared_frames,
                channels=channels,
                sample_width=sample_width,
                sample_rate=sample_rate,
            )
        else:
            assembled.extend(prepared_frames)
        previous_was_audio = True

    _write_wav_frames(
        destination,
        channels=channels,
        sample_width=sample_width,
        sample_rate=sample_rate,
        frames=bytes(assembled),
    )


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


def _wav_duration_seconds(path: Path) -> float | None:
    if path.suffix.lower() != ".wav":
        return None

    try:
        with wave.open(str(path), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
        return frames / float(rate) if rate else None
    except Exception:
        return None


def _model_provider(model_id: str) -> str:
    if model_id in {"f5tts", "fish_speech", VOX_MODEL_ID}:
        return "local"
    return "unknown"


def _generation_params(model_id: str, output_format: str, speed: float) -> dict[str, Any]:
    params: dict[str, Any] = {"format": str(output_format).lower()}
    if model_id == "f5tts":
        params["speed"] = speed
    return params


def _reference_source_type(profile: Any | None, reference_audio_path: str | None, reference_label: str | None) -> str:
    if reference_audio_path:
        if (reference_label or "").strip().lower() == "recorded reference":
            return "temp_recording"
        return "upload"
    if profile is not None:
        return "saved_profile"
    return "none"


def _reference_transcript(profile: Any | None, prompt_text: str | None) -> str | None:
    cleaned_prompt_text = (prompt_text or "").strip()
    if cleaned_prompt_text:
        return cleaned_prompt_text

    profile_transcript = str(getattr(profile, "reference_transcript", "") or "").strip()
    return profile_transcript or None


def _write_output_metadata(
    final_output_path: Path,
    *,
    created_at: str,
    model_id: str,
    voice_mode: str,
    profile: Any | None,
    reference_audio_path: str | None,
    reference_label: str | None,
    script: str,
    prompt_text: str | None,
    output_format: str,
    speed: float,
    chunk_count: int,
    duration_seconds: float | None,
) -> Path:
    metadata = {
        "output_filename": final_output_path.name,
        "created_at": created_at,
        "model_id": model_id,
        "provider": _model_provider(model_id),
        "voice_mode": voice_mode,
        "voice_profile_id": getattr(profile, "id", None),
        "voice_profile_name": getattr(profile, "name", None),
        "reference_source_type": _reference_source_type(profile, reference_audio_path, reference_label),
        "script_text": script,
        "reference_transcript": _reference_transcript(profile, prompt_text),
        "generation_params": _generation_params(model_id, output_format, speed),
        "chunk_count": chunk_count,
        "duration_seconds": round(duration_seconds, 3) if duration_seconds is not None else None,
    }
    metadata_path = final_output_path.with_suffix(".json")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata_path


async def run_voiceover_job(
    job_id,
    profile_id,
    script,
    model_id,
    output_format,
    speed,
    redis_client,
    vox_mode: str | None = None,
    prompt_text: str | None = None,
    style_text: str | None = None,
    reference_audio_path: str | None = None,
    reference_label: str | None = None,
) -> None:
    job_key = f"voiceover:{job_id}"
    completed_chunks = 0

    try:
        profile = get_profile(profile_id) if profile_id else None
        if profile_id and profile is None:
            await _update_job(redis_client, job_key, status="failed", error="Voice profile not found")
            return

        model = ModelRegistry.get_model(model_id)
        if model is None:
            await _update_job(redis_client, job_key, status="failed", error="Requested model was not found")
            return

        effective_vox_mode = (vox_mode or VOX_MODE_CLONE).strip().lower() or VOX_MODE_CLONE
        if model_id != VOX_MODEL_ID:
            effective_vox_mode = VOX_MODE_CLONE

        chunks = _build_voiceover_chunks(script, model_id=model_id, vox_mode=effective_vox_mode)
        synthesis_chunks = [chunk for chunk in chunks if not chunk.get("is_pause")]
        total_chunks = len(synthesis_chunks)
        if model_id == VOX_MODEL_ID:
            log.info(
                "Vox job %s: vox_mode=%s single_pass=%s chunk_count=%s continuation=%s",
                job_id,
                effective_vox_mode,
                len(chunks) == 1 and total_chunks == 1,
                total_chunks,
                effective_vox_mode == VOX_MODE_CONTINUATION,
            )
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
        resolved_reference_audio_path = reference_audio_path or (profile.reference_audio_path if profile else None)
        model_options: dict[str, Any] = {}
        if model_id == "f5tts":
            model_options["speed"] = speed
        if model_id == FISH_MODEL_ID:
            if profile is None:
                await _update_job(redis_client, job_key, status="failed", error="Voice profile not found")
                return
            model_options["voice_profile_id"] = profile.id
        elif model_id == VOX_MODEL_ID:
            if effective_vox_mode == VOX_MODE_DESIGN:
                resolved_reference_audio_path = None
            model_options["vox_mode"] = effective_vox_mode

            cleaned_prompt_text = (prompt_text or "").strip()
            if effective_vox_mode == VOX_MODE_CONTINUATION and cleaned_prompt_text:
                model_options["prompt_text"] = cleaned_prompt_text

            cleaned_style_text = (style_text or "").strip()
            if effective_vox_mode != VOX_MODE_CONTINUATION and cleaned_style_text:
                model_options["style_text"] = cleaned_style_text

        for chunk_index, chunk in enumerate(chunks, start=1):
            if chunk.get("is_pause"):
                await asyncio.sleep(int(chunk.get("pause_ms", 0)) / 1000.0)
                continue

            try:
                wav_bytes = await asyncio.to_thread(
                    model.synthesize,
                    str(chunk.get("text", "")),
                    resolved_reference_audio_path,
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

        merged_path = work_dir / "merged.wav"
        output_basename = _build_output_basename(model_id, reference_label or (profile.name if profile else "voice_design"), script)
        final_wav_path = work_dir / _build_output_filename(output_basename, "wav")
        final_mp3_path = work_dir / _build_output_filename(output_basename, "mp3")

        if model_id == VOX_MODEL_ID:
            await asyncio.to_thread(_stitch_vox_wavs, chunks, rendered_chunk_paths, final_wav_path)
        else:
            sequence_files = _build_sequence_files(chunks, rendered_chunk_paths, work_dir)
            stitched_with_sox = await asyncio.to_thread(_stitch_with_sox, sequence_files, merged_path, final_wav_path)
            if not stitched_with_sox:
                await asyncio.to_thread(_merge_wavs_python, sequence_files, merged_path)
                shutil.copyfile(merged_path, final_wav_path)

        final_output_path = final_wav_path
        duration_seconds = _wav_duration_seconds(final_wav_path)
        if str(output_format).lower() == "mp3":
            final_output_path = await asyncio.to_thread(_convert_to_mp3_if_possible, final_wav_path, final_mp3_path)

        merged_path.unlink(missing_ok=True)
        metadata_created_at = datetime.now().astimezone().isoformat()
        metadata_path = _write_output_metadata(
            final_output_path,
            created_at=metadata_created_at,
            model_id=model_id,
            voice_mode=effective_vox_mode if model_id == VOX_MODEL_ID else "clone",
            profile=profile,
            reference_audio_path=reference_audio_path,
            reference_label=reference_label,
            script=script,
            prompt_text=prompt_text,
            output_format=str(output_format).lower(),
            speed=speed,
            chunk_count=total_chunks,
            duration_seconds=duration_seconds,
        )

        host_output_path = _host_output_dir(str(job_id)) / final_output_path.name
        host_metadata_path = _host_output_dir(str(job_id)) / metadata_path.name
        await _update_job(
            redis_client,
            job_key,
            status="done",
            output_path=str(host_output_path),
            metadata_path=str(host_metadata_path),
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
