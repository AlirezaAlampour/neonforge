"""Use the existing host Claude Code login; never mount credentials into Docker."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import time

import httpx

from .planner import DirectorDecisions, LocalDirector, ShotRequest, apply_director_decisions
from .scene import SceneSpec


class SceneDecisions(DirectorDecisions):
    scene: SceneSpec


SCENE_RULES = """Also generate a complete editable metric 3D blockout scene. Z is up; x right, y depth. Infer approximate relative depth, scale and simple geometry from the images, never exact reconstruction. Describe the visual evidence and uncertainty in reference_analysis. Use reusable mannequin rigs only for people; choose idle, walk or run clips. Static props have empty paths and idle clips. Every moving path starts at time 0 at the initial position, ends at the exact requested duration, and uses increasing times. Use realistic travel speed (run about 3m/s), size x=1 for a 1.8m mannequin. Set boxes/spheres/cylinders with dimensions in metres. Avoid overlapping paths/obstacles. You may select asset IDs ONLY from the supplied existing Forge3D library; these assets are unrigged props. Camera targets must name an entity ID. Follow camera stays behind travel heading at the selected distance and height, aiming at aim_height. Use a follow camera when the user asks third-person/behind. Orbit uses orbit_start/end degrees; track/static need explicit timed camera positions. Use only a few props and 2-5 path points. No added character/object motion unless requested. Complex interactions and realistic anatomy/mocap are unavailable: explain these limitations. For revisions preserve all unspecified geometry, paths and settings from previous_scene. The validated specification will be compiled into Blender Python; do not output freeform executable code."""

API_ENV = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN",
           "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"}
status_cache = None
status_lock = asyncio.Lock()
SYSTEM_PROMPT = """You are a Professional Cinematic Director. Return only the requested structured decisions for ONE continuous cinematic shot.
Use intentional motivated camera movement, coherent geography, readable action and cinematic pacing. User drawings, ordered waypoints and identity constraints take priority. Analyze the supplied reference images. Avoid arbitrary cuts, contradictory movements, identity drift and invented subjects. Divide the exact requested duration into contiguous timed beats. References and hardware dimensions are immutable. No tools, file access, agents, API billing or follow-up calls are permitted."""


def safe_environment() -> dict[str, str]:
    # HOME retains the owner's actual value; no new auth home/profile is created.
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    env = {k: v for k, v in os.environ.items() if k in allowed}
    env.update(CLAUDE_CODE_MAX_OUTPUT_TOKENS="6500", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    return env


async def run_cli(args: list[str], data: bytes | None = None, timeout: float = 80) -> dict:
    executable = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    process = await asyncio.create_subprocess_exec(executable, *args, env=safe_environment(),
                                                  stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                  stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _stderr = await asyncio.wait_for(process.communicate(data), timeout)
    except (TimeoutError, asyncio.CancelledError):
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            process.kill(); await process.wait()
        raise
    if len(stdout) > 256000:
        raise RuntimeError("Claude output exceeded the planning bound")
    # Never return raw stderr, account identifiers, auth output or debug logs.
    try:
        result = json.loads(stdout)
    except ValueError:
        # This installed CLI requires stream output for image-bearing stream
        # input. Keep only the terminal result; never expose verbose/auth events.
        result = None
        for line in stdout.splitlines():
            try:
                item = json.loads(line)
                if isinstance(item, dict) and item.get("type") == "result":
                    result = item
            except ValueError:
                continue
        if result is None:
            raise RuntimeError(f"Claude Code returned no terminal planning result (exit {process.returncode})")
    return result


async def subscription_status() -> dict:
    global status_cache
    if any(os.environ.get(k) for k in API_ENV):
        return {"available": False, "reason": "API/provider environment is present; subscription-only planning is refused."}
    async with status_lock:
        if status_cache and status_cache[0] > time.monotonic():
            return dict(status_cache[1])
        result = await _subscription_status()
        # UI capability polling must not exhaust the read-only quota endpoint.
        # Failed checks also cool down; they never inherit a prior success.
        status_cache = (time.monotonic() + 60, dict(result))
        return result


async def _subscription_status() -> dict:
    if any(os.environ.get(k) for k in API_ENV):
        return {"available": False, "reason": "API/provider environment is present; subscription-only planning is refused."}
    try:
        result = await run_cli(["--safe-mode", "--setting-sources", "", "auth", "status", "--json"], timeout=15)
        ready = result.get("loggedIn") is True and result.get("authMethod") == "claude.ai" and result.get("apiProvider") == "firstParty" and result.get("subscriptionType") in {"pro", "max", "team", "enterprise"}
        if ready:
            # Read-only quota verification prevents overflow/extra-usage billing.
            credentials = Path(result.get("configDirectory", str(Path.home() / ".claude"))) / ".credentials.json"
            if not credentials.is_relative_to(Path.home()) or not credentials.is_file():
                return {"available": False, "reason": "Subscription login is present, but paid extra-usage status cannot be verified."}
            oauth = json.loads(credentials.read_text()).get("claudeAiOauth", {})
            token = oauth.get("accessToken")
            if not token:
                return {"available": False, "reason": "Existing OAuth quota verification is unavailable."}
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get("https://api.anthropic.com/api/oauth/usage", headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"})
            if response.status_code == 429:
                return {"available": False, "reason": "Claude subscription verification is rate limited. Wait a minute before planning; no paid fallback is used."}
            response.raise_for_status()
            quota = response.json()
            extra_disabled = quota.get("extra_usage", {}).get("is_enabled") is False
            exhausted = any(isinstance(quota.get(key), dict) and quota[key].get("utilization", 0) >= 100 for key in ("five_hour", "seven_day", "seven_day_opus"))
            ready = extra_disabled and not exhausted
            if not extra_disabled:
                return {"available": False, "auth_method": "claude.ai", "subscription": result.get("subscriptionType"),
                        "reason": "Paid extra usage is enabled or unverified. Disable Usage credits/Extra usage to enable subscription-only Opus."}
        return {"available": ready, "auth_method": result.get("authMethod"), "subscription": result.get("subscriptionType"),
                "reason": "" if ready else "Subscription quota or disabled extra usage could not be verified; no paid fallback is allowed."}
    except (OSError, RuntimeError, TimeoutError, ValueError, httpx.HTTPError):
        return {"available": False, "reason": "The existing Claude Code launcher/authentication is unavailable."}


async def plan_with_opus(request: ShotRequest, reference_paths: list[str], assets_root: Path, previous_scene=None):
    status = await subscription_status()
    if not status["available"]:
        raise RuntimeError(status["reason"])
    content = []
    for index, relative in enumerate(reference_paths):
        path = (assets_root / relative).resolve()
        if not path.is_relative_to(assets_root.resolve()) or not path.is_file() or path.stat().st_size > 20 * 1024**2:
            raise ValueError("Invalid reference path")
        # Existing FFmpeg creates a small vision input; original references stay intact.
        process = await asyncio.create_subprocess_exec("ffmpeg", "-v", "error", "-i", str(path), "-vf",
                                                       "scale=1024:1024:force_original_aspect_ratio=decrease", "-frames:v", "1", "-threads", "1",
                                                       "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "pipe:1",
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            image, _ = await asyncio.wait_for(process.communicate(), 15)
        except (TimeoutError, asyncio.CancelledError):
            process.kill(); await process.wait(); raise
        if process.returncode or not image or len(image) > 2 * 1024**2:
            raise ValueError("Reference could not be prepared for vision planning")
        content.extend([{"type": "text", "text": f"Reference <Picture {index + 1}> (ID {request.reference_ids[index]})"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(image).decode()}}])
    base = LocalDirector().plan(request)
    compact = request.model_dump(exclude={"plan_id", "annotations"})
    compact["annotations"] = [{**a.model_dump(), "points": [p.model_dump() for p in (a.points if len(a.points) <= 8 else [a.points[round(i * (len(a.points)-1)/7)] for i in range(8)])]} for a in request.annotations]
    context = {"shot": compact, "preset_rules": base.subject_constraints, "baseline_direction": base.camera.movement}
    if request.pipeline == "3d":
        from .scene_host import status as scene_status
        context.update(previous_scene=previous_scene, asset_library=scene_status()["assets"][:30])
    content.append({"type": "text", "text": json.dumps(context)})
    message = {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}
    args = ["-p", "--safe-mode", "--restricted", "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--setting-sources", "", "--no-session-persistence", "--model", "opus", "--effort", "low",
            "--max-turns", "1", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--system-prompt", SYSTEM_PROMPT + (SCENE_RULES if request.pipeline == "3d" else ""),
            "--json-schema", json.dumps((SceneDecisions if request.pipeline == "3d" else DirectorDecisions).model_json_schema())]
    # Exactly one bounded print invocation; no retry or model/API fallback.
    result = await run_cli(args, (json.dumps(message) + "\n").encode(), timeout=180 if request.pipeline == "3d" else 80)
    structured = result.get("structured_output")
    if not structured:
        raise RuntimeError("Opus returned no validated planning decisions; deterministic fallback is available.")
    full = SceneDecisions.model_validate(structured) if request.pipeline == "3d" else None
    decisions = DirectorDecisions.model_validate({k:v for k,v in structured.items() if k != "scene"})
    shot = apply_director_decisions(base, decisions)
    if full:
        shot.scene = full.scene.validate_timeline(request.duration).model_dump()
        shot.conditioning = "reference-video"
        shot.notes = ["Editable approximate reconstruction; reusable stylized rig clips. Review Blender motion before AI generation.",
                      "Blender reference-video conditioning guides H3; exact paths, poses and stationary objects are not guaranteed in its output."]
    return shot
