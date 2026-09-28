import importlib.util
import io
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SERVICE_APP_PATH = ROOT / "services" / "breeze_tts" / "app.py"


def _load_service_module():
    spec = importlib.util.spec_from_file_location("neonforge_breeze_service", SERVICE_APP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


breeze_app = _load_service_module()


def _valid_wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x00\x00" * 240)
    return output.getvalue()


def test_breeze_service_validation_uses_real_three_mode_semantics():
    design = breeze_app._validate_request(
        mode="design",
        instruction="A warm narrator.",
        has_reference_audio=False,
        ref_text="",
        cfg_scale=None,
        seed=42,
    )
    clone = breeze_app._validate_request(
        mode="clone",
        instruction=None,
        has_reference_audio=True,
        ref_text="Exact transcript.",
        cfg_scale=None,
        seed=43,
    )
    direction = breeze_app._validate_request(
        mode="direction",
        instruction="Sound energetic.",
        has_reference_audio=True,
        ref_text="Exact transcript.",
        cfg_scale=4.5,
        seed=44,
    )

    assert design[3:] == (4.0, 42)
    assert clone[3:] == (1.0, 43)
    assert direction[3:] == (4.5, 44)


def test_breeze_service_rejects_mismatched_reference_pair_and_clone_instruction():
    with pytest.raises(ValueError, match="provided together"):
        breeze_app._validate_request(
            mode="clone",
            instruction=None,
            has_reference_audio=True,
            ref_text="",
            cfg_scale=None,
            seed=42,
        )

    with pytest.raises(ValueError, match="use Voice Direction"):
        breeze_app._validate_request(
            mode="clone",
            instruction="Energetic.",
            has_reference_audio=True,
            ref_text="Exact transcript.",
            cfg_scale=None,
            seed=42,
        )


@pytest.mark.parametrize(
    "mode, instruction, with_reference",
    [
        ("design", "A clear creator voice.", False),
        ("clone", None, True),
        ("direction", "Keep the identity and sound excited.", True),
    ],
)
def test_breeze_service_synthesize_returns_wav_for_each_mode(monkeypatch, mode, instruction, with_reference):
    captured: dict[str, object] = {}
    fake_bundle = SimpleNamespace(runtime=SimpleNamespace(sample_rate=24000))

    async def fake_ensure_runtime():
        return fake_bundle

    def fake_render(bundle, **kwargs):
        captured.update(kwargs)
        return _valid_wav()

    monkeypatch.setattr(breeze_app, "_ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr(breeze_app, "_render_wav", fake_render)
    client = TestClient(breeze_app.app)

    data = {
        "text": "Test the Breeze service.",
        "mode": mode,
        "seed": "123",
    }
    if instruction:
        data["instruction"] = instruction
        data["cfg_scale"] = "4"
    if with_reference:
        data["ref_text"] = "Exact transcript."
    files = {"reference_audio": ("reference.wav", _valid_wav(), "audio/wav")} if with_reference else None

    response = client.post("/synthesize", data=data, files=files)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content[:4] == b"RIFF"
    assert captured["mode"] == mode
    assert captured["seed"] == 123


def test_breeze_health_payload_reports_eager_runtime(monkeypatch):
    monkeypatch.setattr(breeze_app, "_runtime_status", "ready")
    monkeypatch.setattr(breeze_app, "_runtime", object())
    payload = breeze_app._health_payload()
    assert payload["runtime_status"] == "ready"
    assert payload["attention"] == "eager"
    assert payload["fast_path"] is False
