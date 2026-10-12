"""Local shot planning contract; no model, profile, or credential side effects."""
from __future__ import annotations

import math
import re
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    duration: Literal[2, 3, 5] = 2
    orientation: Literal["vertical", "horizontal"] = "vertical"
    workflow: Literal["reference", "image"] = "reference"
    seed: int = Field(default=42, ge=0, le=2**53 - 1)

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
        return self


class Camera(StrictModel):
    movement: str
    targets: list[str]
    paths: list[Annotation]
    easing: str = "gentle ease-in, steady middle, gentle ease-out"


class Beat(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    action: str


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
    conditioning: Literal["prompt-guidance"] = "prompt-guidance"
    notes: list[str]


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
        width, height = (384, 672) if request.orientation == "vertical" else (672, 384)
        return ShotSpec(request=request, reference_ids=request.reference_ids,
                        camera=Camera(movement=movement, targets=[a.label or a.id for a in staging if a.kind == "subject"] or ["main subject"], paths=paths if request.mode == "manual" else []),
                        timing=timing, composition=composition, subject_constraints=constraints, staging=staging,
                        width=width, height=height, frames=5 + 17 * math.ceil((duration * 24 - 5) / 17),
                        renderer_prompt=renderer_prompt,
                        notes=["Drawings guide the prompt; exact trajectories are not guaranteed. Control-video conditioning awaits a validated, size-matched control input.",
                               "Duration rounds up to H3's 17k+5 frame grid."])


class HermesDirector:
    """Future adapter boundary. Never invoke an unverified launcher or new profile."""
    def plan(self, request: ShotRequest) -> ShotSpec:
        raise RuntimeError("Hermes launcher and local/no-cost planning capabilities must be verified first")
