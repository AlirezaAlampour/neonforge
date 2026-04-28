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


def _configure_voiceover_output_storage(monkeypatch, tmp_path: Path) -> None:
    outputs_root = tmp_path / "outputs"
    monkeypatch.setattr(routes, "HOST_OUTPUTS_ROOT", outputs_root)
    monkeypatch.setattr(routes, "CONTAINER_OUTPUTS_ROOT", outputs_root)


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


def _mock_reference_transcription(monkeypatch, transcript: str = "This is the exact recorded transcript.") -> None:
    async def fake_transcribe(audio_name: str, audio_bytes: bytes, media_type: str) -> str:
        assert audio_name.endswith(".wav")
        assert audio_bytes[:4] == b"RIFF"
        assert media_type in {"audio/wav", "audio/x-wav"}
        return transcript

    monkeypatch.setattr(routes, "_transcribe_audio_with_whisper", fake_transcribe)


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


@pytest.mark.parametrize(
    "recording_source, filename, media_type",
    [
        ("upload", "reference.mp3", "audio/mpeg"),
        (routes.BROWSER_RECORDED_PROFILE_SOURCE, "reference.webm", "audio/webm"),
    ],
)
def test_create_voice_profile_persists_reference_transcript_when_transcription_succeeds(
    monkeypatch,
    tmp_path: Path,
    recording_source: str,
    filename: str,
    media_type: str,
):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    _mock_ffmpeg_success(monkeypatch, duration_ms=1500, sample_rate=48000, channels=2)
    _mock_reference_transcription(monkeypatch, "Reference clip transcript.")
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/profiles",
        data={
            "name": "Narrator",
            "notes": "Warm delivery",
            "recording_source": recording_source,
        },
        files={"audio_file": (filename, b"uploaded-audio", media_type)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reference_transcript"] == "Reference clip transcript."

    registry_profiles = profiles.load_registry()
    assert len(registry_profiles) == 1
    assert registry_profiles[0].reference_transcript == "Reference clip transcript."


def test_create_voice_profile_accepts_browser_recording_and_stores_a_reusable_wav_profile(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    _mock_ffmpeg_success(monkeypatch, duration_ms=1800, sample_rate=48000, channels=2)
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/profiles",
        data={
            "name": "Recorded Narrator",
            "notes": "Quiet booth take",
            "recording_source": routes.BROWSER_RECORDED_PROFILE_SOURCE,
        },
        files={"audio_file": ("reference.webm", b"browser-recorded-audio", "audio/webm")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Recorded Narrator"
    assert payload["notes"] == "Quiet booth take"
    assert payload["reference_audio_path"].endswith(".wav")

    stored_path = profiles.host_path_to_container(payload["reference_audio_path"])
    assert stored_path.exists()
    with wave.open(str(stored_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 2
        assert wav_file.getframerate() == 48000

    registry_profiles = profiles.load_registry()
    assert [profile.id for profile in registry_profiles] == [payload["id"]]


@pytest.mark.parametrize("filename, media_type", [("reference.ogg", "audio/ogg"), ("reference.webm", "audio/webm")])
def test_create_voice_profile_rejects_unsupported_extensions(monkeypatch, tmp_path: Path, filename: str, media_type: str):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/profiles",
        data={"name": "Narrator"},
        files={"audio_file": (filename, b"uploaded-audio", media_type)},
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


@pytest.mark.parametrize(
    "filename, media_type",
    [
        ("recording.webm", "audio/webm"),
        ("recording.ogg", "audio/ogg"),
        ("recording.m4a", "audio/mp4"),
    ],
)
def test_create_temp_reference_normalizes_transcribes_and_stores_clip(
    monkeypatch,
    tmp_path: Path,
    filename: str,
    media_type: str,
):
    _configure_voiceover_output_storage(monkeypatch, tmp_path)
    _mock_ffmpeg_success(monkeypatch, duration_ms=1200, sample_rate=48000, channels=2)
    _mock_reference_transcription(monkeypatch, "Freshly recorded continuation prompt.")
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/temp-reference",
        files={"audio_file": (filename, b"uploaded-audio", media_type)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["transcript"] == "Freshly recorded continuation prompt."

    stored_path = routes._get_temp_reference_path(payload["temp_reference_id"])
    assert stored_path is not None
    assert stored_path.exists()
    assert stored_path.suffix == ".wav"
    assert stored_path.read_bytes()[:4] == b"RIFF"


class _FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.store: dict[str, dict[str, str]] = {}
        self.deleted_keys: list[str] = []

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.calls.append((key, mapping))
        self.store.setdefault(key, {}).update(mapping)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.store.get(key, {}))

    async def delete(self, key: str) -> None:
        self.deleted_keys.append(key)
        self.store.pop(key, None)


class _FakeModel:
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.display_name = f"{model_id} (fake)"
        self.supports_reference_audio = True

    def is_available(self) -> bool:
        return True

    def availability_error(self) -> str:
        return "unavailable"


def _fake_profile(profile_id: str = "profile-1") -> profiles.VoiceProfile:
    return profiles.VoiceProfile(
        id=profile_id,
        name="Narrator",
        reference_audio_path=f"/tmp/{profile_id}.wav",
        created_at="2026-01-01T00:00:00+00:00",
        notes=None,
        reference_transcript=None,
    )


def _configure_job_creation(monkeypatch, *, model_id: str):
    fake_redis = _FakeRedis()
    captured: dict[str, object] = {}

    async def fake_run_voiceover_job(
        job_id,
        profile_id,
        script,
        requested_model_id,
        output_format,
        speed,
        redis_client,
        vox_mode=None,
        prompt_text=None,
        style_text=None,
        reference_audio_path=None,
        reference_label=None,
    ) -> None:
        captured.update(
            {
                "job_id": job_id,
                "profile_id": profile_id,
                "script": script,
                "model_id": requested_model_id,
                "output_format": output_format,
                "speed": speed,
                "redis_client": redis_client,
                "vox_mode": vox_mode,
                "prompt_text": prompt_text,
                "style_text": style_text,
                "reference_audio_path": reference_audio_path,
                "reference_label": reference_label,
            }
        )

    monkeypatch.setattr(routes, "_get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(routes.ModelRegistry, "get_model", lambda requested_model_id: _FakeModel(requested_model_id))
    monkeypatch.setattr(routes, "run_voiceover_job", fake_run_voiceover_job)
    return fake_redis, captured


def test_create_voiceover_job_defaults_vox_to_clone_mode(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    _, captured = _configure_job_creation(monkeypatch, model_id="voxcpm2")
    profile = _fake_profile()
    monkeypatch.setattr(routes, "get_profile", lambda profile_id: profile if profile_id == profile.id else None)
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/jobs",
        json={
            "voice_profile_id": profile.id,
            "script": "Render this normally.",
            "model_id": "voxcpm2",
            "output_format": "wav",
            "speed": 1.0,
        },
    )

    assert response.status_code == 200
    assert captured["profile_id"] == profile.id
    assert captured["vox_mode"] == routes.VOX_MODE_CLONE
    assert captured["prompt_text"] is None
    assert captured["style_text"] is None


def test_create_voiceover_job_allows_vox_design_without_profile(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    _, captured = _configure_job_creation(monkeypatch, model_id="voxcpm2")
    monkeypatch.setattr(routes, "get_profile", lambda profile_id: pytest.fail("Vox design mode should not look up a saved profile"))
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/jobs",
        json={
            "script": "Invent a fresh announcer voice for this copy.",
            "model_id": "voxcpm2",
            "vox_mode": "design",
            "style_text": "bright, modern, slightly cinematic",
            "output_format": "wav",
            "speed": 1.0,
        },
    )

    assert response.status_code == 200
    assert captured["profile_id"] is None
    assert captured["vox_mode"] == routes.VOX_MODE_DESIGN
    assert captured["style_text"] == "bright, modern, slightly cinematic"


def test_create_voiceover_job_rejects_vox_clone_without_profile(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    _configure_job_creation(monkeypatch, model_id="voxcpm2")
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/jobs",
        json={
            "script": "Clone this voice please.",
            "model_id": "voxcpm2",
            "vox_mode": "clone",
            "output_format": "wav",
            "speed": 1.0,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Vox clone mode requires a saved voice profile"


def test_create_voiceover_job_rejects_vox_continuation_without_any_reference(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    _configure_job_creation(monkeypatch, model_id="voxcpm2")
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/jobs",
        json={
            "script": "Continue from something fresh.",
            "model_id": "voxcpm2",
            "vox_mode": "continuation",
            "prompt_text": "The transcript exists but there is no clip.",
            "output_format": "wav",
            "speed": 1.0,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Vox continuation mode requires a saved voice profile or recorded reference clip"


def test_create_voiceover_job_rejects_vox_continuation_without_transcript(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    _configure_job_creation(monkeypatch, model_id="voxcpm2")
    profile = _fake_profile()
    monkeypatch.setattr(routes, "get_profile", lambda profile_id: profile if profile_id == profile.id else None)
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/jobs",
        json={
            "voice_profile_id": profile.id,
            "script": "Continue from the saved clip.",
            "model_id": "voxcpm2",
            "vox_mode": "continuation",
            "output_format": "wav",
            "speed": 1.0,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Vox continuation mode requires the exact transcript of the reference clip"


def test_create_voiceover_job_rejects_temp_vox_continuation_without_transcript(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    _configure_voiceover_output_storage(monkeypatch, tmp_path)
    _configure_job_creation(monkeypatch, model_id="voxcpm2")

    temp_reference_id = "recorded-reference-1"
    temp_reference_path = routes._temp_reference_path(temp_reference_id)
    temp_reference_path.parent.mkdir(parents=True, exist_ok=True)
    temp_reference_path.write_bytes(b"RIFFfake")

    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/jobs",
        json={
            "temp_reference_id": temp_reference_id,
            "script": "Continue directly from this fresh take.",
            "model_id": "voxcpm2",
            "vox_mode": "continuation",
            "output_format": "wav",
            "speed": 1.0,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Vox continuation mode requires the exact transcript of the reference clip"


def test_create_voiceover_job_accepts_vox_continuation_with_temp_reference(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    _configure_voiceover_output_storage(monkeypatch, tmp_path)
    _, captured = _configure_job_creation(monkeypatch, model_id="voxcpm2")

    temp_reference_id = "recorded-reference-1"
    temp_reference_path = routes._temp_reference_path(temp_reference_id)
    temp_reference_path.parent.mkdir(parents=True, exist_ok=True)
    temp_reference_path.write_bytes(b"RIFFfake")

    monkeypatch.setattr(routes, "get_profile", lambda profile_id: pytest.fail("Temp Vox continuation should not require a saved profile"))
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/jobs",
        json={
            "temp_reference_id": temp_reference_id,
            "script": "Continue directly from this fresh take.",
            "model_id": "voxcpm2",
            "vox_mode": "continuation",
            "prompt_text": "Freshly recorded continuation prompt.",
            "output_format": "wav",
            "speed": 1.0,
        },
    )

    assert response.status_code == 200
    assert captured["profile_id"] is None
    assert captured["vox_mode"] == routes.VOX_MODE_CONTINUATION
    assert captured["prompt_text"] == "Freshly recorded continuation prompt."
    assert captured["reference_audio_path"] != str(temp_reference_path)
    assert Path(str(captured["reference_audio_path"])).name == "recorded_reference.wav"
    assert captured["job_id"] in str(captured["reference_audio_path"])
    assert captured["reference_label"] == "Recorded Reference"


def test_create_voiceover_job_keeps_saved_profile_vox_continuation_flow(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    _, captured = _configure_job_creation(monkeypatch, model_id="voxcpm2")
    profile = _fake_profile()
    monkeypatch.setattr(routes, "get_profile", lambda profile_id: profile if profile_id == profile.id else None)
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/jobs",
        json={
            "voice_profile_id": profile.id,
            "script": "Continue from the saved profile clip.",
            "model_id": "voxcpm2",
            "vox_mode": "continuation",
            "prompt_text": "This is the exact transcript of the saved clip.",
            "output_format": "wav",
            "speed": 1.0,
        },
    )

    assert response.status_code == 200
    assert captured["profile_id"] == profile.id
    assert captured["reference_audio_path"] is None
    assert captured["reference_label"] is None
    assert captured["vox_mode"] == routes.VOX_MODE_CONTINUATION
    assert captured["prompt_text"] == "This is the exact transcript of the saved clip."


def test_create_voiceover_job_still_requires_profile_for_f5(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)
    _configure_job_creation(monkeypatch, model_id="f5tts")
    client = _build_client()

    response = client.post(
        "/api/v1/voiceover/jobs",
        json={
            "script": "Plain F5 narration.",
            "model_id": "f5tts",
            "output_format": "wav",
            "speed": 1.0,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "This model requires a saved voice profile"


def test_recent_outputs_include_metadata_and_script_downloads(monkeypatch, tmp_path: Path):
    _configure_voiceover_output_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(routes, "_get_redis_client", lambda: None)

    job_id = "job-with-metadata"
    job_dir = routes._voiceover_output_dir() / job_id
    job_dir.mkdir(parents=True)
    output_path = job_dir / "voxcpm2_test_ai_training_four_versions_2026-04-27_222424.wav"
    _write_pcm_wav(output_path, _tone_frames(300))
    metadata_path = output_path.with_suffix(".json")
    metadata_path.write_text(
        """{
  "output_filename": "voxcpm2_test_ai_training_four_versions_2026-04-27_222424.wav",
  "created_at": "2026-04-27T22:24:24+00:00",
  "model_id": "voxcpm2",
  "provider": "local",
  "voice_mode": "continuation",
  "reference_source_type": "temp_recording",
  "script_text": "AI training, four versions.",
  "duration_seconds": 0.3
}
""",
        encoding="utf-8",
    )
    client = _build_client()

    outputs_response = client.get("/api/v1/voiceover/outputs")
    assert outputs_response.status_code == 200
    outputs = outputs_response.json()
    assert outputs[0]["job_id"] == job_id
    assert outputs[0]["has_metadata"] is True
    assert outputs[0]["has_script_text"] is True
    assert outputs[0]["metadata_url"] == f"/api/v1/voiceover/output/{job_id}/metadata"
    assert outputs[0]["script_text_url"] == f"/api/v1/voiceover/output/{job_id}/script-text"
    assert outputs[0]["reference_source_type"] == "temp_recording"

    metadata_response = client.get(f"/api/v1/voiceover/output/{job_id}/metadata")
    assert metadata_response.status_code == 200
    assert metadata_response.json()["model_id"] == "voxcpm2"

    script_response = client.get(f"/api/v1/voiceover/output/{job_id}/script-text")
    assert script_response.status_code == 200
    assert script_response.text == "AI training, four versions.\n"


def test_delete_voiceover_output_removes_directory_and_redis_history(monkeypatch, tmp_path: Path):
    _configure_voiceover_output_storage(monkeypatch, tmp_path)
    fake_redis = _FakeRedis()
    monkeypatch.setattr(routes, "_get_redis_client", lambda: fake_redis)

    job_id = "job-to-delete"
    job_dir = routes._voiceover_output_dir() / job_id
    job_dir.mkdir(parents=True)
    output_path = job_dir / "f5tts_narrator_short_script_2026-04-27_222424.wav"
    _write_pcm_wav(output_path, _tone_frames(300))
    output_path.with_suffix(".json").write_text("{}", encoding="utf-8")
    fake_redis.store[f"voiceover:{job_id}"] = {
        "status": "done",
        "output_path": str(output_path),
        "total_chunks": "1",
        "completed_chunks": "1",
    }
    client = _build_client()

    response = client.delete(f"/api/v1/voiceover/output/{job_id}")

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert response.json()["file_deleted"] is True
    assert not job_dir.exists()
    assert fake_redis.deleted_keys == [f"voiceover:{job_id}"]


def test_delete_voiceover_output_handles_already_missing_audio(monkeypatch, tmp_path: Path):
    _configure_voiceover_output_storage(monkeypatch, tmp_path)
    fake_redis = _FakeRedis()
    monkeypatch.setattr(routes, "_get_redis_client", lambda: fake_redis)

    job_id = "job-with-missing-audio"
    job_dir = routes._voiceover_output_dir() / job_id
    job_dir.mkdir(parents=True)
    missing_output = job_dir / "missing.wav"
    (job_dir / "missing.json").write_text("{}", encoding="utf-8")
    fake_redis.store[f"voiceover:{job_id}"] = {
        "status": "done",
        "output_path": str(missing_output),
        "total_chunks": "1",
        "completed_chunks": "1",
    }
    client = _build_client()

    response = client.delete(f"/api/v1/voiceover/output/{job_id}")

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert response.json()["file_deleted"] is False
    assert not job_dir.exists()
    assert fake_redis.deleted_keys == [f"voiceover:{job_id}"]
