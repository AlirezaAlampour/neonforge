"""
Supervisor sidecar — internal-only container lifecycle manager.

This service holds the Docker socket and is NOT exposed to external
networks. The public-facing gateway calls it over the internal ai-net
to request lazy-start / stop operations.

Endpoints:
  POST /start/{service}   — start a compose service, wait for readyz
  POST /stop/{service}    — stop a compose service
  GET  /status/{service}  — check if container is running
  GET  /healthz           — self health check
"""

import asyncio
import logging
import os
import subprocess

import httpx
from fastapi import FastAPI, HTTPException

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
COMPOSE_DIR = os.getenv("COMPOSE_DIR", "/project")
READYZ_TIMEOUT = int(os.getenv("READYZ_TIMEOUT", "300"))

# Map service names to their internal Docker network URLs
SERVICE_URLS = {
    "wan21": os.getenv("WAN21_URL", "http://wan21:8000"),
    "f5tts": os.getenv("F5TTS_URL", "http://f5tts:8000"),
    "liveportrait": os.getenv("LIVEPORTRAIT_URL", "http://liveportrait:8000"),
    "lipsync": os.getenv("LIPSYNC_URL", "http://lipsync:8000"),
    "whisper": os.getenv("WHISPER_URL", "http://whisper:8000"),
}

# Only these services can be started/stopped via supervisor
MANAGED_SERVICES = {"wan21", "f5tts", "liveportrait", "lipsync"}

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s [supervisor] %(message)s")
log = logging.getLogger("supervisor")

app = FastAPI(title="DGX AI Supervisor", version="1.0.0")
_http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))


def _validate_service(service: str):
    if service not in MANAGED_SERVICES:
        raise HTTPException(
            403,
            f"Service '{service}' is not managed by supervisor. "
            f"Allowed: {sorted(MANAGED_SERVICES)}",
        )


@app.get("/healthz")
async def healthz():
    return {"status": "alive", "managed": sorted(MANAGED_SERVICES)}


@app.get("/status/{service}")
async def status(service: str):
    _validate_service(service)
    container = f"ai-{service}"
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", container],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {"service": service, "container": container, "status": "not_found"}
        state = result.stdout.strip()
        return {"service": service, "container": container, "status": state}
    except Exception as e:
        raise HTTPException(500, f"Docker inspect failed: {e}")


@app.post("/start/{service}")
async def start(service: str, wait_ready: bool = True):
    """Start a compose service. Optionally wait for /readyz."""
    _validate_service(service)

    # Check if already running and ready
    url = SERVICE_URLS.get(service)
    if url:
        try:
            resp = await _http.get(f"{url}/healthz", timeout=3.0)
            if resp.status_code == 200:
                log.info("Service %s is already running", service)
                return {"service": service, "action": "already_running"}
        except (httpx.ConnectError, httpx.TimeoutException):
            pass

    log.info("Starting service: %s", service)
    proc = await asyncio.create_subprocess_exec(
        "docker", "compose", "--profile", service, "--profile", "full",
        "up", "-d", service,
        cwd=COMPOSE_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.error("Failed to start %s: %s", service, stderr.decode()[:500])
        raise HTTPException(502, f"docker compose up failed: {stderr.decode()[:300]}")

    if not wait_ready or not url:
        return {"service": service, "action": "started", "ready": False}

    # Wait for readyz with backoff
    for attempt in range(60):
        await asyncio.sleep(min(2 * (attempt + 1), 10))
        try:
            resp = await _http.get(f"{url}/readyz", timeout=5.0)
            if resp.status_code == 200:
                log.info("Service %s ready (attempt %d)", service, attempt + 1)
                return {"service": service, "action": "started", "ready": True}
        except (httpx.ConnectError, httpx.TimeoutException):
            continue

        # Check for /healthz (model may be idle but container is alive)
        try:
            resp = await _http.get(f"{url}/healthz", timeout=3.0)
            if resp.status_code == 200:
                log.info("Service %s alive but model idle (attempt %d)", service, attempt + 1)
                return {"service": service, "action": "started", "ready": False, "alive": True}
        except (httpx.ConnectError, httpx.TimeoutException):
            continue

    log.error("Service %s did not become ready within timeout", service)
    raise HTTPException(504, f"Service {service} did not become ready in {READYZ_TIMEOUT}s")


@app.post("/stop/{service}")
async def stop(service: str):
    _validate_service(service)
    log.info("Stopping service: %s", service)
    proc = await asyncio.create_subprocess_exec(
        "docker", "compose", "stop", service,
        cwd=COMPOSE_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.error("Failed to stop %s: %s", service, stderr.decode()[:500])
        raise HTTPException(502, f"docker compose stop failed: {stderr.decode()[:300]}")
    return {"service": service, "action": "stopped"}
