import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as gateway_app


class _FakeComfyResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeComfyClient:
    async def post(self, url: str, json: dict, timeout=None):
        return _FakeComfyResponse({"prompt_id": "prompt-test-1", "node_errors": None})


async def _async_noop(*args, **kwargs):
    return None


def _insert_asset(
    session_factory,
    uploads_root: Path,
    *,
    kind: str,
    original_filename: str,
    stored_filename: str,
    content: bytes,
) -> gateway_app.ComfyUIAssetRecord:
    relative_path = str(Path(kind) / stored_filename)
    full_path = uploads_root / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(content)

    record = gateway_app.ComfyUIAssetRecord(
        id=str(uuid.uuid4()),
        kind=kind,
        original_filename=original_filename,
        stored_filename=stored_filename,
        relative_path=relative_path,
        content_type=None,
        size_bytes=len(content),
        created_at=gateway_app._now_utc(),
    )
    with session_factory() as db:
        db.add(record)
        db.commit()
        db.refresh(record)
    return record


def _build_validation_payload() -> dict:
    return {
        "available": [],
        "missing": [],
        "warnings": [],
        "configured_roots": [],
        "scanned_roots": [],
    }


def _wait_for_job_completion(client: TestClient, job_id: str, timeout_sec: float = 2.0) -> dict:
    deadline = time.time() + timeout_sec
    last_payload = None
    while time.time() < deadline:
        response = client.get(f"/api/v1/comfyui/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        last_payload = payload
        if payload["status"] == gateway_app.JobStatus.COMPLETED.value:
            return payload
        if payload["status"] == gateway_app.JobStatus.FAILED.value:
            raise AssertionError(f"ComfyUI job failed during test: {payload}")
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for job completion: {last_payload}")


def test_create_comfyui_job_with_valid_asset_ids(tmp_path, monkeypatch):
    outputs_root = tmp_path / "outputs"
    assets_root = tmp_path / "assets"
    uploads_root = assets_root / "comfyui" / "uploads"
    input_root = outputs_root / "comfyui" / "input"
    debug_root = tmp_path / "debug"
    db_path = tmp_path / "history.sqlite3"

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    gateway_app.Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(gateway_app, "OUTPUTS_ROOT", outputs_root)
    monkeypatch.setattr(gateway_app, "ASSETS_ROOT", assets_root)
    monkeypatch.setattr(gateway_app, "COMFYUI_UPLOADS_DIR", uploads_root)
    monkeypatch.setattr(gateway_app, "COMFYUI_INPUT_DIR", input_root)
    monkeypatch.setattr(gateway_app, "COMFYUI_DEBUG_DIR", debug_root)
    monkeypatch.setattr(gateway_app, "HISTORY_DB_PATH", db_path)
    monkeypatch.setattr(gateway_app, "db_engine", engine)
    monkeypatch.setattr(gateway_app, "SessionLocal", session_local)
    monkeypatch.setattr(gateway_app, "rdb", None)
    monkeypatch.setattr(gateway_app, "http_client", _FakeComfyClient())
    monkeypatch.setattr(gateway_app, "_template_manifest_cache", None)
    monkeypatch.setattr(gateway_app, "_template_manifest_cache_key", None)
    monkeypatch.setattr(gateway_app, "validate_template_models", lambda manifest: _build_validation_payload())
    monkeypatch.setattr(gateway_app, "memory_allows_job", lambda tier: (True, {"used_pct": 12.5}, "ok"))
    monkeypatch.setattr(gateway_app, "ensure_comfyui_reachable", _async_noop)
    monkeypatch.setattr(gateway_app, "record_service_activity", _async_noop)
    monkeypatch.setattr(gateway_app, "sync_comfyui_job_to_store", _async_noop)

    async def _fake_history_entry(prompt_id: str):
        return {"status": {"completed": True}}

    monkeypatch.setattr(
        gateway_app,
        "get_comfyui_history_entry",
        _fake_history_entry,
    )
    monkeypatch.setattr(
        gateway_app,
        "extract_output_from_history",
        lambda patched_prompt, history_entry, manifest: {
            "relative_path": "comfyui/test-output.mp4",
            "node_id": "30",
            "filename": "test-output.mp4",
        },
    )

    @asynccontextmanager
    async def test_lifespan(app):
        outputs_root.mkdir(parents=True, exist_ok=True)
        uploads_root.mkdir(parents=True, exist_ok=True)
        input_root.mkdir(parents=True, exist_ok=True)
        debug_root.mkdir(parents=True, exist_ok=True)
        gateway_app.Base.metadata.create_all(bind=gateway_app.db_engine)
        gateway_app.rdb = None
        gateway_app.http_client = _FakeComfyClient()
        yield
        gateway_app.active_comfyui_tasks.clear()

    monkeypatch.setattr(gateway_app.app.router, "lifespan_context", test_lifespan)
    gateway_app.active_comfyui_tasks.clear()

    reference_asset = _insert_asset(
        session_local,
        uploads_root,
        kind="image",
        original_filename="reference.png",
        stored_filename="reference.png",
        content=b"reference-image",
    )
    driving_asset = _insert_asset(
        session_local,
        uploads_root,
        kind="video",
        original_filename="driving.mp4",
        stored_filename="driving.mp4",
        content=b"driving-video",
    )

    with TestClient(gateway_app.app) as client:
        create_response = client.post(
            "/api/v1/comfyui/jobs",
            json={
                "template_id": "wan-character-swap",
                "inputs": {
                    "reference_image": reference_asset.id,
                    "driving_video": driving_asset.id,
                },
                "params": {
                    "seed": 7,
                    "steps": 6,
                    "cfg": 1,
                    "denoise_strength": 5,
                    "frame_rate": 16,
                },
            },
        )

        assert create_response.status_code == 200
        created = create_response.json()
        assert created["status"] == gateway_app.JobStatus.QUEUED.value
        assert created["template_id"] == "wan-character-swap"

        job_payload = _wait_for_job_completion(client, created["job_id"])
        assert job_payload["status"] == gateway_app.JobStatus.COMPLETED.value
        assert job_payload["history_id"]
        assert job_payload["result_path"] == "comfyui/test-output.mp4"

    with session_local() as db:
        job_row = db.get(gateway_app.ComfyUIJobRecordDB, created["job_id"])
        history_rows = db.execute(select(gateway_app.GenerationHistory)).scalars().all()

    assert job_row is not None
    assert job_row.status == gateway_app.JobStatus.COMPLETED.value
    assert len(history_rows) == 1
    assert history_rows[0].job_id == created["job_id"]

    engine.dispose()


def test_create_comfyui_job_with_stale_asset_id_returns_404(tmp_path, monkeypatch):
    outputs_root = tmp_path / "outputs"
    assets_root = tmp_path / "assets"
    uploads_root = assets_root / "comfyui" / "uploads"
    input_root = outputs_root / "comfyui" / "input"
    debug_root = tmp_path / "debug"
    db_path = tmp_path / "history.sqlite3"

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    gateway_app.Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(gateway_app, "OUTPUTS_ROOT", outputs_root)
    monkeypatch.setattr(gateway_app, "ASSETS_ROOT", assets_root)
    monkeypatch.setattr(gateway_app, "COMFYUI_UPLOADS_DIR", uploads_root)
    monkeypatch.setattr(gateway_app, "COMFYUI_INPUT_DIR", input_root)
    monkeypatch.setattr(gateway_app, "COMFYUI_DEBUG_DIR", debug_root)
    monkeypatch.setattr(gateway_app, "HISTORY_DB_PATH", db_path)
    monkeypatch.setattr(gateway_app, "db_engine", engine)
    monkeypatch.setattr(gateway_app, "SessionLocal", session_local)
    monkeypatch.setattr(gateway_app, "rdb", None)
    monkeypatch.setattr(gateway_app, "http_client", _FakeComfyClient())
    monkeypatch.setattr(gateway_app, "_template_manifest_cache", None)
    monkeypatch.setattr(gateway_app, "_template_manifest_cache_key", None)
    monkeypatch.setattr(gateway_app, "validate_template_models", lambda manifest: _build_validation_payload())

    @asynccontextmanager
    async def test_lifespan(app):
        outputs_root.mkdir(parents=True, exist_ok=True)
        uploads_root.mkdir(parents=True, exist_ok=True)
        input_root.mkdir(parents=True, exist_ok=True)
        debug_root.mkdir(parents=True, exist_ok=True)
        gateway_app.Base.metadata.create_all(bind=gateway_app.db_engine)
        gateway_app.rdb = None
        gateway_app.http_client = _FakeComfyClient()
        yield
        gateway_app.active_comfyui_tasks.clear()

    monkeypatch.setattr(gateway_app.app.router, "lifespan_context", test_lifespan)
    gateway_app.active_comfyui_tasks.clear()

    reference_asset = _insert_asset(
        session_local,
        uploads_root,
        kind="image",
        original_filename="reference.png",
        stored_filename="reference.png",
        content=b"reference-image",
    )

    with TestClient(gateway_app.app) as client:
        response = client.post(
            "/api/v1/comfyui/jobs",
            json={
                "template_id": "wan-character-swap",
                "inputs": {
                    "reference_image": reference_asset.id,
                    "driving_video": "missing-video-asset-id",
                },
                "params": {},
            },
        )

        assert response.status_code == 404
        assert response.json() == {
            "detail": {
                "error": "ComfyUI asset not found.",
                "asset_id": "missing-video-asset-id",
                "input_id": "driving_video",
                "template_id": "wan-character-swap",
            }
        }

    with session_local() as db:
        jobs = db.execute(select(gateway_app.ComfyUIJobRecordDB)).scalars().all()

    assert jobs == []

    engine.dispose()
