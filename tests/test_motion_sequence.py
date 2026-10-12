import asyncio
from pathlib import Path
import shutil
import time

import httpx
import pytest

from test_motion_director import studio, upload, await_job, gateway
from motion_director import media, routes
from motion_director.planner import LocalDirector, ShotRequest, segment_shot
from motion_director.renderers import build_h3_graph
from motion_director.renderers import H3Renderer
from gateway.motion_director import host_control, hermes_grok


@pytest.mark.parametrize("duration,count", [(5, 1), (10, 2), (15, 3), (30, 6)])
def test_sequential_generation_has_one_scene_plan_and_bounded_continuous_clips(studio, monkeypatch, duration, count):
    client, fake = studio
    ref = upload(client)
    calls = []
    async def host(runtime, route, payload=None):
        calls.append(route)
        plan = LocalDirector().plan(ShotRequest.model_validate(payload["shot"]))
        plan.planner = "claude-code-opus"
        return plan.model_dump()
    monkeypatch.setattr(routes, "host_request", host)
    async def context(previous, folder, index, width, height):
        folder.mkdir(parents=True, exist_ok=True)
        image, video = folder / f"boundary-{index}.png", folder / f"context-{index}.mp4"
        image.write_bytes(b"mock-context"); video.write_bytes(b"mock-video")
        return image, video
    async def trim(source, target, duration, skip):
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
    async def assemble(paths, target, duration):
        assert len(paths) == count
        shutil.copyfile(paths[0], target)
    monkeypatch.setattr(media, "continuation", context)
    monkeypatch.setattr(media, "trim", trim)
    monkeypatch.setattr(media, "assemble", assemble)
    accepted = client.post("/api/v1/motion-director/jobs", json={"prompt": "A slow cinematic portrait", "reference_ids": [ref], "duration": duration, "director": "opus"})
    assert accepted.status_code == 202, accepted.text
    result = await_job(client, accepted.json()["job_id"])
    assert result["status"] == "completed", result
    assert result["completed_segments"] == result["total_segments"] == count
    assert len(fake.submissions) == count and len(fake.claims) == len(fake.releases) == 1
    assert calls == ["claude/plan"]
    for i, submitted in enumerate(fake.submissions):
        graph = submitted["prompt"]
        assert graph["136"]["inputs"]["length"] == 124
        assert graph["129"]["inputs"]["noise_seed"] == 42 + i
        if i:
            assert graph["136"]["inputs"]["ref_videos.ref_video_0"] == ["context_frames", 0]
            assert graph["boundary_guide"]["inputs"]["frame_idx"] == 0
            assert graph["126"]["inputs"]["conditioning"] == ["boundary_guide", 0]
    # Reconcile completed work never submits or plans again.
    client.post(f"/api/v1/motion-director/jobs/{result['job_id']}/reconcile")
    assert len(fake.submissions) == count and calls == ["claude/plan"]


def test_scene_segments_have_local_timing_and_preserve_reference_ids():
    scene = LocalDirector().plan(ShotRequest(prompt="A continuous portrait", reference_ids=["r"], duration=30))
    clip = segment_shot(scene, 5)
    assert clip.request.duration == 5 and clip.frames == 124
    assert clip.timing[-1].end == 5 and clip.reference_ids == scene.reference_ids
    assert "25–30s" in clip.renderer_prompt and "<Video 1>" in clip.renderer_prompt
    with pytest.raises(ValueError): segment_shot(scene, 6)


def test_missing_supervisor_host_connection_disables_generate(studio, monkeypatch):
    client, fake = studio
    original = fake.handle
    def response(request):
        if request.url.path == "/workloads/status":
            return httpx.Response(200, json={"managed_services":{"h3":{}},"host":{"gpu":{},"blockers":["Host coordination unavailable"]}})
        return original(request)
    monkeypatch.setattr(fake,"handle",response)
    # The MockTransport retains its bound handler, so replace only this client.
    async def replace():
        await gateway.http_client.aclose()
        gateway.http_client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    client.portal.call(replace)
    cap=client.get("/api/v1/motion-director/capabilities").json()
    assert not cap["h3"]["scheduler_ready"] and not cap["h3"]["render_ready"]


