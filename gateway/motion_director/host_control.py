"""Private host adapter: existing Claude CLI, DGX telemetry and shared GPU lease.

Served only on a user-owned Unix socket; Docker receives the socket, never auth
files, executables, agent profiles, arbitrary command execution or model paths.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import time
import secrets
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import Field, model_validator

from .claude_host import plan_with_opus, subscription_status
from .planner import ShotRequest, StrictModel
from shared.host_auth import Verifier

STATE_DIR = Path(os.getenv("NEONFORGE_HOST_STATE_DIR", str(Path(__file__).resolve().parents[2] / ".motion-runtime")))
ASSETS = Path(os.getenv("NEONFORGE_HOST_UPLOADS_DIR", "/srv/ai/assets/comfyui/uploads"))
LEASE_SOCKET = os.getenv("NEONFORGE_SHARED_GPU_SOCKET", "/run/forge3d-resctl/resctl.sock")
FACTORY_HEALTH = os.getenv("NEONFORGE_FACTORY_HEALTH_URL", "http://100.103.129.82:8788/health")
ALLOW_IDLE_PAUSE = os.getenv("NEONFORGE_ALLOW_IDLE_EXTERNAL_PAUSE", "false").lower() == "true"
SERVICES = Literal["h3", "comfyui", "misotts", "breeze_tts", "fish_speech", "voxcpm2", "f5tts", "lipsync"]


def memory() -> dict:
    fields = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            fields[key] = int(value.strip().split()[0]) / 1048576
    return {"total_gb": round(fields["MemTotal"], 1), "available_gb": round(fields["MemAvailable"], 1),
            "swap_total_gb": round(fields["SwapTotal"], 1), "swap_used_gb": round(fields["SwapTotal"] - fields["SwapFree"], 1)}


async def command(*args, timeout=8):
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError):
        process.kill(); await process.wait(); raise
    if process.returncode:
        raise RuntimeError("Host safety probe unavailable")
    return out.decode()


async def telemetry() -> dict:
    result = {"memory": memory(), "cpu": {"cores": os.cpu_count(), "load": list(os.getloadavg())}, "gpu": {}, "blockers": []}
    try:
        raw = await command("nvidia-smi", "--query-gpu=utilization.gpu,clocks.gr,temperature.gpu,power.draw", "--format=csv,noheader,nounits")
        util, clock, temp, power = [float(v.strip()) for v in raw.strip().splitlines()[0].split(",")]
        cap = (await command("systemctl", "is-active", "gpu-clock-cap.service")).strip() == "active"
        result["gpu"] = {"utilization_pct": util, "clock_mhz": clock, "temperature_c": temp, "power_w": power, "cap_mhz": 2000, "cap_active": cap}
        if not cap or clock > 2000:
            result["blockers"].append("The existing 2000 MHz GPU safety cap is not verified.")
        if temp >= 70:
            result["blockers"].append("GPU is too warm to start another heavy render.")
        if util > 10:
            result["blockers"].append("GPU is currently active; do not interrupt its workload.")
    except (OSError, RuntimeError, TimeoutError, ValueError):
        result["blockers"].append("GPU telemetry/safety cap could not be verified.")
    consumers = []
    for path in Path("/proc").glob("[0-9]*/status"):
        try:
            values = {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in path.read_text().splitlines() if ":" in line}
            rss = int(values.get("VmRSS", "0 kB").split()[0]) / 1048576
            if rss > .25:
                consumers.append({"pid": int(path.parent.name), "name": values.get("Name"), "resident_gb": round(rss, 2)})
        except (OSError, ValueError):
            pass
    result["consumers"] = sorted(consumers, key=lambda item: item["resident_gb"], reverse=True)[:8]
    result["h3_minimum_gb"] = 90
    if result["memory"]["available_gb"] < 90:
        result["blockers"].append(f"H3 needs 90 GiB available; current {result['memory']['available_gb']} GiB.")
    return result


class ClaimRequest(StrictModel):
    claim_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    service: SERVICES
    minimum_gb: float = Field(ge=0, le=121.7)
    paused_services: list[SERVICES] = Field(default_factory=list, max_length=8)


class PlanRequest(StrictModel):
    shot: ShotRequest
    references: list[str] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def references_match(self):
        if len(self.references) != len(self.shot.reference_ids):
            raise ValueError("Reference paths must match selected IDs")
        return self


class Coordinator:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.claims = {}
        self.token = None
        self.last_restore = None
        self.renew_task = None
        self.client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=LEASE_SOCKET), timeout=180)
        self.http = httpx.AsyncClient(timeout=10)

    def save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = STATE_DIR / "gpu-lease.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps({"token": self.token, "claims": self.claims}))
        temp.chmod(0o600); temp.replace(path)

    async def lease(self, route, payload=None):
        response = await self.client.request("GET" if payload is None else "POST", f"http://localhost/{route}", json=payload)
        if response.status_code != 200:
            raise RuntimeError(f"Shared GPU lease refused {route} (HTTP {response.status_code}); existing jobs were preserved.")
        return response.json()

    async def start(self):
        path = STATE_DIR / "gpu-lease.json"
        if path.is_file():
            data = json.loads(path.read_text())
            self.token, self.claims = data.get("token"), data.get("claims", {})
        self.renew_task = asyncio.create_task(self.renew())

    async def renew(self):
        while True:
            await asyncio.sleep(30)
            if self.token and self.claims:
                try:
                    await self.lease("renew", {"token": self.token, "lease_seconds": 900})
                except (httpx.HTTPError, RuntimeError):
                    # Never discard an uncertain lease or admit another workload.
                    self.last_restore = {"error": "Shared lease renewal failed; GPU admission remains blocked."}

    async def idle_checks(self):
        # Fail closed on missing health, unlike an unverified process-name guess.
        response = await self.http.get(FACTORY_HEALTH)
        response.raise_for_status()
        health = response.json()
        if health.get("worker", {}).get("current_job_id") or health.get("queue_depth", 0):
            raise RuntimeError("Content-factory has active or queued work; it will not be interrupted.")
        response = await self.http.get("http://100.103.129.82:8189/system")
        response.raise_for_status()
        if response.json().get("active_job"):
            raise RuntimeError("Trellis has an active or queued job; it will not be interrupted.")
        for url in ("http://127.0.0.1:8189", "http://127.0.0.1:8188"):
            response = await self.http.get(url + "/queue")
            response.raise_for_status()
            queue = response.json()
            if queue.get("queue_running") or queue.get("queue_pending"):
                raise RuntimeError("An existing ComfyUI job is active or queued; wait for it to finish.")

    async def acquire(self, request: ClaimRequest):
        async with self.lock:
            if request.claim_id in self.claims:
                if self.claims[request.claim_id]["service"] != request.service:
                    raise RuntimeError("GPU claim belongs to another service")
                self.claims[request.claim_id]["paused_services"] = request.paused_services
                self.save()
                return {"ready": True, "restored_claim": True}
            if self.claims:
                raise RuntimeError("Another NeonForge generation holds the GPU-exclusive slot.")
            hardware = await telemetry()
            blockers = [b for b in hardware["blockers"] if not b.startswith("H3 needs")]
            if blockers:
                raise RuntimeError(" ".join(blockers))
            await self.idle_checks()
            state = await self.lease("state")
            if state.get("exclusive"):
                raise RuntimeError("Another project already holds the shared GPU lease.")
            snapshot = state.get("snapshot", {})
            if snapshot.get("ollama"):
                raise RuntimeError("Ollama has resident models whose request activity cannot be verified; no automatic unload is permitted.")
            if snapshot.get("hunyuan") is None:
                raise RuntimeError("Existing Hunyuan workload state is unknown; GPU admission is refused.")
            if (snapshot.get("hunyuan") or {}).get("busy"):
                raise RuntimeError("Hunyuan is actively generating.")
            if state.get("releasable", {}).get("items") and not ALLOW_IDLE_PAUSE:
                raise RuntimeError("Cross-project idle coordination requires the owner's approval.")
            acquired = await self.lease("acquire", {"lease_seconds": 900})
            self.token = acquired["token"]
            self.claims[request.claim_id] = {"service": request.service, "at": time.time(), "paused_services": request.paused_services}
            self.save()
            minimum = max(request.minimum_gb, 90 if request.service == "h3" else 0)
            if memory()["available_gb"] < minimum:
                await self._release(request.claim_id)
                raise RuntimeError(f"Need {minimum:.1f} GiB after verified idle reclamation; floor was not lowered.")
            return {"ready": True, "memory": memory(), "exclusive": True,
                    "paused": [item.get("kind") for item in acquired.get("released", {}).get("actions", [])]}

    async def _release(self, claim_id):
        if claim_id not in self.claims:
            return {"released": False}
        if self.token:
            state = await self.lease("state")
            snap = state.get("snapshot", {})
            needed = sum(m.get("size_vram_gb", 0) for m in snap.get("ollama", []) if m.get("pinned"))
            hy = snap.get("hunyuan") or {}
            if hy.get("loaded", {}).get("shape") or hy.get("loaded", {}).get("paint"):
                needed += hy.get("footprint_gb", 0)
            if needed and memory()["available_gb"] < needed + 8:
                await self.free_idle_h3()
            report = await self.lease("release", {"token": self.token})
            self.last_restore = {"ok": report.get("ok"), "restored": report.get("restored", report.get("restore"))}
        self.claims.pop(claim_id, None)
        self.token = None; self.save()
        return {"released": True, "restore": self.last_restore}

    async def free_idle_h3(self):
        response = await self.http.get("http://127.0.0.1:8189/queue")
        response.raise_for_status()
        queue = response.json()
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise RuntimeError("H3 is busy; idle cache cannot be released")
        response = await self.http.post("http://127.0.0.1:8189/free", json={"unload_models": True, "free_memory": True})
        response.raise_for_status()
        await asyncio.sleep(2)

    async def release(self, claim_id):
        async with self.lock:
            if self.claims.get(claim_id, {}).get("service") == "h3":
                for _ in range(20):
                    response = await self.http.get("http://127.0.0.1:8189/queue")
                    response.raise_for_status()
                    queue = response.json()
                    if not (queue.get("queue_running") or queue.get("queue_pending")):
                        break
                    await asyncio.sleep(.5)
                else:
                    raise RuntimeError("H3 still has active work; reservation and restoration remain pending.")
            return await self._release(claim_id)

    async def close(self):
        if self.renew_task:
            self.renew_task.cancel(); await asyncio.gather(self.renew_task, return_exceptions=True)
        # Active leases persist across host-adapter restarts; no early restoration.
        await self.client.aclose(); await self.http.aclose()


coordinator = Coordinator()
planning_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.chmod(0o700)
    for role in ("gateway", "supervisor"):
        key = STATE_DIR / f"{role}.key"
        if not key.exists():
            with key.open("x") as stream:
                stream.write(secrets.token_hex(32))
            key.chmod(0o600)
    await coordinator.start()
    yield
    await coordinator.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
verifier = Verifier()


@app.middleware("http")
async def bounded_request(request: Request, call_next):
    from fastapi.responses import JSONResponse
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        return JSONResponse({"detail": "Invalid request length"}, status_code=400)
    if length > 1024**2:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    body = await request.body()
    if len(body) > 1024**2:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    if not verifier.check(request.headers, request.method, request.url.path, body, STATE_DIR):
        return JSONResponse({"detail": "Host capability authentication or scope refused"}, status_code=403)
    return await call_next(request)


@app.get("/resources")
async def resources():
    result = await telemetry()
    result.update(exclusive_claims=coordinator.claims, last_restore=coordinator.last_restore, idle_coordination_allowed=ALLOW_IDLE_PAUSE)
    try:
        shared = await coordinator.lease("state")
        result["shared_gpu"] = {"exclusive": shared.get("exclusive"), "lease": shared.get("lease"), "releasable": shared.get("releasable"), "last_restore": shared.get("last_restore")}
        if shared.get("releasable", {}).get("items") and not ALLOW_IDLE_PAUSE:
            result["blockers"].append("Permission is required before pausing idle services from other projects.")
    except (httpx.HTTPError, RuntimeError):
        result["blockers"].append("Existing shared GPU lease helper is unavailable.")
    return result


@app.post("/gpu/acquire")
async def acquire(request: ClaimRequest):
    try:
        return await coordinator.acquire(request)
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(503, str(exc) if isinstance(exc, RuntimeError) else "Existing workload health is unreachable; GPU admission refused") from exc


@app.post("/gpu/release")
async def release(request: ClaimRequest):
    if request.claim_id in coordinator.claims and coordinator.claims[request.claim_id]["service"] != request.service:
        raise HTTPException(409, "GPU reservation belongs to another service")
    return await coordinator.release(request.claim_id)


@app.post("/h3/free-idle")
async def free_idle(request: ClaimRequest):
    if request.claim_id not in coordinator.claims:
        raise HTTPException(409, "Only the owner of the GPU reservation can request idle cache release")
    try:
        await coordinator.free_idle_h3()
        return {"freed": True}
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(409, "H3 cache is busy or unreachable; it was not forcibly released") from exc


@app.get("/claude/status")
async def claude_status():
    return await subscription_status()


@app.post("/claude/plan")
async def claude_plan(request: PlanRequest):
    async with planning_lock:
        try:
            return await plan_with_opus(request.shot, request.references, ASSETS)
        except (RuntimeError, TimeoutError, ValueError, OSError) as exc:
            raise HTTPException(503, "Opus planning unavailable or invalid; use the deterministic fallback") from exc
