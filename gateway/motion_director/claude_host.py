"""Use the existing host Claude Code login; never mount credentials into Docker."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import time
import re
import uuid
import copy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from .planner import DirectorDecisions, H3Direction, LocalDirector, ShotRequest, apply_director_decisions
from .scene import SceneSpec
from .usage import observe, record, PlanningUsage


class SceneDecisions(DirectorDecisions):
    scene: SceneSpec
    h3: H3Direction


def planning_schema(scene=False):
    schema=(SceneDecisions if scene else DirectorDecisions).model_json_schema()
    if scene:
        entity=schema['$defs']['Entity']
        character=copy.deepcopy(entity);prop=copy.deepcopy(entity)
        character['properties']['geometry']={'type':'string','const':'mannequin'}
        character['properties']['size']={'type':'object','properties':{axis:{'type':'number','const':1} for axis in 'xyz'},
                                          'required':list('xyz'),'additionalProperties':False,
                                          'description':'Uniform rig scale. Character height is character.height_m; never put body dimensions here.'}
        prop['properties']['geometry']['enum']=[g for g in prop['properties']['geometry']['enum'] if g!='mannequin']
        schema['$defs']['Entity']={'anyOf':[character,prop]}
    return schema


SCENE_RULES = """Also generate a complete editable metric 3D blockout scene. Z is up; x right, y depth. Infer approximate relative depth, scale and simple geometry from the images, never exact reconstruction. Describe the visual evidence and uncertainty in reference_analysis. Use reusable mannequin rigs only for people. Procedural-v1 supports only idle, walk and run. For fighting select quaternius-v1 and an explicit actions timeline from the supplied authored_clips catalog, with clip=idle. Static props have empty paths and idle clips. Every moving path starts at time 0 at the initial position, ends at the exact requested duration, and uses increasing times. Use realistic travel speed (run about 3m/s), size x=1 for a 1.8m mannequin. Set boxes/spheres/cylinders with dimensions in metres. Avoid overlapping paths/obstacles. You may select asset IDs ONLY from the supplied existing Forge3D library; these assets are unrigged props. Camera targets must name an entity ID. Follow camera stays behind travel heading at the selected distance and height, aiming at aim_height. Use a follow camera when the user asks third-person/behind. Orbit uses orbit_start/end degrees; track/static need explicit timed camera positions. Use only a few props and 2-5 path points. No added character/object motion unless requested. Arbitrary character retargeting, grappling, kicks and unarmed blocks are unavailable; disclose missing motion assets. For revisions preserve all unspecified geometry, paths and settings from previous_scene. The validated specification will be compiled into Blender Python; do not output freeform executable code."""
SCENE_RULES += """
Interpret short prompts conservatively. 'Character running and camera following' means the visible main character runs continuously and the camera matches travel speed; infer framing and lens from the image, without inventing events, turns, cuts or a new story. Identify visible clothing, hair, accessories, dominant colors, major foreground/background objects, vanishing direction, relative depth and light sources. Use semantic train/building/sign/canopy geometry ONLY for corresponding visible structures; do not replace an entire environment with one wall. Keep background people idle unless asked to animate them. Use character height, shoulder_width, skin/hair/top/bottom/shoes colors and bag only as supported by image evidence; all are approximate. Use lighting colors/direction from the reference. Additional characters get independent rigs/paths only when their motion is requested. A follow camera must maintain subject scale and clear silhouette, not let the runner recede. Include conservative inferred production details in h3.inferred_details for review.

