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
    monkeypatch.setattr(service, "SOURCE_DIR", tmp_path / "runtime")
    monkeypatch.setattr(service, "CHECKPOINT_DIR", tmp_path / "checkpoints")

    available, missing = service._preflight()

    assert available is False
    assert len(missing) == 4


def test_lipsync_preflight_accepts_runtime_and_checkpoint(monkeypatch, tmp_path: Path):
    service = _load_module("neonforge_lipsync_ready", "services/lipsync/app.py")
    runtime = tmp_path / "runtime"
    checkpoints = tmp_path / "checkpoints"
    (runtime / "scripts").mkdir(parents=True)
    (runtime / "configs" / "unet").mkdir(parents=True)
    (checkpoints / "whisper").mkdir(parents=True)
    (runtime / "scripts" / "inference.py").write_text("# test fixture\n", encoding="utf-8")
    (runtime / "configs" / "unet" / "stage2_512.yaml").write_text("# fixture\n", encoding="utf-8")
    (checkpoints / "latentsync_unet.pt").write_bytes(b"fixture")
    (checkpoints / "whisper" / "tiny.pt").write_bytes(b"fixture")
    monkeypatch.setattr(service, "SOURCE_DIR", runtime)
    monkeypatch.setattr(service, "CHECKPOINT_DIR", checkpoints)

    available, missing = service._preflight()

    assert available is True
    assert missing == []


def test_lipsync_rejects_unbounded_media_before_inference():
    import pytest
    from fastapi import HTTPException
    service = _load_module("neonforge_lipsync_bounds", "services/lipsync/app.py")
    probe = {"format": {"duration": "2.08"}, "streams": [{"codec_type": "video", "width": 1080, "height": 1920, "r_frame_rate": "25/1"}]}
    service._validate_media_probe(probe, video=True)
    probe["format"]["duration"] = "600"
    with pytest.raises(HTTPException, match="10 seconds"):
        service._validate_media_probe(probe, video=True)
    probe["format"]["duration"] = "2"
    probe["streams"][0]["width"] = 7680
    with pytest.raises(HTTPException, match="1080p"):
        service._validate_media_probe(probe, video=True)
