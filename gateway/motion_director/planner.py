"""Local shot planning contract; no model, profile, or credential side effects."""
from __future__ import annotations

import math
import re
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from shared.motion_quality import dimensions


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Point(StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Annotation(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    reference_id: str = Field(min_length=1, max_length=80)
    kind: Literal["arrow", "path", "orbit", "waypoint", "subject", "entry", "exit"]
    points: list[Point] = Field(min_length=1, max_length=256)
    label: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def check_geometry(self):
        if self.kind in {"arrow", "path", "orbit"} and len(self.points) < 2:
            raise ValueError("Camera drawings require at least two points")
        return self


class ShotRequest(StrictModel):
    prompt: str = Field(min_length=1, max_length=4000)
    reference_ids: list[str] = Field(min_length=1, max_length=4)
    annotations: list[Annotation] = Field(default_factory=list, max_length=64)
    mode: Literal["auto", "manual"] = "auto"
    preset: Literal["professional", "intimate", "reveal", "action"] = "professional"
    duration: Literal[2, 3, 5, 10, 15, 30] = 2
    orientation: Literal["vertical", "horizontal"] = "vertical"
    workflow: Literal["reference", "image"] = "reference"
    seed: int = Field(default=42, ge=0, le=2**53 - 1)
    director: Literal["local", "opus", "auto"] = "local"
    engine: Literal["h3", "grok"] = "h3"
    plan_id: str | None = Field(default=None, max_length=36)
    pipeline: Literal["prompt", "3d"] = "prompt"
    parent_plan_id: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}$")
    revision: str = Field(default="", max_length=1200)
    quality: Literal['draft', 'native'] = 'draft'

    @model_validator(mode="after")
    def check_references(self):
        if not self.prompt.strip():
            raise ValueError("Enter a cinematic prompt")
        if len(set(self.reference_ids)) != len(self.reference_ids):
            raise ValueError("Reference IDs must be unique")
        if any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", r) for r in self.reference_ids):
            raise ValueError("Invalid reference ID")
        if any(a.reference_id not in self.reference_ids for a in self.annotations):
            raise ValueError("Every annotation must belong to a selected reference")
        if len({a.id for a in self.annotations}) != len(self.annotations):
            raise ValueError("Annotation IDs must be unique")
        if self.workflow == "image" and len(self.reference_ids) != 1:
            raise ValueError("Image-to-video requires exactly one first-frame image")
        if self.workflow == "image" and self.duration > 5:
            raise ValueError("Long videos use the installed reference-to-video continuation graph")
        if self.pipeline == "3d" and self.workflow != "reference":
            raise ValueError("3D direction uses the installed reference-video checkpoint")
        return self


class Camera(StrictModel):
    movement: str = Field(max_length=800)
    targets: list[str] = Field(max_length=8)
    paths: list[Annotation]
    easing: str = "gentle ease-in, steady middle, gentle ease-out"


class Beat(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    action: str = Field(max_length=1200)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end <= self.start:
            raise ValueError("A beat must end after it starts")
        return self


class DirectorDecisions(StrictModel):
    camera_movement: str = Field(min_length=1, max_length=800)
    targets: list[str] = Field(min_length=1, max_length=8)
    composition: str = Field(min_length=1, max_length=1200)
    subject_constraints: list[str] = Field(min_length=1, max_length=12)
    timing: list[Beat] = Field(min_length=1, max_length=6)


class H3Direction(StrictModel):
    """Reference-mode context IR; appearance is independent of Blender geometry."""
    subject_definitions: list[str] = Field(min_length=1, max_length=12)
    summary: str = Field(min_length=1, max_length=1200)
    retention_analysis: list[str] = Field(min_length=1, max_length=12)
    detailed_description: str = Field(min_length=1, max_length=5000)
    overall_soundscape: str = Field(default='Quiet location ambience and natural footsteps. No dialogue.', max_length=1000)
    non_diegetic_music: str = Field(default='N/A', max_length=500)
    inferred_details: list[str] = Field(default_factory=list, max_length=8)

    def compile(self, start=0, end=None, beats=(), continuation=False):
        parts = []
        for field in ('subject_definitions','summary','retention_analysis','detailed_description','overall_soundscape','non_diegetic_music'):
            value = getattr(self, field)
            parts.append(field + ':\n' + ('\n'.join(value) if isinstance(value,list) else value))
        if end is not None:
            # Scene-builder beats can contain implementation terms or obsolete
            # source directions. H3's own detailed_description is authoritative.
            parts.append(f'[Shot 1] Render only scene interval {start:.1f}–{end:.1f}s, lasting {end-start:.1f}s. Continue the specified action at its planned speed; no new event or cut.')
        if continuation:
            parts.append('Continue from the supplied boundary frame with no cut, reset, identity change or change of lens. Preserve all appearance from <Picture 1>.')
        result = '\n\n'.join(parts)
        if len(result)>24000 or any(len(s)>1500 for s in self.subject_definitions+self.retention_analysis+self.inferred_details):
            raise ValueError('H3 context exceeds bounded reference-encoding size')
        return result


def apply_director_decisions(base: "ShotSpec", decisions: DirectorDecisions) -> "ShotSpec":
    beats = decisions.timing
    if abs(beats[0].start) > .01 or abs(beats[-1].end - base.request.duration) > .01:
        raise ValueError("Director timing must cover the requested duration")
    if any(abs(a.end - b.start) > .01 for a, b in zip(beats, beats[1:])):
        raise ValueError("Director timing must be contiguous")
    if any(len(s) > 500 for s in decisions.subject_constraints + decisions.targets):
        raise ValueError("Director text exceeds safe bounds")
    camera = base.camera.model_copy(update={"movement": decisions.camera_movement, "targets": decisions.targets})
    constraints = list(dict.fromkeys(base.subject_constraints + decisions.subject_constraints))
    # Keep references, editable drawings, staging and resource bounds server-owned.
    guidance = " ".join(describe_annotation(a, base.reference_ids.index(a.reference_id) + 1)
                        for a in base.camera.paths + base.staging)
    reference_text = " ".join(f"<Picture {i + 1}> is reference {r}." for i, r in enumerate(base.reference_ids))
    if base.request.workflow == "image":
        reference_text = "Animate the supplied first frame."
        guidance = guidance.replace("<Picture 1>", "the first frame")
    timeline = " ".join(f"{b.start:.1f}–{b.end:.1f}s: {b.action}" for b in beats)
    prompt = f"{reference_text} {base.request.prompt}\nCamera: {camera.movement}. {decisions.composition}\n{timeline}\n{' '.join(constraints)}\n{guidance}"
    if base.request.revision:
        prompt += "\nScene correction: " + base.request.revision
    if len(prompt) > 24000:
        raise ValueError("Compiled director prompt is too large")
    return base.model_copy(update={"planner": "claude-code-opus", "camera": camera, "timing": beats,
                                   "composition": decisions.composition, "subject_constraints": constraints,
                                   "renderer_prompt": prompt})


class ShotSpec(StrictModel):
    version: Literal["0.1"] = "0.1"
    planner: str = "local-director"
    request: ShotRequest
    reference_ids: list[str]
    camera: Camera
    timing: list[Beat]
    composition: str
    subject_constraints: list[str]
    staging: list[Annotation]
    width: int
    height: int
    fps: Literal[24] = 24
    frames: int
    renderer_prompt: str
    conditioning: Literal["prompt-guidance", "reference-video"] = "prompt-guidance"
    notes: list[str]
    # Kept as JSON here to avoid a schema import cycle; validate_scene is the
    # mandatory boundary before persistence, rendering and inference.
    scene: dict | None = None
    h3: H3Direction | None = None


def segment_shot(scene: ShotSpec, index: int) -> ShotSpec:
    """One bounded clip from a single already-validated scene plan; no LLM calls."""
    duration = min(scene.request.duration, 5)
    count = math.ceil(scene.request.duration / duration)
    if not 0 <= index < count:
        raise ValueError("Invalid segment index")
    start, end = index * duration, (index + 1) * duration
    beats = [Beat(start=max(b.start, start) - start, end=min(b.end, end) - start, action=b.action)
             for b in scene.timing if b.start < end and b.end > start]
    refs = " ".join(f"<Picture {i+1}> preserves reference identity {r}." for i, r in enumerate(scene.reference_ids))
    context = ("<Video 1> is the preceding shot's final motion. Continue its direction, lighting, pose and geography. "
               "The guide image is the exact boundary frame. Start there and continue naturally; no reset or cut." if index else "Establish the scene without rushing.")
    if scene.scene:
        context = "<Video 1> is this clip's opening Blender motion reference. Keep the reference camera behind the running character when specified. " + ("Continue from the preceding AI clip's boundary image without a cut." if index else "Establish this rendered scene geography.")
    timeline = " ".join(f"{b.start:.2f}–{b.end:.2f}s: {b.action}" for b in beats)
    prompt = f"{refs} {scene.request.prompt}\nClip {index+1}/{count}, scene time {start}–{end}s. {context}\nCamera: {scene.camera.movement}. {scene.composition}\n{timeline}\n{' '.join(scene.subject_constraints)}\n" + " ".join(describe_annotation(a, scene.reference_ids.index(a.reference_id)+1) for a in scene.camera.paths + scene.staging)
    if scene.request.revision:
        prompt += "\nScene correction: " + scene.request.revision
    if scene.h3:
        prompt = scene.h3.compile(start, end, beats, bool(index))
    return scene.model_copy(update={"request": scene.request.model_copy(update={"duration": duration, "seed": (scene.request.seed + index) % (2**53), "plan_id": None}),
                                   "frames": 5 + 17 * math.ceil((duration * 24 - 5) / 17), "timing": beats, "renderer_prompt": prompt})


class ShotPlanner(Protocol):
    def plan(self, request: ShotRequest) -> ShotSpec: ...


PROFESSIONAL_CINEMATIC_DIRECTOR = {
    "movement": "one slow, motivated dolly toward the main subject",
    "composition": "Keep the main subject on a rule-of-thirds line, with clear silhouette, eye-line, headroom and lead room. Maintain the 180-degree axis and foreground/midground/background separation.",
    "constraints": [
        "Preserve reference identity, face, anatomy, costume, scale and scene geography.",
        "Stage one readable main action; maintain direction of travel and avoid occlusion.",
        "One continuous shot, no cuts, no abrupt zooms, no unmotivated camera shake.",
        "Use deliberate acceleration and deceleration; finish on a stable composition.",
    ],
}


def describe_annotation(a: Annotation, picture: int) -> str:
    # Preserve every editable vertex in the spec, but keep dense touch strokes
    # from multiplying text-encoder tokens on the shared-UMA host.
    sampled = a.points if len(a.points) <= 8 else [a.points[round(i * (len(a.points) - 1) / 7)] for i in range(8)]
    points = " → ".join(f"({p.x:.2f}, {p.y:.2f})" for p in sampled)
    return f"{a.kind} {a.label or a.id} on <Picture {picture}>: {points} (normalized image coordinates, x right, y down)"


class LocalDirector:
    def plan(self, request: ShotRequest) -> ShotSpec:
        prompt = request.prompt.strip()
        movement = PROFESSIONAL_CINEMATIC_DIRECTOR["movement"]
        text = prompt.lower()
        for token, description in (("orbit", "a slow orbit around the subject, maintaining its screen position"),
                                   ("pan", "a measured pan following the action"),
                                   ("track", "a steady lateral tracking move with consistent lead room"),
                                   ("pull back", "a gentle pull-back revealing the environment"),
                                   ("static", "a locked camera with readable subject movement")):
            if token in text:
                movement = description
                break
        if request.preset == "intimate":
            movement = "a subtle push-in, holding eye-line and breathing room"
        elif request.preset == "reveal":
            movement = "a slow pull-back that reveals context in coherent depth layers"
        elif request.preset == "action":
            movement = "a smooth tracking move that keeps the main action readable"
        paths = [a for a in request.annotations if a.kind in {"arrow", "path", "orbit", "waypoint"}]
        staging = [a for a in request.annotations if a.kind in {"subject", "entry", "exit"}]
        if paths and request.mode == "manual":
            movement = "Follow the annotated camera direction and ordered waypoints smoothly; orbit markers define an arc around their center."
        duration = request.duration
        timing = [Beat(start=0, end=duration * .2, action="Establish the subject and spatial relationships."),
                  Beat(start=duration * .2, end=duration * .8, action=f"Develop the main action: {prompt}"),
                  Beat(start=duration * .8, end=duration, action="Ease the camera to a composed finish; let the action resolve.")]
        constraints = list(PROFESSIONAL_CINEMATIC_DIRECTOR["constraints"])
        if staging:
            constraints.append("Respect marked subjects, entry and exit positions; do not swap subject identities.")
        composition = PROFESSIONAL_CINEMATIC_DIRECTOR["composition"]
        reference_text = " ".join(f"<Picture {i + 1}> is reference {r}." for i, r in enumerate(request.reference_ids))
        if request.workflow == "image":
            reference_text = "Animate the supplied first frame; preserve its subject identity and initial composition."
        drawing_text = " ".join(describe_annotation(a, request.reference_ids.index(a.reference_id) + 1)
                                for a in (paths if request.mode == "manual" else []) + staging)
        if request.workflow == "image":
            drawing_text = drawing_text.replace("<Picture 1>", "the first frame")
        beats = " ".join(f"{b.start:.1f}–{b.end:.1f}s: {b.action}" for b in timing)
        renderer_prompt = f"{reference_text} {prompt}\nCamera: {movement}. {composition}\n{beats}\n{' '.join(constraints)}\n{drawing_text}"
        width, height = dimensions(request.quality, request.orientation)
        return ShotSpec(request=request, reference_ids=request.reference_ids,
                        camera=Camera(movement=movement, targets=[a.label or a.id for a in staging if a.kind == "subject"] or ["main subject"], paths=paths if request.mode == "manual" else []),
                        timing=timing, composition=composition, subject_constraints=constraints, staging=staging,
                        width=width, height=height, frames=(duration * 24 if duration > 5 else 5 + 17 * math.ceil((duration * 24 - 5) / 17)),
                        renderer_prompt=renderer_prompt,
                        notes=["Drawings guide the prompt; exact trajectories are not guaranteed. Control-video conditioning awaits a validated, size-matched control input.",
                               "5–30 seconds use sequential bounded clips with reference-video context and a boundary-frame guide. Final output is trimmed to the requested duration." if duration >= 5 else "Duration rounds up to H3's 17k+5 frame grid."])


class HermesDirector:
    """Future adapter boundary. Never invoke an unverified launcher or new profile."""
    def plan(self, request: ShotRequest) -> ShotSpec:
        raise RuntimeError("Hermes launcher and local/no-cost planning capabilities must be verified first")
