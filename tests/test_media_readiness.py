import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _install_torch_stub_if_needed():
    try:
        import torch  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["torch"] = types.SimpleNamespace(
            nn=types.SimpleNamespace(Module=object),
            cuda=types.SimpleNamespace(
                empty_cache=lambda: None,
                reset_peak_memory_stats=lambda: None,
            ),
        )


def _load_module(name: str, relative_path: str):
    _install_torch_stub_if_needed()
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_lipsync_preflight_rejects_health_only_install(monkeypatch, tmp_path: Path):
    service = _load_module("neonforge_lipsync_readiness", "services/lipsync/app.py")
    monkeypatch.setattr(service, "VIDEO_RETALKING_DIR", tmp_path / "runtime")
    monkeypatch.setattr(service, "MODEL_DIR", tmp_path / "models")

    result = service.backend_preflight()

    assert result["available"] is False
    assert result["status"] == "runtime_error"
    assert len(result["missing"]) == 2


def test_lipsync_preflight_accepts_runtime_and_checkpoint(monkeypatch, tmp_path: Path):
    service = _load_module("neonforge_lipsync_ready", "services/lipsync/app.py")
    runtime = tmp_path / "runtime"
    checkpoints = tmp_path / "models" / "video-retalking" / "checkpoints"
    runtime.mkdir()
    checkpoints.mkdir(parents=True)
    (runtime / "inference.py").write_text("# test fixture\n", encoding="utf-8")
    (checkpoints / "model.pth").write_bytes(b"fixture")
    monkeypatch.setattr(service, "VIDEO_RETALKING_DIR", runtime)
    monkeypatch.setattr(service, "MODEL_DIR", tmp_path / "models")

    result = service.backend_preflight()

    assert result["available"] is True
    assert result["status"] == "idle"


def test_liveportrait_preflight_reports_missing_adapter_and_weights(monkeypatch, tmp_path: Path):
    service = _load_module("neonforge_liveportrait_readiness", "services/liveportrait/app.py")
    source = tmp_path / "LivePortrait"
    source.mkdir()
    monkeypatch.setattr(service, "SOURCE_DIR", source)
    monkeypatch.setattr(service, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(service.importlib.util, "find_spec", lambda _name: None)

    result = service.runtime_preflight()

    assert result["available"] is False
    assert result["status"] == "missing_model"
    assert "python module liveportrait.api" in result["missing"]