Return TWO SEPARATE outputs: scene is the Blender spatial/motion specification; h3 is the appearance-preserving reference-generation context in the publisher's six-section format. Never describe the final H3 subject as a mannequin, proxy or placeholder, or carry Blender implementation details into the final appearance. h3.subject_definitions must bind <Subject 1> identity and clothing to <Picture 1>; bind environment/style/light to image evidence too. <Video 1> supplies ONLY relative spatial motion, timing and camera tracking. h3.retention_analysis must explicitly retain original image appearance, lighting and identity and exclude transfer of the video's simplified materials/character appearance. h3.detailed_description describes one continuous shot's composition, lens, action, camera speed and lighting, with stable object layout and identity over time. Use h3.summary for a short human-readable shot plan. No duplicated contradictory camera instructions, fabricated dialogue or music; non_diegetic_music is N/A unless requested. Preserve the simple user's original intent rather than adding entertainment events. For a scene revision, update h3 to the FINAL resolved intent, never append obsolete user directions or describe the revision history."""
SCENE_RULES += """ The phrase 'camera following' or 'follow camera' requires camera.mode=follow, behind the subject travelling in the SAME direction, unless the USER explicitly requests leading/from the front. Do not reinterpret following as a camera retreating ahead of the subject just because the photograph shows the subject's face. Prioritize requested camera behavior over copying the image's exact view. Choose a rear three-quarter framing or rear follow while retaining visible identity details. Camera path and character heading must agree with the h3 description."""
SCENE_RULES += """ Character/train/building positions are their base on the ground; boxes, signs and canopies use their geometric center. Keep recognized static foreground/background objects present even when no animation is requested for them; lack of a suitable detailed asset permits approximate primitive geometry, not silently omitting them. Record unknown depth and occluded details as uncertainty."""
SCENE_RULES += """ For 'the characters start fighting', identify two visible characters and bind each to its original image identity, clothing and initial relative position. Use separate quaternius-v1 armatures with independent paths and facing=<opponent ID>. Stage a brief readable exchange using punch_jab/punch_cross, synchronized hit_chest/hit_head reactions, a dodge_crouch and recovery if duration permits. Do not invent unavailable clips. Authored guard fills action gaps. Ordered actions cannot overlap on one character; start/end must fit duration and blend=.06-.2 seconds. Preserve attack natural speed (jab .87s, cross 1s), with contact near the middle. Each attack requires an interactions entry (actor,target,time,outcome,limb,zone) and a simultaneous defender reaction or dodge. Both feet stay on the ground, actors about .8-.95m apart at contact, step only during walk/jog cues, and never pass through each other, walls or train. Camera.include must include the other fighter. Prefer a smooth, mostly lateral track camera showing both full bodies with stable screen sides and enough margins for fists. Blender evaluates actual joint contact before generation. h3 binds BOTH subjects separately to their image evidence and retains stationary environment layout. This is limited authored humanoid choreography, not general realistic interaction support."""

API_ENV = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN",
           "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"}
status_cache = None
status_lock = asyncio.Lock()
status_task = None
rate_failures = 0
VERIFICATION_STATE = Path(os.getenv('NEONFORGE_CLAUDE_VERIFICATION_STATE', str(Path(__file__).resolve().parents[2] / '.motion-runtime/claude-verification.json')))
SYSTEM_PROMPT = """You are a Professional Cinematic Director. Return only the requested structured decisions for ONE continuous cinematic shot.
Use intentional motivated camera movement, coherent geography, readable action and cinematic pacing. User drawings, ordered waypoints and identity constraints take priority. Analyze the supplied reference images. Avoid arbitrary cuts, contradictory movements, identity drift and invented subjects. Divide the exact requested duration into contiguous timed beats. References and hardware dimensions are immutable. No tools, file access, agents, API billing or follow-up calls are permitted."""


def safe_environment() -> dict[str, str]:
    # HOME retains the owner's actual value; no new auth home/profile is created.
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    env = {k: v for k, v in os.environ.items() if k in allowed}
    env.update(CLAUDE_CODE_MAX_OUTPUT_TOKENS="10500", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    return env


async def run_cli(args: list[str], data: bytes | None = None, timeout: float = 80) -> dict:
    started=time.monotonic();invocation=str(uuid.uuid4());model_call='-p' in args
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
        if model_call:
            record(observe(b'',invocation,time.monotonic()-started,'interrupted; token usage unavailable')[1])
        raise
    if len(stdout) > 256000:
        if model_call:
            record(observe(b'',invocation,time.monotonic()-started,'output bound exceeded; usage unavailable')[1])
        raise RuntimeError("Claude output exceeded the planning bound")
    # Never return raw stderr, account identifiers, auth output or debug logs.
    result,usage=observe(stdout,invocation,time.monotonic()-started,
                         'completed' if process.returncode==0 else 'CLI failed')
    if model_call:
        record(usage)
    if result is None:
        raise RuntimeError(f"Claude Code returned no terminal planning result (exit {process.returncode})")
    if model_call:
        result['_motion_usage']=usage.model_dump()
    return result


def _stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def retry_delay(value, now=None):
    """Retry-After is either seconds or an HTTP date. Never shorten it."""
    now = time.time() if now is None else now
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - now
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0, seconds) if seconds < float('inf') else None


def _cached_status():
    if status_cache:
        data = dict(status_cache[1])
        if status_cache[0] <= time.monotonic() and data.get('available'):
            data.update(available=False, state='verification_due', reason='Subscription verification expired. Verify before new Claude planning.')
        return data
    # Only a refusal/cooldown survives a restart, never a billing authorization.
    try:
        data = json.loads(VERIFICATION_STATE.read_text())
        if data.get('state') == 'verification_rate_limited':
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {'available': False, 'state': 'unverified', 'reason': 'Verify the existing Claude subscription before new planning. Saved scenes and local camera edits remain available.', 'next_check_at': None}


async def _verify_status():
    global status_cache, rate_failures
    async with status_lock:
        cached = _cached_status()
        if cached.get('next_check_epoch', 0) > time.time():
            return cached
        try:
            previous = json.loads(VERIFICATION_STATE.read_text())
            if previous.get('state') == 'verification_rate_limited':
                rate_failures = max(rate_failures, previous.get('consecutive_rate_limits', 0))
        except (OSError, ValueError, TypeError):
            pass
        result = await _subscription_status()
        limited = result.get('state') == 'verification_rate_limited'
        rate_failures = rate_failures + 1 if limited else 0
        delay = max(300 * 2 ** min(rate_failures - 1, 4), result.pop('_retry_after', 0) or 0) if limited else (300 if result['available'] else 60)
        now = time.time()
        result.update(checked_at=_stamp(now), next_check_at=_stamp(now + delay), next_check_epoch=now + delay,
                      consecutive_rate_limits=rate_failures)
        result.setdefault('state', 'ready' if result['available'] else 'verification_failed')
        status_cache = (time.monotonic() + delay, dict(result))
        # Sanitized operational record, no auth output, account ID or credentials.
        VERIFICATION_STATE.parent.mkdir(parents=True, exist_ok=True)
        part = VERIFICATION_STATE.with_suffix('.tmp')
        part.write_text(json.dumps(result)); part.chmod(0o600); part.replace(VERIFICATION_STATE)
        with VERIFICATION_STATE.with_suffix('.jsonl').open('a') as audit:
            audit.write(json.dumps(result) + '\n')
        return dict(result)


async def subscription_status(check=True) -> dict:
    global status_task
    if any(os.environ.get(k) for k in API_ENV):
        return {"available": False, "state": "provider_refused", "reason": "API/provider environment is present; subscription-only planning is refused."}
    cached = _cached_status()
    if not check or cached.get('next_check_epoch', 0) > time.time():
        return {**cached, 'checking': bool(status_task and not status_task.done())}
    # Cancellation of one disconnected browser must not cancel the shared probe.
    if status_task is None or status_task.done():
        status_task = asyncio.create_task(_verify_status())
    return dict(await asyncio.shield(status_task))


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
            expires = oauth.get('expiresAt')
            if isinstance(expires,(int,float)) and expires/1000 <= time.time():
                return {'available':False, 'state':'authentication_expired', 'subscription':result.get('subscriptionType'),
                        'reason':'The saved Claude Code sign-in has expired. On the DGX, run claude auth login --claudeai, then verify again. Saved scenes and local camera edits remain available.'}
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get("https://api.anthropic.com/api/oauth/usage", headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"})
            if response.status_code == 429:
                return {"available": False, "state": "verification_rate_limited", "subscription": result.get('subscriptionType'),
                        "http_status": 429, "_retry_after": retry_delay(response.headers.get('Retry-After')),
                        "retry_after_provided": 'Retry-After' in response.headers,
                        "reason": "Claude's subscription verification endpoint returned HTTP 429. Model usage is unknown. Retry verification after the displayed time; saved scenes remain usable."}
            if response.status_code != 200:
                return {'available':False, 'state':'authentication_failed' if response.status_code in {401,403} else 'verification_failed',
                        'http_status':response.status_code, 'subscription':result.get('subscriptionType'),
                        'reason':f'Claude subscription verification returned HTTP {response.status_code}. Subscription usage and extra-usage status could not be verified.'}
            response.raise_for_status()
            quota = response.json()
            if not isinstance(quota,dict) or not isinstance(quota.get('extra_usage'),dict):
                return {'available':False,'state':'verification_failed','reason':'Claude returned an unrecognized usage response; extra-usage billing cannot be verified.'}
            extra_disabled = quota.get("extra_usage", {}).get("is_enabled") is False
            exhausted = any(isinstance(quota.get(key), dict) and quota[key].get("utilization", 0) >= 100 for key in ("five_hour", "seven_day", "seven_day_opus"))
            ready = extra_disabled and not exhausted
            if not extra_disabled:
                return {"available": False, "state": "extra_usage_not_disabled", "auth_method": "claude.ai", "subscription": result.get("subscriptionType"),
                        "reason": "Paid extra usage is enabled or unverified. Disable Usage credits/Extra usage to enable subscription-only Opus."}
            if exhausted:
                return {"available": False, "state": "model_usage_exhausted", "subscription": result.get('subscriptionType'),
                        "reason": "Claude reports exhausted subscription model usage. Wait for the usage window to reset; no paid fallback is used."}
        return {"available": ready, "auth_method": result.get("authMethod"), "subscription": result.get("subscriptionType"),
                "reason": "" if ready else "Subscription quota or disabled extra usage could not be verified; no paid fallback is allowed."}
    except (OSError, RuntimeError, TimeoutError, ValueError, httpx.HTTPError):
        return {"available": False, "reason": "The existing Claude Code launcher/authentication is unavailable."}


async def plan_with_opus(request: ShotRequest, reference_paths: list[str], assets_root: Path, previous_scene=None):
    started=time.monotonic()
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
        capabilities = scene_status()
        context.update(previous_scene=previous_scene, asset_library=capabilities['assets'][:30], authored_clips=capabilities['authored_clips'])
        if re.search(r'\bfight(?:ing)?\b',request.prompt,re.I) and request.duration>=5:
            from .choreography import fight_study
            study=fight_study().model_dump()
            context['validated_choreography_example']={'duration':5, 'entities':[{k:e[k] for k in ('id','geometry','rig','size','clip','character','position','path','facing','actions')} for e in study['entities'] if e.get('actions')],
                                                     'interactions':study['interactions'], 'camera':study['camera'],
                                                     'instruction':'Reuse validated relative timing/reach where appropriate; adapt identities, environment, camera and placement to the actual supplied image. Extend the final hold for longer scenes. Do not copy unrelated environment geometry.'}
    content.append({"type": "text", "text": json.dumps(context)})
    message = {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}
    args = ["-p", "--safe-mode", "--restricted", "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--setting-sources", "", "--no-session-persistence", "--model", "opus", "--effort", "low",
            "--max-turns", "1", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--system-prompt", SYSTEM_PROMPT + (SCENE_RULES if request.pipeline == "3d" else ""),
            "--json-schema", json.dumps(planning_schema(request.pipeline == '3d'))]
    # Exactly one bounded print invocation; no retry or model/API fallback.
    result = await run_cli(args, (json.dumps(message) + "\n").encode(), timeout=240 if request.pipeline == "3d" else 80)
    try:
        return finish_plan(result,request,base,started)
    except (ValueError,RuntimeError) as exc:
        path=Path(os.getenv('NEONFORGE_MOTION_PLAN_DIAGNOSTICS',str(Path(__file__).resolve().parents[2]/'.motion-runtime/plan-validation.jsonl')))
        path.parent.mkdir(parents=True,exist_ok=True)
        # Only model-authored scene data and our validation error, never auth,
        # account metadata, raw CLI events or stderr.
        with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600),'a') as stream:
            stream.write(json.dumps({'time':_stamp(time.time()),'error':str(exc)[:1800],
                'subtype':result.get('subtype'),'structured_output':result.get('structured_output')})+'\n')
        raise


def finish_plan(result,request,base,started):
    structured = result.get("structured_output")
    if not structured:
        raise RuntimeError("Opus returned no validated planning decisions (" + str(result.get('subtype','unstructured'))[:60] + "); no 3D scene was substituted.")
    full = SceneDecisions.model_validate(structured) if request.pipeline == "3d" else None
    decisions = DirectorDecisions.model_validate({k:v for k,v in structured.items() if k not in {"scene", "h3"}})
    shot = apply_director_decisions(base, decisions)
    shot.planning_usage=PlanningUsage(source='opus',new_claude_invocations=1,
        elapsed_seconds=round(time.monotonic()-started,3),original=result.get('_motion_usage'))
    if full:
        full.scene.validate_timeline(request.duration)
        camera_intent = request.revision or request.prompt
        requires_follow = re.search(r'\bcamera\s+(?:is\s+)?follow(?:s|ing)?\b|\bfollow\s+camera\b', camera_intent, re.I)
        if requires_follow and not re.search(r'\b(front|lead|leading)\b', camera_intent, re.I) and full.scene.camera.mode != 'follow':
            raise ValueError('A requested follow camera must remain behind the moving subject')
        shot.scene = full.scene.model_dump()
        shot.conditioning = "reference-video"
        shot.h3 = full.h3
        shot.renderer_prompt = full.h3.compile()
        shot.notes = ["Editable approximate reconstruction; reusable stylized rig clips. Review Blender motion before AI generation.",
                      "Blender reference-video conditioning guides H3; exact paths, poses and stationary objects are not guaranteed in its output."]
    return shot
