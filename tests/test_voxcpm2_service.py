from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SERVICE_APP_PATH = ROOT / "services" / "voxcpm2" / "app.py"

spec = importlib.util.spec_from_file_location("voxcpm2_service_app", SERVICE_APP_PATH)
assert spec is not None and spec.loader is not None
voxcpm_service = importlib.util.module_from_spec(spec)
sys.modules.setdefault("voxcpm2_service_app", voxcpm_service)
spec.loader.exec_module(voxcpm_service)


class _FakeRuntimeModel:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.tts_model = type("FakeTTSModel", (), {"sample_rate": 24000})()

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return [0.0, 0.0]


@contextmanager
def _service_client(monkeypatch):
    fake_model = _FakeRuntimeModel()

    monkeypatch.setattr(voxcpm_service, "load_model", lambda: None)
    monkeypatch.setattr(voxcpm_service, "destroy_model", lambda: None)
    monkeypatch.setattr(voxcpm_service.sf, "write", lambda buffer, wav, sample_rate, format: buffer.write(b"RIFFfake"))

    voxcpm_service.model = fake_model
    voxcpm_service.model_loaded = True
    voxcpm_service.load_error = None

    with TestClient(voxcpm_service.app) as client:
        yield client, fake_model


def test_voxcpm2_service_design_mode_uses_no_reference_paths(monkeypatch):
    with _service_client(monkeypatch) as (client, fake_model):
        response = client.post(
            "/synthesize",
            data={"text": "Design a fresh voice for this line.", "vox_mode": "design"},
        )

    assert response.status_code == 200
    assert response.content == b"RIFFfake"
    assert fake_model.calls == [
        {
            "text": "Design a fresh voice for this line.",
            "cfg_value": voxcpm_service.CFG_VALUE,
            "inference_timesteps": voxcpm_service.INFERENCE_TIMESTEPS,
            "normalize": False,
            "denoise": False,
        }
    ]


def test_voxcpm2_service_clone_mode_uses_reference_only(monkeypatch):
    with _service_client(monkeypatch) as (client, fake_model):
        response = client.post(
            "/synthesize",
            data={"text": "Clone this voice cleanly.", "vox_mode": "clone"},
            files={"reference_audio": ("reference.wav", b"fake-audio", "audio/wav")},
        )

    assert response.status_code == 200
    assert response.content == b"RIFFfake"
    assert len(fake_model.calls) == 1
    generate_kwargs = fake_model.calls[0]
    assert generate_kwargs["text"] == "Clone this voice cleanly."
    assert "reference_wav_path" in generate_kwargs
    assert "prompt_wav_path" not in generate_kwargs
    assert "prompt_text" not in generate_kwargs


def test_voxcpm2_service_continuation_mode_uses_prompt_semantics(monkeypatch):
    with _service_client(monkeypatch) as (client, fake_model):
        response = client.post(
            "/synthesize",
            data={
                "text": "Continue the same performance.",
                "vox_mode": "continuation",
                "prompt_text": "This is the exact transcript of the reference clip.",
            },
            files={"reference_audio": ("reference.wav", b"fake-audio", "audio/wav")},
        )

    assert response.status_code == 200
    assert response.content == b"RIFFfake"
    assert len(fake_model.calls) == 1
    generate_kwargs = fake_model.calls[0]
    assert generate_kwargs["text"] == "Continue the same performance."
    assert generate_kwargs["prompt_text"] == "This is the exact transcript of the reference clip."
    assert generate_kwargs["prompt_wav_path"] == generate_kwargs["reference_wav_path"]
