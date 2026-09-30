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
import json
import logging
import os
import re
import subprocess
import uuid

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from resource_manager import ResourceManager, ServicePolicy, workload_minimum_gb

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
COMPOSE_DIR = os.getenv("COMPOSE_DIR", "/project")
READYZ_TIMEOUT = int(os.getenv("READYZ_TIMEOUT", "300"))
COMFYUI_CONTAINER_NAME = os.getenv("COMFYUI_CONTAINER_NAME", "ai-comfyui")

# Explicit policies are the lifecycle security boundary. No caller-supplied
# container name is ever passed through to Docker.
POLICIES = {
    "f5tts": ServicePolicy("f5tts", "ai-f5tts", "voice", os.getenv("F5TTS_URL", "http://f5tts:8000"), "/healthz", "voice", 12, int(os.getenv("F5TTS_IDLE_TIMEOUT", "900"))),
    "fish_speech": ServicePolicy("fish_speech", "ai-fish-speech", "voice", os.getenv("FISH_SPEECH_URL", "http://fish_speech:8000"), "/v1/health", "voice", 24, int(os.getenv("FISH_SPEECH_IDLE_TIMEOUT", "900"))),
    "voxcpm2": ServicePolicy("voxcpm2", "ai-voxcpm2", "voice", os.getenv("VOXCPM2_URL", "http://voxcpm2:8000"), "/v1/health", "voice", 16, int(os.getenv("VOXCPM2_IDLE_TIMEOUT", "900"))),
    "misotts": ServicePolicy("misotts", "ai-misotts", "voice", os.getenv("MISOTTS_URL", "http://misotts:8000"), "/healthz", "voice", 40, int(os.getenv("MISOTTS_IDLE_TIMEOUT", "900"))),
    "breeze_tts": ServicePolicy("breeze_tts", "ai-breeze-tts", "voice", os.getenv("BREEZE_TTS_URL", "http://breeze_tts:8000"), "/healthz", "voice", 24, int(os.getenv("BREEZE_TTS_IDLE_TIMEOUT", "900"))),
    "comfyui": ServicePolicy("comfyui", COMFYUI_CONTAINER_NAME, "video", os.getenv("COMFYUI_URL", "http://comfyui:8188"), "/", "video / character", 48, int(os.getenv("COMFYUI_IDLE_TIMEOUT", "300"))),
    "lipsync": ServicePolicy("lipsync", "ai-lipsync", "lip-sync", os.getenv("LIPSYNC_URL", "http://lipsync:8000"), "/readyz", "lip-sync", 32, int(os.getenv("LIPSYNC_IDLE_TIMEOUT", "300"))),
}
PROTECTED_SERVICES = {"gateway", "frontend", "redis", "supervisor", "whisper"}
MANAGED_SERVICES = set(POLICIES)
SCAN_TARGETS = {
    "comfyui": COMFYUI_CONTAINER_NAME,
    COMFYUI_CONTAINER_NAME: COMFYUI_CONTAINER_NAME,
}

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s [supervisor] %(message)s")
log = logging.getLogger("supervisor")

app = FastAPI(title="DGX AI Supervisor", version="1.0.0")
_http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
resource_manager = ResourceManager(
    compose_dir=COMPOSE_DIR,
    policies=POLICIES,
    protected_services=PROTECTED_SERVICES,
    ready_timeout_sec=READYZ_TIMEOUT,
    memory_wait_sec=int(os.getenv("MEMORY_RECLAIM_TIMEOUT", "90")),
    idle_scan_sec=int(os.getenv("IDLE_SCAN_INTERVAL", "15")),
)


class PrepareRequest(BaseModel):
    job_id: str | None = None
    claim_id: str | None = None
    model_label: str | None = None
    workload_id: str | None = None
    frames: int | None = None
    width: int | None = None
    height: int | None = None


class ReleaseRequest(BaseModel):
    claim_id: str


@app.on_event("startup")
async def start_resource_manager():
    resource_manager.start_idle_loop()


@app.on_event("shutdown")
async def stop_resource_manager():
    await resource_manager.close()
    await _http.aclose()


def _validate_service(service: str):
    if service not in MANAGED_SERVICES:
        raise HTTPException(
            403,
            f"Service '{service}' is not managed by supervisor. "
            f"Allowed: {sorted(MANAGED_SERVICES)}",
        )


