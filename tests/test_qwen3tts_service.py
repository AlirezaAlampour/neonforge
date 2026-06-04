from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SERVICE_APP_PATH = ROOT / "services" / "qwen3tts" / "app.py"

spec = importlib.util.spec_from_file_location("qwen3tts_service_app", SERVICE_APP_PATH)
assert spec is not None and spec.loader is not None
qwen3tts_service = importlib.util.module_from_spec(spec)
sys.modules.setdefault("qwen3tts_service_app", qwen3tts_service)
spec.loader.exec_module(qwen3tts_service)


class _FakeQwenModel:
    def __init__(self, mode_name: str) -> None:
        self.mode_name = mode_name
        self.calls: list[tuple[str, dict[str, object]]] = []

    def generate_voice_clone(self, **kwargs):
        self.calls.append(("generate_voice_clone", kwargs))
        return [[0.0, 0.0]], 24000

    def generate_voice_design(self, **kwargs):
        self.calls.append(("generate_voice_design", kwargs))
        return [[0.0, 0.0]], 24000


@contextmanager
def _service_client(monkeypatch):
    clone_model = _FakeQwenModel("clone")
    design_model = _FakeQwenModel("design")

    monkeypatch.setattr(qwen3tts_service, "_load_clone_model", lambda: clone_model)
    monkeypatch.setattr(qwen3tts_service, "_load_design_model", lambda: design_model)
    monkeypatch.setattr(qwen3tts_service.sf, "write", lambda buffer, wav, sample_rate, format: buffer.write(b"RIFFfake"))

    with TestClient(qwen3tts_service.app) as client:
        yield client, clone_model, design_model


def test_qwen3tts_service_clone_mode_uses_official_clone_call(monkeypatch):
    with _service_client(monkeypatch) as (client, clone_model, _design_model):
        response = client.post(
            "/synthesize",
            data={
                "text": "Clone this speaker into a new script.",
                "voice_mode": "clone",
                "prompt_text": "This is the exact reference transcript.",
            },
            files={"reference_audio": ("reference.wav", b"fake-audio", "audio/wav")},
        )

    assert response.status_code == 200
    assert response.content == b"RIFFfake"
    assert clone_model.calls == [
        (
            "generate_voice_clone",
            {
                "text": "Clone this speaker into a new script.",
                "language": "English",
                "ref_audio": clone_model.calls[0][1]["ref_audio"],
                "ref_text": "This is the exact reference transcript.",
            },
        )
    ]
    assert str(clone_model.calls[0][1]["ref_audio"]).endswith(".wav")


def test_qwen3tts_service_design_mode_uses_official_voice_design_call(monkeypatch):
    with _service_client(monkeypatch) as (client, _clone_model, design_model):
        response = client.post(
            "/synthesize",
            data={
                "text": "Invent a warm, documentary-style narrator.",
                "voice_mode": "design",
                "style_text": "Warm, steady, mature documentary narrator.",
            },
        )

    assert response.status_code == 200
    assert response.content == b"RIFFfake"
    assert design_model.calls == [
        (
            "generate_voice_design",
            {
                "text": "Invent a warm, documentary-style narrator.",
                "language": "English",
                "instruct": "Warm, steady, mature documentary narrator.",
            },
        )
    ]


def test_qwen3tts_service_readyz_accepts_official_snapshot_layout_without_standalone_tokenizer(monkeypatch, tmp_path):
    clone_dir = tmp_path / "Qwen3-TTS-12Hz-0.6B-Base"
    design_dir = tmp_path / "Qwen3-TTS-12Hz-1.7B-VoiceDesign"
    for model_dir in (clone_dir, design_dir):
        (model_dir / "speech_tokenizer").mkdir(parents=True)
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        (model_dir / "speech_tokenizer" / "config.json").write_text("{}", encoding="utf-8")
        (model_dir / "model-00001-of-00001.safetensors").write_bytes(b"fake")

    loaded_paths: list[str] = []

    monkeypatch.setattr(qwen3tts_service, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(qwen3tts_service.importlib.util, "find_spec", lambda name: object() if name == "qwen_tts" else None)
    monkeypatch.setattr(qwen3tts_service, "_build_model", lambda model_dir: loaded_paths.append(str(model_dir)) or object())
    qwen3tts_service._clone_model = None
    qwen3tts_service._design_model = None
    qwen3tts_service._clone_load_error = None
    qwen3tts_service._design_load_error = None
    qwen3tts_service._dependency_error = None

    with TestClient(qwen3tts_service.app) as client:
        ready = client.get("/readyz")

    assert ready.status_code == 200
    payload = ready.json()
    assert payload["status"] == "ready"
    assert payload["clone_model_loaded"] is True
    assert payload["design_model_loaded"] is True
    assert payload["inventory"]["clone_model"] == str(clone_dir)
    assert payload["inventory"]["design_model"] == str(design_dir)
    assert loaded_paths == [str(clone_dir), str(design_dir)]


def test_qwen3tts_service_reports_missing_model_layout_clearly(monkeypatch, tmp_path):
    monkeypatch.setattr(qwen3tts_service, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(qwen3tts_service.importlib.util, "find_spec", lambda name: object() if name == "qwen_tts" else None)
    qwen3tts_service._clone_model = None
    qwen3tts_service._design_model = None
    qwen3tts_service._clone_load_error = None
    qwen3tts_service._design_load_error = None
    qwen3tts_service._dependency_error = None

    with TestClient(qwen3tts_service.app) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json()["clone_validation_error"] == f"Clone model directory is missing under {tmp_path}"
    assert ready.status_code == 503
    assert ready.json()["detail"] == f"Clone model directory is missing under {tmp_path}"
