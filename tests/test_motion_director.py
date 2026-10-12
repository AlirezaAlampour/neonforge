import asyncio
import base64
import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))
import app as gateway
from motion_director import routes
from motion_director.planner import Annotation, LocalDirector, ShotRequest
from motion_director.renderers import H3Renderer, IMAGE_LORA, IMAGE_MODEL, WORKFLOW_PATH, build_h3_graph
from supervisor.resource_manager import ResourceManager, ServicePolicy, workload_minimum_gb

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


def test_planner_auto_manual_timing_and_original_coordinates():
    auto = LocalDirector().plan(ShotRequest(prompt="Orbit a traveler crossing the street", reference_ids=["ref-1"], duration=3))
    assert "orbit" in auto.camera.movement
    assert auto.frames == 73 and auto.width == 384
    assert auto.timing[0].start == 0 and auto.timing[-1].end == 3
    assert "<Picture 1>" in auto.renderer_prompt
    assert "180-degree" in auto.composition
    annotations = [Annotation(id="path", reference_id="ref-1", kind="path", points=[{"x": .1, "y": .8}, {"x": .7, "y": .2}]),
                   Annotation(id="actor", reference_id="ref-1", kind="subject", points=[{"x": .4, "y": .5}], label="traveler")]
    manual = LocalDirector().plan(ShotRequest(prompt="A traveler walks", reference_ids=["ref-1"], annotations=annotations, mode="manual"))
    assert manual.camera.paths == annotations[:1]
    assert manual.camera.targets == ["traveler"]
    assert "(0.10, 0.80)" in manual.renderer_prompt
    assert manual.conditioning == "prompt-guidance"
    assert manual.request.annotations == annotations


def test_dense_strokes_keep_vectors_without_expanding_encoder_context():
    points = [{"x": i / 255, "y": .5} for i in range(256)]
    shot = LocalDirector().plan(ShotRequest(prompt="Track the traveler", reference_ids=["r"], mode="manual",
                                          annotations=[Annotation(id="camera", reference_id="r", kind="path", points=points)]))
    assert len(shot.request.annotations[0].points) == 256
    assert shot.renderer_prompt.count(" → ") == 7
    assert "(0.00, 0.50)" in shot.renderer_prompt and "(1.00, 0.50)" in shot.renderer_prompt


@pytest.mark.parametrize("change", [{"prompt": " "}, {"reference_ids": []}, {"reference_ids": ["r", "r"]},
    {"reference_ids": ["../escape"]}, {"workflow": "image", "reference_ids": ["r", "r2"]}, {"duration": 20},
    {"annotations": [{"id": "x", "reference_id": "other", "kind": "subject", "points": [{"x": .2, "y": .3}]}]},
    {"annotations": [{"id": "x", "reference_id": "r", "kind": "arrow", "points": [{"x": .2, "y": .3}]}]},
    {"annotations": [{"id": "x", "reference_id": "r", "kind": "path", "points": [{"x": 2, "y": .3}, {"x": .4, "y": .8}]}]},
    {"annotations": [{"id": "x", "reference_id": "r", "kind": "subject", "points": [{"x": float('nan'), "y": .3}]}]},
    {"renderer": "grok"}])
def test_invalid_shots_fail_before_inference(change):
    with pytest.raises(ValidationError):
        ShotRequest.model_validate({"prompt": "A cinematic scene", "reference_ids": ["r"], **change})


def test_graph_uses_local_nodes_and_all_refs_and_image_mode():
    shot = LocalDirector().plan(ShotRequest(prompt="Track a character", reference_ids=["r1", "r2"], orientation="horizontal", duration=5))
    graph = build_h3_graph(shot, ["ref1.png", "ref2.png"], "job")
    assert graph["136"]["inputs"]["ref_images.ref_image_1"] == ["scale_1", 0]
    assert graph["136"]["inputs"]["length"] == 124
    assert graph["124"]["inputs"]["steps"] == 4
    assert all("cloud" not in n["class_type"].lower() for n in graph.values())
    image = LocalDirector().plan(ShotRequest(prompt="A first frame moves", reference_ids=["r"], workflow="image"))
    graph = build_h3_graph(image, ["first.png"], "job")
    assert graph["136"]["class_type"] == "MiniMaxH3ImageToVideo"
    assert graph["136"]["inputs"]["first_frame"] == ["scale_0", 0]
    assert graph["127"]["inputs"]["unet_name"] == IMAGE_MODEL
    assert graph["124"]["inputs"]["steps"] == 8
    assert "audio_vae" not in graph["136"]["inputs"]


