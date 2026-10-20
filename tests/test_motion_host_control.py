import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from shared.host_auth import Verifier, signed_headers, signature
from gateway.motion_director import claude_host, host_control
from gateway.motion_director.planner import DirectorDecisions, LocalDirector, ShotRequest, apply_director_decisions
from test_motion_director import studio, upload, await_job
import motion_director.routes as routes


def keys(tmp_path):
    for role in ("gateway", "supervisor"):
        (tmp_path / f"{role}.key").write_text(role * 8)
    return tmp_path


def test_host_capabilities_are_signed_role_limited_and_not_replayable(tmp_path):
    root = keys(tmp_path)
    verifier = Verifier()
    headers = signed_headers(str(root / "gateway.key"), "gateway", "GET", "/resources", b"")
    assert verifier.check(headers, "GET", "/resources", b"", root)
    assert not verifier.check(headers, "GET", "/resources", b"", root)
    headers = signed_headers(str(root / "gateway.key"), "gateway", "POST", "/gpu/acquire", b"{}")
    assert not verifier.check(headers, "POST", "/gpu/acquire", b"{}", root)
    headers = signed_headers(str(root / "supervisor.key"), "supervisor", "POST", "/claude/plan", b"{}")
    assert not verifier.check(headers, "POST", "/claude/plan", b"{}", root)
    headers = signed_headers(str(root / "supervisor.key"), "supervisor", "POST", "/gpu/acquire", b"{}")
    assert not verifier.check(headers, "POST", "/gpu/acquire", b'{"tampered":true}', root)
    headers["X-NeonForge-Time"] = str(int(time.time()) - 100)
    assert not verifier.check(headers, "POST", "/gpu/acquire", b"{}", root)


def test_host_api_rejects_unsigned_and_wrong_role_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(host_control, "STATE_DIR", keys(tmp_path))
    monkeypatch.setattr(host_control, "verifier", Verifier())
    async def telemetry():
        return {"gpu": {"cap_active": True}, "blockers": []}
    monkeypatch.setattr(host_control, "telemetry", telemetry)
    async def state(route):
        return {"exclusive": False}
    monkeypatch.setattr(host_control.coordinator, "lease", state)
    @asynccontextmanager
    async def lifespan(app):
        yield
    monkeypatch.setattr(host_control.app.router, "lifespan_context", lifespan)
    with TestClient(host_control.app) as client:
        assert client.get("/resources").status_code == 403
        headers = signed_headers(str(tmp_path / "gateway.key"), "gateway", "GET", "/resources", b"")
        assert client.get("/resources", headers=headers).status_code == 200
        body = b'{"claim_id":"x","service":"h3","minimum_gb":90}'
        headers = signed_headers(str(tmp_path / "gateway.key"), "gateway", "POST", "/gpu/acquire", body)
        assert client.post("/gpu/acquire", content=body, headers=headers).status_code == 403


@pytest.mark.parametrize("extra,quota,expected", [(True, 0, False), (False, 100, False), (False, 0, True)])
def test_opus_requires_subscription_quota_and_disabled_paid_extra_usage(tmp_path, monkeypatch, extra, quota, expected):
    monkeypatch.setattr(claude_host, "status_cache", None)
    directory = tmp_path / ".claude"
    directory.mkdir()
    (directory / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "test-token-not-a-credential"}}))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    async def fake_cli(args, **kwargs):
        assert "status" in args and "-p" not in args
        return {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "pro", "configDirectory": str(directory)}
    monkeypatch.setattr(claude_host, "run_cli", fake_cli)
    real_client = httpx.AsyncClient
    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"extra_usage": {"is_enabled": extra}, "five_hour": {"utilization": quota}})))
    monkeypatch.setattr(claude_host.httpx, "AsyncClient", client)
    assert asyncio.run(claude_host.subscription_status())["available"] is expected


