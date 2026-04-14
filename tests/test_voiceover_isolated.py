import math
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.voiceover.chunker import chunk_script
from gateway.voiceover.models import FishSpeechModel
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


def test_vox_chunk_strategy_uses_two_sentence_groups():
    chunks = chunk_script(
        "First sentence. Second sentence! Third question? Fourth line.",
        max_chars=runner.VOX_MAX_CHARS,
        target_sentences_per_chunk=runner.VOX_TARGET_SENTENCES_PER_CHUNK,
    )

    assert [chunk["text"] for chunk in chunks] == [
        "First sentence. Second sentence!",
        "Third question? Fourth line.",
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
