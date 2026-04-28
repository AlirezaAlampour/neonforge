import math
import struct
import sys
import wave
import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.voiceover.chunker import chunk_script
from gateway.voiceover.models import FishSpeechModel, VoxCPM2Model
from gateway.voiceover.profiles import get_profile, save_profile
from gateway.voiceover import models, profiles, runner


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
    monkeypatch.setattr(runner, "HOST_OUTPUTS_ROOT", outputs_root)
    monkeypatch.setattr(runner, "CONTAINER_OUTPUTS_ROOT", outputs_root)


def _tone_frames(duration_ms: int, *, sample_rate: int = 24000, amplitude: int = 5000, frequency_hz: float = 220.0) -> bytes:
    frame_count = int(sample_rate * (duration_ms / 1000.0))
    payload = bytearray()
    for frame_index in range(frame_count):
        value = int(amplitude * math.sin((2.0 * math.pi * frequency_hz * frame_index) / sample_rate))
        payload.extend(struct.pack("<h", value))
    return bytes(payload)


def _silence_frames(duration_ms: int, *, sample_rate: int = 24000) -> bytes:
    frame_count = int(sample_rate * (duration_ms / 1000.0))
    return b"\x00\x00" * frame_count


def _write_pcm_wav(path: Path, frames: bytes, *, sample_rate: int = 24000) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)


def _wav_duration_ms(path: Path) -> float:
    with wave.open(str(path), "rb") as wav_file:
        return (wav_file.getnframes() / float(wav_file.getframerate())) * 1000.0


