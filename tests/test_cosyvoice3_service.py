from __future__ import annotations

import importlib.util
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SERVICE_APP_PATH = ROOT / "services" / "cosyvoice3" / "app.py"

spec = importlib.util.spec_from_file_location("cosyvoice3_service_app", SERVICE_APP_PATH)
assert spec is not None and spec.loader is not None
cosyvoice3_service = importlib.util.module_from_spec(spec)
sys.modules.setdefault("cosyvoice3_service_app", cosyvoice3_service)
spec.loader.exec_module(cosyvoice3_service)


class _FakeCosyModel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.sample_rate = 24000

    def inference_zero_shot(self, text, prompt_text, prompt_wav_path, *, stream=False):
        self.calls.append(("inference_zero_shot", (text, prompt_text, prompt_wav_path), {"stream": stream}))
        yield {"tts_speech": [0.0, 0.0]}


@contextmanager
def _service_client(monkeypatch):
    fake_model = _FakeCosyModel()

    monkeypatch.setattr(cosyvoice3_service, "load_model", lambda: fake_model)
    monkeypatch.setattr(cosyvoice3_service, "destroy_model", lambda: None)
    monkeypatch.setattr(cosyvoice3_service.sf, "write", lambda buffer, wav, sample_rate, format: buffer.write(b"RIFFfake"))

    cosyvoice3_service._model = fake_model
    cosyvoice3_service._load_error = None
    cosyvoice3_service._dependency_error = None

    with TestClient(cosyvoice3_service.app) as client:
        yield client, fake_model


def test_cosyvoice3_service_clone_mode_uses_zero_shot_with_reference_transcript(monkeypatch):
    with _service_client(monkeypatch) as (client, fake_model):
        response = client.post(
            "/synthesize",
            data={
                "text": "Render this with the saved narrator voice.",
                "voice_mode": "clone",
                "prompt_text": "This is the exact reference transcript.",
            },
            files={"reference_audio": ("reference.wav", b"fake-audio", "audio/wav")},
        )

    assert response.status_code == 200
    assert response.content == b"RIFFfake"
    assert len(fake_model.calls) == 1
    call_name, args, kwargs = fake_model.calls[0]
    assert call_name == "inference_zero_shot"
    assert args[0] == "Render this with the saved narrator voice."
    assert args[1] == "This is the exact reference transcript."
    assert str(args[2]).endswith(".wav")
    assert kwargs == {"stream": False}


def test_cosyvoice3_service_rejects_missing_reference_transcript(monkeypatch):
    with _service_client(monkeypatch) as (client, _fake_model):
        response = client.post(
            "/synthesize",
            data={"text": "Render this with no transcript.", "voice_mode": "clone"},
            files={"reference_audio": ("reference.wav", b"fake-audio", "audio/wav")},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "clone mode requires prompt_text"


def test_cosyvoice3_service_reports_missing_model_files(monkeypatch, tmp_path):
    monkeypatch.setattr(cosyvoice3_service, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(cosyvoice3_service.importlib.util, "find_spec", lambda name: object() if name == "cosyvoice" else None)
    cosyvoice3_service._model = None
    cosyvoice3_service._load_error = None
    cosyvoice3_service._dependency_error = None

    with TestClient(cosyvoice3_service.app) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")

    assert health.status_code == 200
    assert "Missing required CosyVoice 3 file:" in health.json()["error"]
    assert ready.status_code == 503
    assert "Missing required CosyVoice 3 file:" in ready.json()["detail"]


def test_cosyvoice3_service_does_not_require_spk2info_for_official_snapshot(monkeypatch, tmp_path):
    model_dir = tmp_path / "Fun-CosyVoice3-0.5B-2512"
    blank_en_dir = model_dir / "CosyVoice-BlankEN"
    blank_en_dir.mkdir(parents=True)
    for filename in cosyvoice3_service.REQUIRED_MODEL_FILES:
        (model_dir / filename).write_bytes(b"fake")

    loaded_dirs: list[str] = []

    def _fake_load_model():
        fake_model = object()
        loaded_dirs.append(str(model_dir))
        cosyvoice3_service._model = fake_model
        return fake_model

    monkeypatch.setattr(cosyvoice3_service, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(cosyvoice3_service.importlib.util, "find_spec", lambda name: object() if name == "cosyvoice" else None)
    monkeypatch.setattr(cosyvoice3_service, "load_model", _fake_load_model)
    cosyvoice3_service._model = None
    cosyvoice3_service._load_error = None
    cosyvoice3_service._dependency_error = None
    cosyvoice3_service._load_task = None
    cosyvoice3_service._load_started_at = None

    with TestClient(cosyvoice3_service.app) as client:
        first_ready = client.get("/readyz")
        ready = None
        for _ in range(10):
            candidate = client.get("/readyz")
            if candidate.status_code == 200:
                ready = candidate
                break
            time.sleep(0.01)

    assert first_ready.status_code == 503
    assert "still loading" in first_ready.json()["detail"]
    assert ready is not None
    assert ready.status_code == 200
    assert ready.json()["resolved_model_dir"] == str(model_dir)
    assert loaded_dirs == [str(model_dir)]
