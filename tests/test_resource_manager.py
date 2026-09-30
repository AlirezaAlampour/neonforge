import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from supervisor.resource_manager import ResourceManager, ServicePolicy, select_reclamation_candidates


def _policy(name: str, minimum: float) -> ServicePolicy:
    return ServicePolicy(
        service=name,
        container=f"ai-{name}",
        profile=None,
        url=f"http://{name}:8000",
        ready_path="/healthz",
        workload_class="test",
        min_available_gb=minimum,
        idle_timeout_sec=300,
    )


def test_reclamation_uses_only_allowlisted_idle_services():
    policies = {
        "fish_speech": _policy("fish_speech", 24),
        "comfyui": _policy("comfyui", 40),
        "lipsync": _policy("lipsync", 24),
    }

    result = select_reclamation_candidates(
        target="comfyui",
        running={"fish_speech", "comfyui", "lipsync", "unrelated-container"},
        claimed={"lipsync"},
        policies=policies,
        memory_gb={"fish_speech": 17.4, "lipsync": 2.0, "unrelated-container": 99.0},
    )

    assert result == ["fish_speech"]


def test_reclamation_prefers_largest_memory_consumer():
    policies = {
        "fish_speech": _policy("fish_speech", 24),
        "misotts": _policy("misotts", 24),
        "comfyui": _policy("comfyui", 40),
    }

    result = select_reclamation_candidates(
        target="comfyui",
        running=set(policies),
        claimed={"comfyui"},
        policies=policies,
        memory_gb={"fish_speech": 17.4, "misotts": 2.2},
    )

    assert result == ["fish_speech", "misotts"]


def test_protected_service_cannot_be_managed():
    policies = {"gateway": _policy("gateway", 1)}

    try:
        ResourceManager(
            compose_dir="/tmp",
            policies=policies,
            protected_services={"gateway"},
        )
    except ValueError as exc:
        assert "Protected services cannot be managed" in str(exc)
    else:
        raise AssertionError("ResourceManager accepted a protected service policy")


class _FakeResourceManager(ResourceManager):
    def __init__(self, *args, running=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fake_running = set(running or ())
        self.stopped = []
        self.started = []

    async def running_services(self):
        return set(self.fake_running)

    async def container_state(self, policy):
        return "running" if policy.service in self.fake_running else "not_found"

    async def memory_consumers(self):
        return {name: 20.0 for name in self.fake_running}

    async def _stop(self, service, *, reason):
        self.stopped.append((service, reason))
        self.fake_running.discard(service)
        self.memory_reader.available = 52.0

    async def _start(self, policy):
        self.started.append(policy.service)
        self.fake_running.add(policy.service)

    async def _wait_ready(self, policy):
        return None


class _MemoryReader:
    def __init__(self, available):
        self.available = available

    def __call__(self):
        return {
            "total_gb": 121.7,
            "available_gb": self.available,
            "used_gb": 121.7 - self.available,
            "used_pct": 0,
            "swap_total_gb": 16,
            "swap_free_gb": 16,
            "swap_used_gb": 0,
        }


def test_prepare_reclaims_conflict_and_retries_admission():
    memory = _MemoryReader(18.0)
    policies = {
        "fish_speech": _policy("fish_speech", 24),
        "comfyui": _policy("comfyui", 48),
    }
    manager = _FakeResourceManager(
        compose_dir="/tmp",
        policies=policies,
        protected_services={"gateway"},
        memory_reader=memory,
        memory_wait_sec=0,
        running={"fish_speech"},
    )

    async def scenario():
        result = await manager.prepare("comfyui", job_id="job-1")
        assert result["stopped_services"] == ["fish_speech"]
        assert result["memory_before"]["available_gb"] == 18.0
        assert result["memory_after"]["available_gb"] == 52.0
        assert manager.started == ["comfyui"]
        status = await manager.status()
        assert status["claims"][0]["job_id"] == "job-1"
        await manager.release("comfyui", result["claim_id"])
        assert any(event["event"] == "workload_metrics" for event in manager.events)
        await manager.close()

    asyncio.run(scenario())


def test_prepare_accepts_allowlisted_workflow_minimum():
    memory = _MemoryReader(42.0)
    manager = _FakeResourceManager(
        compose_dir="/tmp",
        policies={"comfyui": _policy("comfyui", 48)},
        protected_services={"gateway"},
        memory_reader=memory,
        memory_wait_sec=0,
    )

    async def scenario():
        result = await manager.prepare(
            "comfyui",
            job_id="hunyuan-job",
            model_label="HunyuanVideo 1.5",
            minimum_available_gb=40,
        )
        assert result["minimum_available_gb"] == 40
        assert manager.started == ["comfyui"]
        await manager.release("comfyui", result["claim_id"])
        await manager.close()

    asyncio.run(scenario())


def test_prepare_failure_clears_claim_after_reclamation_exhausted():
    memory = _MemoryReader(10.0)
    manager = _FakeResourceManager(
        compose_dir="/tmp",
        policies={"comfyui": _policy("comfyui", 48)},
        protected_services={"gateway"},
        memory_reader=memory,
        memory_wait_sec=0,
    )

    async def scenario():
        try:
            await manager.prepare("comfyui", job_id="job-fail")
        except RuntimeError as exc:
            assert "after allowlisted reclamation" in str(exc)
        else:
            raise AssertionError("prepare unexpectedly admitted an under-memory workload")
        assert manager.claims == {}
        assert manager.metrics["prepare_failures"] == 1
        await manager.close()

    asyncio.run(scenario())


def test_prepare_readiness_timeout_clears_claim():
    memory = _MemoryReader(64.0)
    manager = _FakeResourceManager(
        compose_dir="/tmp",
        policies={"lipsync": _policy("lipsync", 32)},
        protected_services={"gateway"},
        memory_reader=memory,
        memory_wait_sec=0,
    )

    async def fail_readiness(_policy):
        raise TimeoutError("lipsync did not become ready")

    manager._wait_ready = fail_readiness

    async def scenario():
        try:
            await manager.prepare("lipsync", job_id="timeout-job")
        except TimeoutError as exc:
            assert "did not become ready" in str(exc)
        else:
            raise AssertionError("prepare unexpectedly ignored a readiness timeout")
        assert manager.claims == {}
        assert manager.metrics["prepare_failures"] == 1
        assert any(event["event"] == "prepare_failed" for event in manager.events)
        await manager.close()

    asyncio.run(scenario())


def test_primary_navigation_contains_only_creator_goals():
    sidebar = (ROOT / "frontend" / "components" / "sidebar.tsx").read_text(encoding="utf-8")
    expected = [
        ("/voiceover", "Voiceover"),
        ("/video", "Video Generation"),
        ("/character", "Character"),
        ("/avatar", "Avatar"),
        ("/lipsync", "Lip Sync"),
        ("/status", "Utilities & Status"),
    ]
    assert [(href, label) for href, label in expected if f"href: '{href}', label: '{label}'" in sidebar] == expected
    for hidden_route in ("/studio", "/broll", "/voice"):
        assert f"href: '{hidden_route}'" not in sidebar
