"""Allowlist-only lifecycle management for shared-UMA model services."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

log = logging.getLogger("supervisor.resources")


@dataclass(frozen=True)
class ServicePolicy:
    service: str
    container: str
    profile: str | None
    url: str
    ready_path: str
    workload_class: str
    min_available_gb: float
    idle_timeout_sec: int


@dataclass
class Claim:
    claim_id: str
    service: str
    job_id: str | None
    created_at: float
    model_label: str
    prepared_at: float | None
    memory_before_gb: float
    min_available_gb_observed: float


def read_uma_memory(path: str = "/proc/meminfo") -> dict[str, float]:
    """Read canonical shared UMA availability without relying on nvidia-smi."""
    values: dict[str, int] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        key, _, raw = line.partition(":")
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            values[key] = int(raw.strip().split()[0])
    total_kb = values.get("MemTotal", 0)
    available_kb = values.get("MemAvailable", 0)
    used_kb = max(0, total_kb - available_kb)
    swap_total_kb = values.get("SwapTotal", 0)
    swap_free_kb = values.get("SwapFree", 0)
    return {
        "total_gb": round(total_kb / 1048576, 1),
        "available_gb": round(available_kb / 1048576, 1),
        "used_gb": round(used_kb / 1048576, 1),
        "used_pct": round((used_kb / total_kb * 100) if total_kb else 0, 1),
        "swap_total_gb": round(swap_total_kb / 1048576, 1),
        "swap_free_gb": round(swap_free_kb / 1048576, 1),
        "swap_used_gb": round(max(0, swap_total_kb - swap_free_kb) / 1048576, 1),
    }


def select_reclamation_candidates(
    *,
    target: str,
    running: set[str],
    claimed: set[str],
    policies: dict[str, ServicePolicy],
    memory_gb: dict[str, float],
) -> list[str]:
    """Return only idle, allowlisted services, largest consumers first."""
    candidates = running.intersection(policies).difference({target}).difference(claimed)
    return sorted(
        candidates,
        key=lambda name: (
            memory_gb.get(name, 0.0),
            policies[name].min_available_gb,
        ),
        reverse=True,
    )


class ResourceManager:
    def __init__(
        self,
        *,
        compose_dir: str,
        policies: dict[str, ServicePolicy],
        protected_services: set[str],
        ready_timeout_sec: int = 300,
        memory_wait_sec: int = 90,
        idle_scan_sec: int = 15,
        memory_reader: Callable[[], dict[str, float]] = read_uma_memory,
    ) -> None:
        overlap = set(policies).intersection(protected_services)
        if overlap:
            raise ValueError(f"Protected services cannot be managed: {sorted(overlap)}")
        self.compose_dir = compose_dir
        self.policies = policies
        self.protected_services = frozenset(protected_services)
        self.ready_timeout_sec = ready_timeout_sec
        self.memory_wait_sec = memory_wait_sec
        self.idle_scan_sec = idle_scan_sec
        self.memory_reader = memory_reader
        self.claims: dict[str, Claim] = {}
        self.last_used: dict[str, float] = {}
        self.touched: set[str] = set()
        self.metrics: dict[str, int] = {
            "prepare_requests": 0,
            "prepare_failures": 0,
            "containers_started": 0,
            "containers_stopped_for_memory": 0,
            "containers_stopped_for_idle": 0,
        }
        self.events: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
        self._idle_task: asyncio.Task | None = None

    def validate(self, service: str) -> ServicePolicy:
        policy = self.policies.get(service)
        if policy is None:
            raise KeyError(service)
        return policy

    def _event(self, event: str, service: str, **details: Any) -> None:
        self.events.append({"at": time.time(), "event": event, "service": service, **details})
        del self.events[:-50]

    def _sample_claim_memory(self) -> dict[str, float]:
        memory = self.memory_reader()
        available = memory["available_gb"]
        for claim in self.claims.values():
            claim.min_available_gb_observed = min(claim.min_available_gb_observed, available)
        return memory

    async def _command(self, *args: str, timeout: int = 180) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=self.compose_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return 124, "", f"Command timed out after {timeout}s"
        return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")

    async def container_state(self, policy: ServicePolicy) -> str:
        code, stdout, _ = await self._command(
            "docker", "inspect", "-f", "{{.State.Status}}", policy.container, timeout=15
        )
        return stdout.strip() if code == 0 else "not_found"

    async def running_services(self) -> set[str]:
        # Avoid shell interpolation and keep container lookup bounded to the allowlist.
        states = await asyncio.gather(*(self.container_state(policy) for policy in self.policies.values()))
        return {
            name
            for (name, _), state in zip(self.policies.items(), states)
            if state == "running"
        }

    @staticmethod
    def _parse_size_gb(value: str) -> float:
        raw = value.strip().replace("iB", "B")
        units = {"B": 1 / 1e9, "kB": 1 / 1e6, "MB": 1 / 1e3, "GB": 1.0, "TB": 1e3}
        for unit in ("TB", "GB", "MB", "kB", "B"):
            if raw.endswith(unit):
                try:
                    return round(float(raw[: -len(unit)].strip()) * units[unit], 2)
                except ValueError:
                    return 0.0
        return 0.0

    async def memory_consumers(self) -> dict[str, float]:
        containers = [policy.container for policy in self.policies.values()]
        code, stdout, _ = await self._command(
            "docker", "stats", "--no-stream", "--format", "{{json .}}", *containers, timeout=30
        )
        if code != 0:
            return {}
        by_container = {policy.container: name for name, policy in self.policies.items()}
        result: dict[str, float] = {}
        for line in stdout.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            service = by_container.get(str(item.get("Name") or item.get("Container") or ""))
            usage = str(item.get("MemUsage") or "").split("/", 1)[0]
            if service:
                result[service] = self._parse_size_gb(usage)
        return result

    async def _stop(self, service: str, *, reason: str) -> None:
        # The policy lookup is the security boundary: no unlisted name reaches Docker.
        self.validate(service)
        code, _, stderr = await self._command("docker", "compose", "stop", service, timeout=180)
        if code != 0:
            raise RuntimeError(f"Unable to stop {service}: {stderr.strip()[:300]}")
        metric = "containers_stopped_for_idle" if reason == "idle" else "containers_stopped_for_memory"
        self.metrics[metric] += 1
        self._event("stopped", service, reason=reason)

    async def _start(self, policy: ServicePolicy) -> None:
        args = ["docker", "compose"]
        if policy.profile:
            args.extend(["--profile", policy.profile])
        args.extend(["up", "-d", "--no-deps", policy.service])
        code, _, stderr = await self._command(*args, timeout=300)
        if code != 0:
            raise RuntimeError(f"Unable to start {policy.service}: {stderr.strip()[:300]}")
        self.metrics["containers_started"] += 1
        self._event("started", policy.service)

    async def _wait_for_memory(self, minimum_gb: float) -> dict[str, float]:
        deadline = time.monotonic() + self.memory_wait_sec
        memory = self.memory_reader()
        while memory["available_gb"] < minimum_gb and time.monotonic() < deadline:
            await asyncio.sleep(1)
            memory = self.memory_reader()
        return memory

    async def _wait_ready(self, policy: ServicePolicy) -> None:
        deadline = time.monotonic() + self.ready_timeout_sec
        url = f"{policy.url.rstrip('/')}/{policy.ready_path.lstrip('/')}" if policy.ready_path else policy.url
        last_error = "not reachable"
        while time.monotonic() < deadline:
            try:
                response = await self._http.get(url, timeout=8.0)
                if response.status_code == 200:
                    return
                last_error = f"HTTP {response.status_code}"
            except Exception as exc:
                last_error = str(exc)
            await asyncio.sleep(2)
        raise TimeoutError(f"{policy.service} did not become ready in {self.ready_timeout_sec}s ({last_error})")

    async def prepare(
        self,
        service: str,
        *,
        job_id: str | None = None,
        claim_id: str | None = None,
        model_label: str | None = None,
        minimum_available_gb: float | None = None,
    ) -> dict[str, Any]:
        policy = self.validate(service)
        minimum = policy.min_available_gb if minimum_available_gb is None else minimum_available_gb
        token = claim_id or str(uuid.uuid4())
        async with self._lock:
            self.metrics["prepare_requests"] += 1
            before = self.memory_reader()
            stopped: list[str] = []
            self.claims[token] = Claim(
                claim_id=token,
                service=service,
                job_id=job_id,
                created_at=time.time(),
                model_label=model_label or service,
                prepared_at=None,
                memory_before_gb=before["available_gb"],
                min_available_gb_observed=before["available_gb"],
            )
            try:
                running = await self.running_services()
                state = await self.container_state(policy)
                required = 0.0 if state == "running" else minimum
                if required and before["available_gb"] < required:
                    consumers = await self.memory_consumers()
                    claimed_services = {claim.service for claim in self.claims.values()}
                    candidates = select_reclamation_candidates(
                        target=service,
                        running=running,
                        claimed=claimed_services,
                        policies=self.policies,
                        memory_gb=consumers,
                    )
                    for candidate in candidates:
                        await self._stop(candidate, reason=f"prepare:{service}")
                        stopped.append(candidate)
                        memory = await self._wait_for_memory(required)
                        if memory["available_gb"] >= required:
                            break

                admitted = await self._wait_for_memory(required) if required else self.memory_reader()
                if required and admitted["available_gb"] < required:
                    active = sorted({claim.service for claim in self.claims.values() if claim.claim_id != token})
                    raise RuntimeError(
                        f"Need {required:.1f} GB available for {service}, but only "
                        f"{admitted['available_gb']:.1f} GB is available after allowlisted reclamation. "
                        f"Active protected workloads: {active or 'none'}; unrelated containers were not touched."
                    )

                if state != "running":
                    await self._start(policy)
                await self._wait_ready(policy)
                self.touched.add(service)
                self.last_used[service] = time.time()
                after = self.memory_reader()
                claim = self.claims[token]
                claim.prepared_at = time.time()
                claim.min_available_gb_observed = min(
                    claim.min_available_gb_observed,
                    after["available_gb"],
                )
                self._event("prepared", service, claim_id=token, stopped=stopped)
                return {
                    "service": service,
                    "claim_id": token,
                    "state": "ready",
                    "workload_class": policy.workload_class,
                    "minimum_available_gb": minimum,
                    "memory_before": before,
                    "memory_after": after,
                    "stopped_services": stopped,
                }
            except Exception:
                self.metrics["prepare_failures"] += 1
                self.claims.pop(token, None)
                self._event("prepare_failed", service, claim_id=token)
                raise

    async def release(self, service: str, claim_id: str) -> dict[str, Any]:
        self.validate(service)
        async with self._lock:
            claim = self.claims.get(claim_id)
            if claim and claim.service != service:
                raise ValueError(f"Claim {claim_id} belongs to {claim.service}, not {service}")
            memory = self._sample_claim_memory()
            if claim:
                self._event(
                    "workload_metrics",
                    service,
                    claim_id=claim_id,
                    job_id=claim.job_id,
                    model_label=claim.model_label,
                    duration_sec=round(time.time() - (claim.prepared_at or claim.created_at), 1),
                    memory_before_gb=claim.memory_before_gb,
                    min_available_gb=claim.min_available_gb_observed,
                    memory_on_release_gb=memory["available_gb"],
                )
            released = self.claims.pop(claim_id, None) is not None
            self.last_used[service] = time.time()
            self._event("released", service, claim_id=claim_id, found=released)
            return {"service": service, "claim_id": claim_id, "released": released}

    async def idle_sweep(self) -> list[str]:
        stopped: list[str] = []
        async with self._lock:
            now = time.time()
            claimed = {claim.service for claim in self.claims.values()}
            for service in sorted(self.touched):
                policy = self.policies[service]
                if service in claimed or now - self.last_used.get(service, now) < policy.idle_timeout_sec:
                    continue
                if await self.container_state(policy) == "running":
                    memory_before = self.memory_reader()
                    await self._stop(service, reason="idle")
                    memory_after = self.memory_reader()
                    self._event(
                        "unload_metrics",
                        service,
                        memory_before_gb=memory_before["available_gb"],
                        memory_after_gb=memory_after["available_gb"],
                        recovered_gb=round(
                            memory_after["available_gb"] - memory_before["available_gb"],
                            1,
                        ),
                    )
                    stopped.append(service)
                self.touched.discard(service)
            return stopped

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(self.idle_scan_sec)
            try:
                self._sample_claim_memory()
                await self.idle_sweep()
            except Exception:
                log.exception("Idle lifecycle sweep failed")

    def start_idle_loop(self) -> None:
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_loop())

    async def close(self) -> None:
        if self._idle_task:
            self._idle_task.cancel()
            try:
                await self._idle_task
            except asyncio.CancelledError:
                pass
        await self._http.aclose()

    async def status(self) -> dict[str, Any]:
        self._sample_claim_memory()
        running = await self.running_services()
        consumers = await self.memory_consumers()
        now = time.time()
        return {
            "memory": self.memory_reader(),
            "protected_services": sorted(self.protected_services),
            "managed_services": {
                name: {
                    **asdict(policy),
                    "running": name in running,
                    "claimed": any(claim.service == name for claim in self.claims.values()),
                    "memory_gb": consumers.get(name),
                    "idle_for_sec": round(now - self.last_used[name], 1) if name in self.last_used else None,
                }
                for name, policy in self.policies.items()
            },
            "claims": [asdict(claim) for claim in self.claims.values()],
            "metrics": dict(self.metrics),
            "events": list(self.events),
        }
