from __future__ import annotations

import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, TextIO

HOST_ASSETS_ROOT = Path("/srv/ai/assets")
CONTAINER_ASSETS_ROOT = Path(os.getenv("ASSETS_ROOT", "/app/data/assets"))
VOICE_PROFILES_RELATIVE_DIR = Path("voice_profiles")
HOST_VOICE_PROFILES_DIR = HOST_ASSETS_ROOT / VOICE_PROFILES_RELATIVE_DIR
CONTAINER_VOICE_PROFILES_DIR = CONTAINER_ASSETS_ROOT / VOICE_PROFILES_RELATIVE_DIR
REGISTRY_HOST_PATH = HOST_VOICE_PROFILES_DIR / "registry.json"
REGISTRY_CONTAINER_PATH = CONTAINER_VOICE_PROFILES_DIR / "registry.json"


@dataclass(slots=True)
class VoiceProfile:
    id: str
    name: str
    reference_audio_path: str
    created_at: str
    notes: str | None = None
    reference_transcript: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_storage_dirs() -> None:
    CONTAINER_VOICE_PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def host_path_to_container(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.exists():
        return path

    try:
        relative = path.relative_to(HOST_ASSETS_ROOT)
    except ValueError:
        return path

    return CONTAINER_ASSETS_ROOT / relative


def _deserialize_profile(payload: dict) -> VoiceProfile:
    return VoiceProfile(
        id=str(payload["id"]),
        name=str(payload["name"]),
        reference_audio_path=str(payload["reference_audio_path"]),
        created_at=str(payload["created_at"]),
        notes=str(payload["notes"]) if payload.get("notes") not in (None, "") else None,
        reference_transcript=str(payload["reference_transcript"])
        if payload.get("reference_transcript") not in (None, "")
        else None,
    )


def _write_profiles(handle: TextIO, profiles: list[VoiceProfile]) -> None:
    handle.seek(0)
    json.dump([asdict(profile) for profile in profiles], handle, indent=2)
    handle.write("\n")
    handle.truncate()
    handle.flush()
    os.fsync(handle.fileno())


@contextmanager
def _locked_registry(exclusive: bool) -> Iterator[tuple[TextIO, list[VoiceProfile]]]:
    _ensure_storage_dirs()
    REGISTRY_CONTAINER_PATH.touch(exist_ok=True)

    with REGISTRY_CONTAINER_PATH.open("r+", encoding="utf-8") as handle:
        lock_mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(handle.fileno(), lock_mode)
        try:
            raw = handle.read().strip()
            payload = json.loads(raw) if raw else []
            profiles = [_deserialize_profile(item) for item in payload]
            yield handle, profiles
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_registry() -> list[VoiceProfile]:
    with _locked_registry(exclusive=False) as (_, profiles):
        return list(profiles)


def save_profile(name: str, audio_bytes: bytes, stored_filename: str, notes: str | None) -> VoiceProfile:
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("Voice profile name is required")

    _ensure_storage_dirs()
    profile_id = str(uuid.uuid4())
    extension = Path(stored_filename or "").suffix.lower() or ".wav"
    persisted_filename = f"{profile_id}{extension}"
    container_audio_path = CONTAINER_VOICE_PROFILES_DIR / persisted_filename
    host_audio_path = HOST_VOICE_PROFILES_DIR / persisted_filename

    container_audio_path.write_bytes(audio_bytes)

    profile = VoiceProfile(
        id=profile_id,
        name=cleaned_name,
        reference_audio_path=str(host_audio_path),
        created_at=_now_iso(),
        notes=notes.strip() if notes and notes.strip() else None,
    )

    try:
        with _locked_registry(exclusive=True) as (handle, profiles):
            profiles.append(profile)
            _write_profiles(handle, profiles)
    except Exception:
        container_audio_path.unlink(missing_ok=True)
        raise

    return profile


def delete_profile(profile_id: str) -> bool:
    removed_profile: VoiceProfile | None = None

    with _locked_registry(exclusive=True) as (handle, profiles):
        remaining: list[VoiceProfile] = []
        for profile in profiles:
            if profile.id == profile_id and removed_profile is None:
                removed_profile = profile
                continue
            remaining.append(profile)

        if removed_profile is None:
            return False

        _write_profiles(handle, remaining)

    audio_path = host_path_to_container(removed_profile.reference_audio_path)
    audio_path.unlink(missing_ok=True)
    return True


def get_profile(profile_id: str) -> VoiceProfile | None:
    with _locked_registry(exclusive=False) as (_, profiles):
        for profile in profiles:
            if profile.id == profile_id:
                return profile
    return None


def update_profile_reference_transcript(profile_id: str, transcript: str) -> VoiceProfile | None:
    cleaned_transcript = transcript.strip()
    if not cleaned_transcript:
        raise ValueError("Reference transcript must not be empty")

    with _locked_registry(exclusive=True) as (handle, profiles):
        updated_profile: VoiceProfile | None = None
        for profile in profiles:
            if profile.id != profile_id:
                continue
            profile.reference_transcript = cleaned_transcript
            updated_profile = profile
            break

        if updated_profile is None:
            return None

        _write_profiles(handle, profiles)
        return updated_profile