class _FakeResponse:
    def __init__(self, *, payload=None, content: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self._payload = payload or {}
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.store.setdefault(key, {}).update(mapping)


class _FakeVoxModel:
    model_id = runner.VOX_MODEL_ID
    display_name = "Vox fake"
    supports_reference_audio = True

    def synthesize(self, text: str, reference_audio_path: str | None, options: dict) -> bytes:
        output = Path(options.get("_test_output", "")) if options.get("_test_output") else None
        if output:
            return output.read_bytes()

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            temp_path = Path(handle.name)
        try:
            _write_pcm_wav(temp_path, _tone_frames(250))
            return temp_path.read_bytes()
        finally:
            temp_path.unlink(missing_ok=True)


def test_fish_reference_transcript_is_persisted_and_reused(monkeypatch, tmp_path: Path):
    _configure_voice_profile_storage(monkeypatch, tmp_path)

    reference_wav = tmp_path / "reference.wav"
    _write_pcm_wav(reference_wav, _tone_frames(300))
    profile = save_profile(
        name="Narrator",
        audio_bytes=reference_wav.read_bytes(),
        stored_filename="reference.wav",
        notes="",
    )

    transcription_calls = 0
    synth_payloads: list[dict] = []

    def fake_post(url: str, *args, **kwargs):
        nonlocal transcription_calls
        if url.endswith("/transcribe"):
            transcription_calls += 1
            return _FakeResponse(payload={"text": "This is the cached reference transcript."})
        if url.endswith("/v1/tts"):
            synth_payloads.append(kwargs["json"])
            return _FakeResponse(content=b"RIFFfake", headers={"content-type": "audio/wav"})
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(FishSpeechModel, "is_available", lambda self: True)
    monkeypatch.setattr(models.httpx, "post", fake_post)

    first_model = FishSpeechModel()
    second_model = FishSpeechModel()

    first_output = first_model.synthesize(
        "First job.",
        profile.reference_audio_path,
        {"voice_profile_id": profile.id, "speed": 1.0},
    )
    second_output = second_model.synthesize(
        "Second job.",
        profile.reference_audio_path,
        {"voice_profile_id": profile.id, "speed": 1.0},
    )

    assert first_output == b"RIFFfake"
    assert second_output == b"RIFFfake"
    assert transcription_calls == 1
    assert len(synth_payloads) == 2
    assert synth_payloads[0]["references"][0]["text"] == "This is the cached reference transcript."
    assert synth_payloads[1]["references"][0]["text"] == "This is the cached reference transcript."
    assert get_profile(profile.id).reference_transcript == "This is the cached reference transcript."


def test_run_voiceover_job_writes_metadata_and_script_slug_filename(monkeypatch, tmp_path: Path):
    _configure_voiceover_output_storage(monkeypatch, tmp_path)
    fake_redis = _FakeRedis()
    monkeypatch.setattr(runner.ModelRegistry, "get_model", lambda model_id: _FakeVoxModel())

    script = "AI training, four versions for launch review with a much longer trailing clause."

    asyncio.run(
        runner.run_voiceover_job(
            "job-1",
            None,
            script,
            runner.VOX_MODEL_ID,
            "wav",
            1.15,
            fake_redis,
            runner.VOX_MODE_DESIGN,
            None,
            "warm",
        )
    )

    job = fake_redis.store["voiceover:job-1"]
    assert job["status"] == "done"
    output_path = Path(job["output_path"])
    container_output_path = runner._host_output_path_to_container(output_path)
    assert container_output_path.exists()
    assert "ai_training_four_versions_for_launch_review_with" in container_output_path.name
    assert container_output_path.name.endswith(".wav")

    metadata_path = container_output_path.with_suffix(".json")
    assert metadata_path.exists()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["output_filename"] == container_output_path.name
    assert metadata["model_id"] == runner.VOX_MODEL_ID
    assert metadata["provider"] == "local"
    assert metadata["voice_mode"] == runner.VOX_MODE_DESIGN
    assert metadata["reference_source_type"] == "none"
    assert metadata["script_text"] == script
    assert metadata["generation_params"] == {"format": "wav"}
    assert metadata["chunk_count"] == 1
    assert metadata["duration_seconds"] is not None


def test_vox_clone_mode_sends_reference_audio_without_prompt_text(monkeypatch, tmp_path: Path):
    reference_wav = tmp_path / "reference.wav"
    _write_pcm_wav(reference_wav, _tone_frames(300))

    captured: dict[str, object] = {}

    def fake_post(url: str, *args, **kwargs):
        captured["url"] = url
        captured["data"] = kwargs["data"]
        captured["files"] = kwargs.get("files") or {}
        return _FakeResponse(content=b"RIFFfake", headers={"content-type": "audio/wav"})

    monkeypatch.setattr(VoxCPM2Model, "is_available", lambda self: True)
    monkeypatch.setattr(models.httpx, "post", fake_post)

    output = VoxCPM2Model().synthesize(
        "This is a clean clone.",
        str(reference_wav),
        {"vox_mode": runner.VOX_MODE_CLONE, "style_text": "warm, steady delivery"},
    )

    assert output == b"RIFFfake"
    assert captured["url"] == "http://voxcpm2:8000/synthesize"
    assert captured["data"] == {
        "text": "(warm, steady delivery)This is a clean clone.",
        "vox_mode": runner.VOX_MODE_CLONE,
    }
    assert list((captured["files"] or {}).keys()) == ["reference_audio"]


def test_vox_continuation_mode_sends_prompt_text_when_explicitly_requested(monkeypatch, tmp_path: Path):
    reference_wav = tmp_path / "reference.wav"
    _write_pcm_wav(reference_wav, _tone_frames(300))

    captured: dict[str, object] = {}

    def fake_post(url: str, *args, **kwargs):
        captured["data"] = kwargs["data"]
        captured["files"] = kwargs.get("files") or {}
        return _FakeResponse(content=b"RIFFfake", headers={"content-type": "audio/wav"})

    monkeypatch.setattr(VoxCPM2Model, "is_available", lambda self: True)
    monkeypatch.setattr(models.httpx, "post", fake_post)

    output = VoxCPM2Model().synthesize(
        "Continue from the same thought.",
        str(reference_wav),
        {
            "vox_mode": runner.VOX_MODE_CONTINUATION,
            "prompt_text": "This is the exact transcript of the saved clip.",
            "style_text": "should be ignored here",
        },
    )

    assert output == b"RIFFfake"
    assert captured["data"] == {
        "text": "Continue from the same thought.",
        "vox_mode": runner.VOX_MODE_CONTINUATION,
        "prompt_text": "This is the exact transcript of the saved clip.",
    }
    assert list((captured["files"] or {}).keys()) == ["reference_audio"]


def test_vox_chunk_strategy_prefers_single_pass_for_short_scripts():
    script = "First sentence. Second sentence! Third question? Fourth line."

    chunks = runner._build_voiceover_chunks(
        script,
        model_id=runner.VOX_MODEL_ID,
        vox_mode=runner.VOX_MODE_CLONE,
    )

    assert chunks == [{"text": script, "pause_ms": 0, "is_pause": False, "soft_split": False}]


def test_vox_chunk_strategy_uses_larger_semantic_groups_when_chunking_is_needed(monkeypatch):
    monkeypatch.setattr(runner, "VOX_SINGLE_PASS_MAX_CHARS", 10)

    chunks = runner._build_voiceover_chunks(
        "One. Two. Three. Four. Five. Six.",
        model_id=runner.VOX_MODEL_ID,
        vox_mode=runner.VOX_MODE_CLONE,
    )

    assert [chunk["text"] for chunk in chunks] == [
        "One. Two. Three. Four.",
        "Five. Six.",
    ]


def test_vox_stitching_trims_obvious_silence_and_crossfades_chunks(tmp_path: Path):
    chunk_one = tmp_path / "chunk_0001.wav"
    chunk_two = tmp_path / "chunk_0002.wav"
    output = tmp_path / "stitched.wav"

    _write_pcm_wav(
        chunk_one,
        _silence_frames(100) + _tone_frames(250, frequency_hz=220.0) + _silence_frames(160),
    )
    _write_pcm_wav(
        chunk_two,
        _silence_frames(120) + _tone_frames(250, frequency_hz=330.0) + _silence_frames(100),
    )

    runner._stitch_vox_wavs(
        [
            {"text": "One.", "pause_ms": 0, "is_pause": False, "soft_split": False},
            {"text": "Two.", "pause_ms": 0, "is_pause": False, "soft_split": False},
        ],
        [chunk_one, chunk_two],
        output,
    )

    duration_ms = _wav_duration_ms(output)

    assert duration_ms < 750
    assert duration_ms > 600


def test_vox_stitching_keeps_pause_chunks(tmp_path: Path):
    chunk_one = tmp_path / "chunk_0001.wav"
    chunk_two = tmp_path / "chunk_0002.wav"
    output = tmp_path / "stitched_pause.wav"

    _write_pcm_wav(chunk_one, _tone_frames(200, frequency_hz=220.0))
    _write_pcm_wav(chunk_two, _tone_frames(200, frequency_hz=330.0))

    runner._stitch_vox_wavs(
        [
            {"text": "One.", "pause_ms": 0, "is_pause": False, "soft_split": False},
            {"text": "", "pause_ms": 600, "is_pause": True},
            {"text": "Two.", "pause_ms": 0, "is_pause": False, "soft_split": False},
        ],
        [chunk_one, chunk_two],
        output,
    )

    duration_ms = _wav_duration_ms(output)

    assert duration_ms > 950
    assert duration_ms < 1025