def _resolve_scan_target(target: str) -> dict[str, str]:
    normalized = target.strip()
    if not normalized:
        raise HTTPException(
            400,
            detail={
                "target": target,
                "resolved_container": None,
                "reason": "empty_target",
                "message": "Scan target cannot be empty.",
            },
        )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", normalized):
        raise HTTPException(
            400,
            detail={
                "target": target,
                "resolved_container": None,
                "reason": "invalid_target",
                "message": "Scan target contains unsupported characters.",
            },
        )

    resolved_container = SCAN_TARGETS.get(normalized)
    if not resolved_container:
        resolved_container = normalized if normalized.startswith("ai-") else f"ai-{normalized}"

    return {
        "target": target,
        "normalized_target": normalized,
        "resolved_container": resolved_container,
    }


CONTAINER_FILE_SCAN_SCRIPT = r"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
extensions = {value.lower() for value in sys.argv[2:] if value}

payload = {
    "path": str(root),
    "resolved_path": str(root.resolve(strict=False)),
    "exists": root.exists(),
    "is_dir": root.is_dir(),
    "item_count": 0,
    "error": None,
    "items": [],
}

if not payload["exists"]:
    print(json.dumps(payload))
    raise SystemExit(0)

if not payload["is_dir"]:
    payload["error"] = "Path exists but is not a directory."
    print(json.dumps(payload))
    raise SystemExit(0)

for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    if extensions and path.suffix.lower() not in extensions:
        continue
    stat = path.stat()
    payload["items"].append(
        {
            "filename": path.name,
            "path": str(path),
            "relative_path": str(path.relative_to(root)),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        }
    )

payload["item_count"] = len(payload["items"])
print(json.dumps(payload))
"""


@app.get("/healthz")
async def healthz():
    return {
        "status": "alive",
        "managed": sorted(MANAGED_SERVICES),
        "protected": sorted(PROTECTED_SERVICES),
    }


@app.get("/status/{service}")
async def status(service: str):
    _validate_service(service)
    container = POLICIES[service].container
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


@app.get("/container-files/{target}")
async def container_files(target: str, root: str):
    resolved = _resolve_scan_target(target)
    container = resolved["resolved_container"]

    inspect = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}}", container],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if inspect.returncode != 0:
        raise HTTPException(
            404,
            detail={
                **resolved,
                "reason": "container_not_found",
                "message": f"Container lookup failed for {container}.",
                "docker_stdout": inspect.stdout.strip() or None,
                "docker_stderr": inspect.stderr.strip() or None,
            },
        )

    state = inspect.stdout.strip()
    if state != "running":
        raise HTTPException(
            503,
            detail={
                **resolved,
                "reason": "container_not_running",
                "message": f"Container {container} is not running.",
                "state": state,
            },
        )

    proc = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        container,
        "python",
        "-c",
        CONTAINER_FILE_SCAN_SCRIPT,
        root,
        ".pt",
        ".pth",
        ".bin",
        ".ckpt",
        ".safetensors",
        ".onnx",
        ".gguf",
        ".pickle",
        ".pkl",
        ".json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(
            502,
            detail={
                **resolved,
                "reason": "docker_exec_failed",
                "message": "docker exec scan failed.",
                "docker_stderr": stderr.decode()[:300] or None,
                "docker_stdout": stdout.decode()[:300] or None,
            },
        )

    try:
        payload = json.loads(stdout.decode())
    except json.JSONDecodeError as exc:
        raise HTTPException(
            502,
            detail={
                **resolved,
                "reason": "invalid_scan_response",
                "message": f"Invalid scan response from {container}: {exc}",
            },
        ) from exc

    payload.update(resolved)
    payload["container"] = container
    payload["source"] = "comfyui_container"
    return payload


@app.post("/prepare/{service}")
async def prepare(service: str, request: PrepareRequest):
    _validate_service(service)
    try:
        return await resource_manager.prepare(
            service,
            job_id=request.job_id,
            claim_id=request.claim_id,
            model_label=request.model_label,
            minimum_available_gb=workload_minimum_gb(
                service, request.workload_id, frames=request.frames,
                width=request.width, height=request.height,
            ),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/release/{service}")
async def release(service: str, request: ReleaseRequest):
    _validate_service(service)
    try:
        return await resource_manager.release(service, request.claim_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/workloads/status")
async def workloads_status():
    return await resource_manager.status()


@app.post("/start/{service}")
async def start(service: str, wait_ready: bool = True):
    """Compatibility endpoint; prepare then hand the service to idle cleanup."""
    _validate_service(service)
    token = f"compat-{uuid.uuid4()}"
    try:
        result = await resource_manager.prepare(service, claim_id=token, model_label=service)
        await resource_manager.release(service, token)
        return {**result, "action": "started", "ready": True}
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/stop/{service}")
async def stop(service: str):
    _validate_service(service)
    if any(claim.service == service for claim in resource_manager.claims.values()):
        raise HTTPException(409, f"Service {service} has an active workload and cannot be stopped")
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