@pytest.mark.parametrize("duration", [2, 10])
def test_gateway_cancellation_during_submission_retains_exclusive_claim(studio, monkeypatch, duration):
    client, fake = studio
    entered = []
    async def uncertain_submit(*args, **kwargs):
        entered.append(True)
        await asyncio.sleep(300)
    monkeypatch.setattr(H3Renderer, "submit", uncertain_submit)
    ref = upload(client)
    assert client.post("/api/v1/motion-director/jobs", json={"prompt":"scene", "reference_ids":[ref], "duration":duration}).status_code == 202
    for _ in range(100):
        if entered: break
        time.sleep(.01)
    assert entered
    client.portal.call(routes.shutdown_motion_jobs)
    assert len(fake.claims) == 1 and not fake.releases


def test_idle_h3_release_recovers_memory_without_interrupting_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(host_control, "STATE_DIR", tmp_path)
    available = {"available_gb": 58}
    monkeypatch.setattr(host_control, "memory", lambda: dict(available))
    coordinator = host_control.Coordinator()
    coordinator.claims = {"job": {"service": "h3", "at": 1}}
    coordinator.token = "mock-lease"
    calls = []
    async def lease(route, payload=None):
        calls.append(route)
        return {"snapshot": {}} if route == "state" else {"ok": True, "restored": {"errors": []}}
    coordinator.lease = lease
    async def scenario():
        await coordinator.http.aclose()
        def handle(request):
            if request.url.path == "/queue": return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
            assert request.url.path == "/free"
            available["available_gb"] = 97
            return httpx.Response(200, json={})
        coordinator.http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        result = await coordinator.release("job")
        assert result["released"] and available["available_gb"] >= 90
        assert not coordinator.claims and calls[-1] == "release"
        await coordinator.close()
    asyncio.run(scenario())


def test_grok_import_is_local_only_and_preserves_source(studio, monkeypatch):
    client, fake = studio
    async def probe(path):
        return {"streams": [{"codec_type": "video", "width": 384, "height": 672}], "format": {"duration": "10"}}
    async def normalize(source, target, width, height): shutil.copyfile(source, target)
    monkeypatch.setattr(media, "probe", probe); monkeypatch.setattr(media, "normalize_import", normalize)
    content = b"\x00\x00\x00\x18ftypmp42manual-mock-video"
    response = client.post("/api/v1/motion-director/imports/grok", files={"file": ("grok.mp4", content, "video/mp4")})
    assert response.status_code == 200, response.text
    assert response.json()["service"] == "grok" and response.json()["shot"]["planner"] == "manual-import"
    assert client.get(response.json()["preview_url"]).content == content
    assert not fake.submissions and not fake.claims
    assert list(gateway.OUTPUTS_ROOT.glob("motion_director/imports/*original.mp4"))[0].read_bytes() == content
    assert client.post("/api/v1/motion-director/imports/grok", files={"file": ("bad.mp4", b"bad", "video/mp4")}).status_code == 400


def test_real_cpu_media_assembly_is_exact_and_continuation_is_bounded(tmp_path):
    if not shutil.which("ffmpeg"): pytest.skip("Existing FFmpeg unavailable")
    async def scenario():
        source = tmp_path / "source.mp4"
        await media.command("ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=navy:s=384x672:r=24:d=5.166667", "-c:v", "libx264", "-threads", "2", str(source))
        clips = [tmp_path / "first.mp4", tmp_path / "second.mp4"]
        await media.trim(source, clips[0], 5, False)
        image, context = await media.continuation(clips[0], tmp_path, 1, 384, 672)
        assert image.is_file() and float((await media.probe(context))["format"]["duration"]) < 1
        await media.trim(source, clips[1], 5, True)
        result = await media.assemble(clips, tmp_path / "final.mp4", 10)
        assert abs(float(result["format"]["duration"]) - 10) < .05
    asyncio.run(scenario())