class MockH3:
    def __init__(self):
        self.submissions = []
        self.releases = []
        self.claims = []
        self.busy = False
        self.fail_execution = False
        self.ambiguous = False
        self.output_path = "video.mp4"
        self.history_calls = 0
        self.image_weights = True

    def handle(self, request):
        path = request.url.path
        if path == "/object_info":
            graph = json.loads(WORKFLOW_PATH.read_text())
            schemas = {node["class_type"]: {"input": {"required": {}}} for node in graph.values()}
            schemas.update({"LoadImage": {}, "ResizeImagesByLongerEdge": {}, "MiniMaxH3ImageToVideo": {}, "MiniMaxH3AddGuide": {}, "LoadVideo": {}, "GetVideoComponents": {}})
            for node in graph.values():
                field = {"UNETLoader": "unet_name", "CLIPLoader": "clip_name", "VAELoader": "vae_name", "LoraLoaderModelOnly": "lora_name"}.get(node["class_type"])
                if field:
                    schemas[node["class_type"]]["input"]["required"].setdefault(field, [[]])[0].append(node["inputs"][field])
            if self.image_weights:
                schemas["UNETLoader"]["input"]["required"]["unet_name"][0].append(IMAGE_MODEL)
                schemas["LoraLoaderModelOnly"]["input"]["required"]["lora_name"][0].append(IMAGE_LORA)
            return httpx.Response(200, json=schemas)
        if path == "/workloads/status":
            return httpx.Response(200, json={"managed_services": {"h3": {}}})
        if path == "/queue":
            return httpx.Response(200, json={"queue_running": [[1, "unrelated-job"]] if self.busy else [], "queue_pending": []})
        if path == "/prepare/h3":
            claim = json.loads(request.content)
            self.claims.append(claim)
            assert workload_minimum_gb("h3", claim["workload_id"], frames=claim["frames"], width=claim["width"], height=claim["height"]) == 90
            return httpx.Response(200, json={"claim_id": claim["claim_id"], "state": "ready"})
        if path == "/release/h3":
            self.releases.append(json.loads(request.content))
            return httpx.Response(200, json={"released": True})
        if path == "/recheck/h3":
            return httpx.Response(200, json={"ready": True, "minimum_available_gb": 90})
        if path == "/upload/image":
            assert request.content
            return httpx.Response(200, json={"name": "uploaded.png", "subfolder": ""})
        if path == "/prompt":
            self.submissions.append(json.loads(request.content))
            if self.ambiguous:
                raise httpx.ReadTimeout("lost acknowledgement", request=request)
            return httpx.Response(200, json={"prompt_id": "test-prompt" if len(self.submissions) == 1 else f"test-prompt-{len(self.submissions)}", "node_errors": {}})
        if path.startswith("/history/test-prompt"):
            self.history_calls += 1
            status = {"completed": True, "status_str": "error" if self.fail_execution else "success", "messages": [["execution_error", {"exception_message": "mock failure"}]] if self.fail_execution else []}
            return httpx.Response(200, json={path.rsplit("/",1)[-1]: {"status": status, "outputs": {"92": {"images": [{"filename": self.output_path, "type": "output", "subfolder": "motion_director"}]}}}})
        if path == "/view":
            return httpx.Response(200, content=b"\x00\x00\x00\x18ftypmp42mock-video", headers={"Content-Type": "video/mp4"})
        raise AssertionError(f"Unexpected local mock request: {request.method} {path}")


