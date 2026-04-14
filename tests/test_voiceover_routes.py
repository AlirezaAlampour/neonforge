import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.voiceover import profiles, routes


def _configure_voice_profile_storage(monkeypatch, tmp_path: Path) -> None:
    assets_root = tmp_path / "assets"
    voice_profiles_dir = assets_root / "voice_profiles"
    registry_path = voice_profiles_dir / "registry.json"

    monkeypatch.setattr(profiles, "HOST_ASSETS_ROOT", assets_root)
    monkeypatch.setattr(profiles, "CONTAINER_ASSETS_ROOT", assets_root)
    monkeypatch.setattr(profiles, "HOST_VOICE_PROFILES_DIR", voice_profiles_dir)
    monkeypatch.setattr(profiles, "CONTAINER_VOICE_PROFILES_DIR", voice_profiles_dir)
    monkeypatch.setattr(profiles, "REGISTRY_HOST_PATH", registry_path)
    monkeypatch.setattr(profiles, "REGISTRY_CONTAINER_PATH", registry_path)


def _tone_frames(duration_ms: int, *, sample_rate: int = 24000, amplitude: int = 5000, frequency_hz: float = 220.0) -> bytes:
    frame_count = int(sample_rate * (duration_ms / 1000.0))
    payload = bytearray()
    for frame_index in range(frame_count):
        value = int(amplitude * math.sin((2.0 * math.pi * frequency_hz * frame_index) / sample_rate))
        payload.extend(struct.pack("<h", value))
    return bytes(payload)


def _interleave_stereo(frames: bytes) -> bytes:
    stereo = bytearray()
    for frame_index in range(0, len(frames), 2):
        sample = frames[frame_index : frame_index + 2]
        stereo.extend(sample)
        stereo.extend(sample)
    return bytes(stereo)


def _write_pcm_wav(path: Path, frames: bytes, *, sample_rate: int = 24000, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _mock_ffmpeg_success(
    monkeypatch,
    *,
    duration_ms: int = 1000,
    sample_rate: int = 48000,
    channels: int = 2,
) -> None:
    monkeypatch.setattr(routes.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)

    def fake_run(command: list[str], *, check: bool, capture_output: bool, text: bool):
        assert check is True
        assert capture_output is True
        assert text is True
        assert command[0] == "ffmpeg"
        assert "-ac" not in command
        assert "-ar" not in command
        assert "-c:a" in command and command[command.index("-c:a") + 1] == "pcm_s16le"

        output_path = Path(command[-1])
        frames = _tone_frames(duration_ms, sample_rate=sample_rate)
        if channels == 2:
            frames = _interleave_stereo(frames)
        _write_pcm_wav(output_path, frames, sample_rate=sample_rate, channels=channels)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(routes.subprocess, "run", fake_run)


@pytest.mark.parametrize("filename", ["reference.wav", "reference.mp3", "reference.m4a"])
def test_create_voice_profile_normalizes_supported_uploads_to_wav(monkeypatch, tmp_path: Path, filename: str):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    _mock_ffmpeg_success(monkeypatch, duration_ms=1500, sample_rate=48000, channels=2)
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/profiles",
        data={"name": "Narrator", "notes": "Warm delivery"},
        files={"audio_file": (filename, b"uploaded-audio", "application/octet-stream")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Narrator"
    assert payload["notes"] == "Warm delivery"
    assert payload["reference_audio_path"].endswith(".wav")

    stored_path = profiles.host_path_to_container(payload["reference_audio_path"])
    assert stored_path.exists()
    assert stored_path.suffix == ".wav"
    assert stored_path.read_bytes()[:4] == b"RIFF"
    assert stored_path.read_bytes() != b"uploaded-audio"
    with wave.open(str(stored_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 2
        assert wav_file.getframerate() == 48000


def test_create_voice_profile_rejects_unsupported_extensions(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/profiles",
        data={"name": "Narrator"},
        files={"audio_file": ("reference.ogg", b"uploaded-audio", "audio/ogg")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Audio file must be a WAV, MP3, or M4A"


def test_create_voice_profile_rejects_reference_audio_longer_than_30_seconds(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    _mock_ffmpeg_success(monkeypatch, duration_ms=31_000)
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/profiles",
        data={"name": "Narrator"},
        files={"audio_file": ("reference.m4a", b"uploaded-audio", "audio/mp4")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Reference audio must be 30 seconds or shorter"
    assert profiles.load_registry() == []


def test_create_voice_profile_returns_clear_error_when_normalization_fails(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(routes.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)

    def fake_run(command: list[str], *, check: bool, capture_output: bool, text: bool):
        raise subprocess.CalledProcessError(1, command, stderr="invalid data")

    monkeypatch.setattr(routes.subprocess, "run", fake_run)
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/profiles",
        data={"name": "Narrator"},
        files={"audio_file": ("reference.m4a", b"uploaded-audio", "audio/mp4")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Audio file could not be decoded as WAV, MP3, or M4A"
    assert profiles.load_registry() == []


def test_create_voice_profile_returns_clear_error_when_ffmpeg_is_unavailable(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(routes.shutil, "which", lambda name: None)
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/profiles",
        data={"name": "Narrator"},
        files={"audio_file": ("reference.m4a", b"uploaded-audio", "audio/mp4")},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Reference audio normalization is unavailable because ffmpeg is not installed"
