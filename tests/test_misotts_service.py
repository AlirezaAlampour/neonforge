from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SERVICE_APP_PATH = ROOT / "services" / "misotts" / "app.py"

spec = importlib.util.spec_from_file_location("misotts_service_app", SERVICE_APP_PATH)
assert spec is not None and spec.loader is not None
misotts_service = importlib.util.module_from_spec(spec)
sys.modules.setdefault("misotts_service_app", misotts_service)
spec.loader.exec_module(misotts_service)


class _FakeSynthesisResult:
    def __init__(self, *, audio_bytes: bytes | None = None, output_path: str | None = None, sample_rate: int = 24000):
        self.audio_bytes = audio_bytes
        self.output_path = output_path
        self.sample_rate = sample_rate


class _FakeProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def synthesize(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeSynthesisResult(audio_bytes=b"RIFFfake", sample_rate=24000)


def _reset_service_state() -> None:
    misotts_service.provider = None
    misotts_service.provider_loaded = False
    misotts_service.provider_loading = False
    misotts_service.load_error = None


def test_misotts_service_healthz_does_not_load_model(monkeypatch):
    _reset_service_state()
    load_calls: list[str] = []
    monkeypatch.setattr(misotts_service, "load_provider", lambda: load_calls.append("load"))
    monkeypatch.setattr(
        misotts_service,
        "_gpu_diagnostics",
        lambda: {
            "cuda_available": True,
            "gpu_device": "NVIDIA GB10",
            "gpu_free_gb": 20.7,
            "gpu_total_gb": 121.7,
            "top_gpu_process": {"pid": 4835, "process_name": "python", "used_memory_mib": 19479},
            "actionable_hint": "Stop Qwen/Vox/Jupyter/LLM service to free GPU memory.",
        },
    )

    with TestClient(misotts_service.app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "alive"
    assert payload["model_loaded"] is False
    assert payload["diagnostics"]["gpu_device"] == "NVIDIA GB10"
    assert payload["diagnostics"]["gpu_free_gb"] == 20.7
    assert load_calls == []


def test_misotts_service_runtime_health_reports_loading_state_without_loading_model():
    _reset_service_state()
    misotts_service.provider_loading = True

    with TestClient(misotts_service.app) as client:
        response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json()["runtime_status"] == "loading"


def test_misotts_service_runtime_health_reports_error_state_after_failed_load():
    _reset_service_state()
    misotts_service.load_error = "MisoTTS ran out of CUDA memory while loading or generating audio."

    with TestClient(misotts_service.app) as client:
        response = client.get("/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_status"] == "error"
    assert "CUDA memory" in payload["error"]


def test_misotts_service_plain_synthesis_returns_wav_bytes(monkeypatch):
    _reset_service_state()
    fake_provider = _FakeProvider()

    def fake_load_provider():
        misotts_service.provider = fake_provider
        misotts_service.provider_loaded = True
        return fake_provider

    monkeypatch.setattr(misotts_service, "load_provider", fake_load_provider)

    with TestClient(misotts_service.app) as client:
        response = client.post(
            "/synthesize",
            data={
                "text": "Hello from Miso.",
                "speaker": "1",
                "max_audio_length_ms": "9000",
            },
        )

    assert response.status_code == 200
    assert response.content == b"RIFFfake"
    assert fake_provider.calls == [
        {
            "text": "Hello from Miso.",
            "speaker": 1,
            "max_audio_length_ms": 9000,
            "prompt_text": None,
            "reference_audio": None,
            "output_wav_path": None,
        }
    ]


def test_misotts_service_rejects_prompt_audio_without_transcript(monkeypatch):
    _reset_service_state()
    fake_provider = _FakeProvider()

    def fake_load_provider():
        misotts_service.provider = fake_provider
        misotts_service.provider_loaded = True
        return fake_provider

    monkeypatch.setattr(misotts_service, "load_provider", fake_load_provider)

    with TestClient(misotts_service.app) as client:
        response = client.post(
            "/synthesize",
            data={"text": "Clone this voice."},
            files={"reference_audio": ("prompt.wav", b"RIFFfake", "audio/wav")},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "Prompt audio requires prompt_text"
    assert fake_provider.calls == []


def test_misotts_service_returns_json_when_output_path_is_requested(monkeypatch, tmp_path: Path):
    _reset_service_state()
    fake_provider = _FakeProvider()

    def fake_synthesize(**kwargs):
        fake_provider.calls.append(kwargs)
        return _FakeSynthesisResult(output_path=str(tmp_path / "miso.wav"), sample_rate=24000)

    fake_provider.synthesize = fake_synthesize

    def fake_load_provider():
        misotts_service.provider = fake_provider
        misotts_service.provider_loaded = True
        return fake_provider

    monkeypatch.setattr(misotts_service, "load_provider", fake_load_provider)

    with TestClient(misotts_service.app) as client:
        response = client.post(
            "/synthesize",
            data={
                "text": "Write this file.",
                "output_wav_path": str(tmp_path / "miso.wav"),
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "output_path": str(tmp_path / "miso.wav"),
        "sample_rate": 24000,
    }


def test_misotts_service_surfaces_missing_dependency_errors(monkeypatch):
    _reset_service_state()

    def fake_load_provider():
        raise ImportError("Install the optional MisoTTS runtime dependencies first")

    monkeypatch.setattr(misotts_service, "load_provider", fake_load_provider)

    with TestClient(misotts_service.app) as client:
        response = client.post("/synthesize", data={"text": "Hello from Miso."})

    assert response.status_code == 503
    assert "optional MisoTTS runtime dependencies" in response.json()["detail"]


def test_misotts_service_releases_cached_provider_state_after_failed_synthesis(monkeypatch):
    _reset_service_state()

    class _FailingProvider:
        def synthesize(self, **kwargs):
            raise RuntimeError(
                "Failed to download or resolve the MisoTTS model weights from Hugging Face. "
                "This runtime also needs access to the gated repo meta-llama/Llama-3.2-1B."
            )

    failing_provider = _FailingProvider()

    def fake_load_provider():
        misotts_service.provider = failing_provider
        misotts_service.provider_loaded = True
        return failing_provider

    monkeypatch.setattr(misotts_service, "load_provider", fake_load_provider)

    with TestClient(misotts_service.app) as client:
        response = client.post("/synthesize", data={"text": "Hello from Miso."})

    assert response.status_code == 503
    assert misotts_service.provider is None
    assert misotts_service.provider_loaded is False
    assert "meta-llama/Llama-3.2-1B" in response.json()["detail"]