@pytest.fixture
def studio(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.sqlite3", connect_args={"check_same_thread": False})
    session = sessionmaker(bind=engine, expire_on_commit=False)
    gateway.Base.metadata.create_all(engine)
    fake = MockH3()
    monkeypatch.setattr(gateway, "SessionLocal", session)
    monkeypatch.setattr(gateway, "db_engine", engine)
    monkeypatch.setattr(gateway, "OUTPUTS_ROOT", tmp_path / "outputs")
    monkeypatch.setattr(gateway, "COMFYUI_UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(gateway, "H3_COMFYUI_URL", "http://local-h3")
    monkeypatch.setattr(gateway, "SUPERVISOR_URL", "http://local-supervisor")
    monkeypatch.setattr(gateway, "get_memory_status", lambda: {"available_gb": 95})
    monkeypatch.setattr(gateway, "rdb", None)
    monkeypatch.setattr(gateway, "COMFYUI_POLL_INTERVAL_SEC", .01)
    monkeypatch.setattr(gateway, "gpu_heavy_sem", asyncio.Semaphore(1))

    @asynccontextmanager
    async def lifespan(app):
        gateway.http_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
        yield
        await routes.shutdown_motion_jobs()
        await gateway.http_client.aclose()

    monkeypatch.setattr(gateway.app.router, "lifespan_context", lifespan)
    with TestClient(gateway.app) as client:
        yield client, fake
    engine.dispose()


def upload(client):
    response = client.post("/api/v1/motion-director/references", files={"file": ("original.png", PNG, "image/png")})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def await_job(client, job_id):
    for _ in range(100):
        result = client.get(f"/api/v1/motion-director/jobs/{job_id}").json()
        if result["status"] in {"completed", "failed"}:
            return result
        time.sleep(.02)
    raise AssertionError("Mock inference did not terminate")


@pytest.mark.parametrize("workflow", ["image", "reference"])
def test_upload_plan_job_preview_download_vertical_slice(studio, workflow):
    client, fake = studio
    ref = upload(client)
    annotations = [{"id": "camera", "reference_id": ref, "kind": "arrow", "points": [{"x": .1, "y": .5}, {"x": .8, "y": .5}], "label": "pan right"}]
    body = {"prompt": "Track the traveler", "reference_ids": [ref], "annotations": annotations, "mode": "manual", "workflow": workflow}
    plan = client.post("/api/v1/motion-director/plan", json=body)
    assert plan.status_code == 200
    assert plan.json()["request"]["annotations"] == annotations
    assert client.get(f"/api/v1/motion-director/references/{ref}").content == PNG
    accepted = client.post("/api/v1/motion-director/jobs", json=body)
    assert accepted.status_code == 202, accepted.text
    result = await_job(client, accepted.json()["job_id"])
    assert result["status"] == "completed", result
    assert client.get(result["preview_url"]).content.endswith(b"mock-video")
    download = client.get(result["download_url"])
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
    assert len(fake.submissions) == len(fake.claims) == len(fake.releases) == 1
    assert client.get(f"/api/v1/motion-director/references/{ref}").content == PNG
    assert client.get("/api/v1/history?service=motion_director").json()["items"][0]["parameters"]["request"]["annotations"] == annotations
    assert client.get("/healthz").json()["status"] == "alive"
    assert client.get("/api/v1/motion-director/capabilities").json()["grok"]["available"] is False


def test_missing_and_invalid_references_and_busy_h3_are_blocked(studio, monkeypatch):
    client, fake = studio
    assert client.post("/api/v1/motion-director/references", files={"file": ("bad.png", b"not an image", "image/png")}).status_code == 400
    assert client.post("/api/v1/motion-director/plan", json={"prompt": "scene", "reference_ids": ["missing"]}).status_code == 404
    ref = upload(client)
    fake.busy = True
    assert client.post("/api/v1/motion-director/jobs", json={"prompt": "scene", "reference_ids": [ref]}).status_code == 503
    fake.busy = False
    monkeypatch.setattr(gateway, "get_memory_status", lambda: {"available_gb": 85})
    assert client.post("/api/v1/motion-director/jobs", json={"prompt": "scene", "reference_ids": [ref]}).status_code == 503
    assert not fake.submissions and not fake.claims


def test_first_frame_mode_requires_its_actual_weights(studio):
    client, fake = studio
    fake.image_weights = False
    caps = client.get("/api/v1/motion-director/capabilities").json()
    assert caps["h3"]["workflows"]["reference"]["available"] is True
    assert caps["h3"]["workflows"]["image"]["available"] is False
    ref = upload(client)
    result = client.post("/api/v1/motion-director/jobs", json={"prompt": "scene", "reference_ids": [ref], "workflow": "image"})
    assert result.status_code == 503 and "fl2va" in result.text
    assert not fake.submissions


@pytest.mark.parametrize("error", ["fail_execution", "ambiguous", "unsafe_output"])
def test_render_errors_release_only_confirmed_terminal_jobs(studio, error):
    client, fake = studio
    if error == "unsafe_output":
        fake.output_path = "../bad.mp4"
    else:
        setattr(fake, error, True)
    ref = upload(client)
    accepted = client.post("/api/v1/motion-director/jobs", json={"prompt": "scene", "reference_ids": [ref]})
    result = await_job(client, accepted.json()["job_id"])
    assert result["status"] == "failed" and result["error"]
    assert len(fake.releases) == (0 if error == "ambiguous" else 1)
    assert not result["preview_url"]


def test_accepted_prompt_reconciliation_never_resubmits(studio):
    client, fake = studio
    fake.fail_execution = True
    ref = upload(client)
    accepted = client.post("/api/v1/motion-director/jobs", json={"prompt": "scene", "reference_ids": [ref]})
    result = await_job(client, accepted.json()["job_id"])
    assert result["status"] == "failed"
    fake.fail_execution = False
    client.post(f"/api/v1/motion-director/jobs/{result['job_id']}/reconcile")
    result = await_job(client, accepted.json()["job_id"])
    assert result["status"] == "completed"
    assert len(fake.submissions) == 1


def test_external_h3_claim_has_no_lifecycle_authority_or_reclamation():
    policy = ServicePolicy("h3", "", None, "http://h3", "/queue", "video", 90, 0, lifecycle_managed=False)
    memory = {"available_gb": 85}
    manager = ResourceManager(compose_dir="/tmp", policies={"h3": policy}, protected_services=set(), memory_reader=lambda: memory, memory_wait_sec=0)

    async def scenario():
        with pytest.raises(RuntimeError, match="Need 90"):
            await manager.prepare("h3")
        assert not manager.claims
        memory["available_gb"] = 95
        async def ready(policy):
            return None
        manager._wait_ready = ready
        prepared = await manager.prepare("h3", job_id="job")
        assert prepared["stopped_services"] == []
        with pytest.raises(RuntimeError, match="Another generation"):
            await manager.prepare("h3")
        with pytest.raises(RuntimeError, match="forbidden"):
            await manager._stop("h3", reason="idle")
        with pytest.raises(RuntimeError, match="forbidden"):
            await manager._start(policy)
        await manager.release("h3", prepared["claim_id"])
        assert await manager.idle_sweep() == []
        assert manager.metrics["containers_started"] == 0
        assert manager.metrics["containers_stopped_for_memory"] == 0
        await manager.close()
    asyncio.run(scenario())


def test_h3_workload_bounds_cannot_bypass_supervisor():
    for frames in [56, 73, 124]:
        assert workload_minimum_gb("h3", "motion-h3", frames=frames, width=384, height=672) == 90
    for args in [(56, 1344, 768), (1000, 384, 672)]:
        with pytest.raises(ValueError):
            workload_minimum_gb("h3", "motion-h3", frames=args[0], width=args[1], height=args[2])


def test_external_h3_queue_blocks_other_gpu_admission():
    h3 = ServicePolicy("h3", "", None, "http://h3", "/queue", "video", 90, 0, lifecycle_managed=False)
    voice = ServicePolicy("misotts", "ai-misotts", "voice", "http://misotts", "/healthz", "voice", 40, 300)
    manager = ResourceManager(compose_dir="/tmp", policies={"h3": h3, "misotts": voice},
                              protected_services=set(), memory_reader=lambda: {"available_gb": 95}, memory_wait_sec=0)

    async def scenario():
        await manager._http.aclose()
        manager._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"queue_running": [[1, "external"]]})))
        with pytest.raises(RuntimeError, match="External H3 is busy"):
            await manager.prepare("misotts")
        assert not manager.claims and manager.metrics["containers_started"] == 0
        await manager.close()
    asyncio.run(scenario())