def test_api_environment_disables_opus_before_any_execution(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    status = asyncio.run(claude_host.subscription_status())
    assert not status["available"] and "API/provider" in status["reason"]


def test_claude_stream_parser_keeps_only_terminal_structured_output(monkeypatch,tmp_path):
    class Process:
        returncode = 0
        async def communicate(self, data):
            return (b'{"type":"system","private_metadata":"not-returned"}\n'
                    b'{"type":"assistant","message":{"content":[]}}\n'
                    b'{"type":"result","structured_output":{"camera_movement":"slow push"}}\n', b'private stderr not returned')
    async def spawn(*args, **kwargs):
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        return Process()
    monkeypatch.setattr(claude_host.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv('NEONFORGE_MOTION_USAGE_LOG',str(tmp_path/'usage.jsonl'))
    result = asyncio.run(claude_host.run_cli(["-p"]))
    usage=result.pop('_motion_usage')
    assert usage['input_tokens'] is None and usage['provider_requests'] is None
    assert usage['cli_invocations']==1 and usage['application_retries']==0
    assert result == {"type": "result", "structured_output": {"camera_movement": "slow push"}}


def test_director_cannot_change_reference_or_hardware_bounds():
    base = LocalDirector().plan(ShotRequest(prompt="A portrait", reference_ids=["r"], director="opus"))
    decisions = DirectorDecisions(camera_movement="A motivated gentle push", targets=["main subject"], composition="Eye-level portrait",
                                  subject_constraints=["Preserve identity"], timing=[{"start": 0, "end": 2, "action": "A subtle head turn"}])
    shot = apply_director_decisions(base, decisions)
    assert shot.planner == "claude-code-opus"
    assert (shot.width, shot.height, shot.frames, shot.reference_ids) == (base.width, base.height, base.frames, base.reference_ids)
    decisions.timing[0].end = 10
    with pytest.raises(ValueError, match="duration"):
        apply_director_decisions(base, decisions)


def test_opus_plans_once_then_render_reuses_the_persisted_plan(studio, monkeypatch):
    client, fake = studio
    ref = upload(client)
    calls = []
    async def host(runtime, route, payload=None):
        assert route == "claude/plan"
        calls.append(payload)
        shot = LocalDirector().plan(ShotRequest.model_validate(payload["shot"]))
        shot.planner = "claude-code-opus"
        return shot.model_dump()
    monkeypatch.setattr(routes, "host_request", host)
    request = {"prompt": "A portrait", "reference_ids": [ref], "director": "opus"}
    one = client.post("/api/v1/motion-director/plan", json=request).json()
    two = client.post("/api/v1/motion-director/plan", json=request).json()
    assert one["request"]["plan_id"] == two["request"]["plan_id"]
    assert len(calls) == 1
    accepted = client.post("/api/v1/motion-director/jobs", json=one["request"])
    assert accepted.status_code == 202
    assert await_job(client, accepted.json()["job_id"])["status"] == "completed"
    assert len(calls) == 1
    edited = {**one["request"], "prompt": "A different portrait"}
    assert client.post("/api/v1/motion-director/jobs", json=edited).status_code == 409
    generated = client.post("/api/v1/motion-director/jobs", json=request)
    assert generated.status_code == 202
    assert await_job(client, generated.json()["job_id"])["status"] == "completed"
    assert len(calls) == 2  # Generate automatically plans this new generation once.


def test_invalid_opus_output_uses_local_fallback_once(studio, monkeypatch):
    client, _ = studio
    ref = upload(client)
    calls = []
    async def bad_host(runtime, route, payload=None):
        calls.append(route)
        shot = LocalDirector().plan(ShotRequest.model_validate(payload["shot"]))
        shot.width = 9999
        return shot.model_dump()
    monkeypatch.setattr(routes, "host_request", bad_host)
    request = {"prompt": "A portrait", "reference_ids": [ref], "director": "opus"}
    result = client.post("/api/v1/motion-director/plan", json=request).json()
    assert result["planner"] == "local-director" and result["width"] == 384
    assert any("fallback" in note for note in result["notes"])
    client.post("/api/v1/motion-director/plan", json=request)
    assert len(calls) == 1


def test_host_h3_floor_cannot_be_lowered_and_failed_admission_restores_lease(tmp_path, monkeypatch):
    monkeypatch.setattr(host_control, "STATE_DIR", tmp_path)
    monkeypatch.setattr(host_control, "ALLOW_IDLE_PAUSE", True)
    monkeypatch.setattr(host_control, "memory", lambda: {"available_gb": 85})
    async def hardware():
        return {"blockers": [], "gpu": {"utilization_pct": 0}}
    monkeypatch.setattr(host_control, "telemetry", hardware)
    coordinator = host_control.Coordinator()
    calls = []
    async def idle():
        return None
    async def lease(route, payload=None):
        calls.append(route)
        if route == "state":
            return {"exclusive": False, "snapshot": {"ollama": [], "hunyuan": {"busy": False}}, "releasable": {"items": []}}
        if route == "acquire":
            return {"token": "test-lease", "released": {"actions": []}}
        return {"ok": True, "restored": {"errors": []}}
    coordinator.idle_checks = idle
    coordinator.lease = lease
    async def scenario():
        with pytest.raises(RuntimeError, match="90.0"):
            await coordinator.acquire(host_control.ClaimRequest(claim_id="job", service="h3", minimum_gb=0))
        assert not coordinator.claims and coordinator.token is None
        assert calls[-1] == "release"
        await coordinator.close()
    asyncio.run(scenario())


def test_h3_reservation_is_not_released_until_engine_is_idle(tmp_path, monkeypatch):
    monkeypatch.setattr(host_control, "STATE_DIR", tmp_path)
    monkeypatch.setattr(host_control, "memory", lambda: {"available_gb": 95})
    coordinator = host_control.Coordinator()
    coordinator.claims = {"job": {"service": "h3", "at": 1}}
    coordinator.token = "test-lease"
    checks = []
    async def lease(route, payload=None):
        if route == "state":
            return {"snapshot": {}}
        assert len(checks) >= 3
        return {"ok": True, "restored": {"errors": []}}
    coordinator.lease = lease
    async def scenario():
        await coordinator.http.aclose()
        def queue(request):
            checks.append(request.url.path)
            return httpx.Response(200, json={"queue_running": [[1, "active"]] if len(checks) < 3 else [], "queue_pending": []})
        coordinator.http = httpx.AsyncClient(transport=httpx.MockTransport(queue))
        result = await coordinator.release("job")
        assert result["released"] and not coordinator.claims
        await coordinator.close()
    asyncio.run(scenario())
