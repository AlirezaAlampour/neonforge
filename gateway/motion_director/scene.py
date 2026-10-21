"""Validated, editable metric blockouts. No model-authored Python is executed."""
from __future__ import annotations

import math
from typing import Literal
from pydantic import Field, model_validator
from .planner import StrictModel
from .animation_library import CLIPS


class Vec3(StrictModel):
    x: float = Field(default=0, ge=-150, le=150)
    y: float = Field(default=0, ge=-150, le=150)
    z: float = Field(default=0, ge=-10, le=50)

    def xyz(self):
        return [self.x, self.y, self.z]


class Key(StrictModel):
    time: float = Field(ge=0, le=30)
    position: Vec3


class CharacterLook(StrictModel):
    height_m: float = Field(default=1.8, ge=.8, le=2.4)
    shoulder_width: float = Field(default=.4, ge=.2, le=.7)
    skin: str = Field(default='#bd8b68', pattern=r'^#[a-fA-F0-9]{6}$')
    hair: str = Field(default='#463127', pattern=r'^#[a-fA-F0-9]{6}$')
    top: str = Field(default='#e6dccb', pattern=r'^#[a-fA-F0-9]{6}$')
    bottom: str = Field(default='#746259', pattern=r'^#[a-fA-F0-9]{6}$')
    shoes: str = Field(default='#d5b56c', pattern=r'^#[a-fA-F0-9]{6}$')
    bag: bool = False


class Lighting(StrictModel):
    description: str = Field(default='Soft environment light matching the reference.', max_length=800)
    key_color: str = Field(default='#ffe9cc', pattern=r'^#[a-fA-F0-9]{6}$')
    fill_color: str = Field(default='#9585bf', pattern=r'^#[a-fA-F0-9]{6}$')
    key_position: Vec3 = Field(default_factory=lambda: Vec3(x=-4,y=-2,z=7))
    strength: float = Field(default=800, ge=50, le=2000)


class AnimationCue(StrictModel):
    clip: Literal['guard','punch_jab','punch_cross','hook','recover','hit_chest','hit_head','hit_knockback','dodge_crouch','dodge_roll','walk','jog']
    start: float = Field(ge=0, le=30)
    end: float = Field(gt=0, le=30)
    blend: float = Field(default=.1, ge=.04, le=.3)


class Interaction(StrictModel):
    time: float = Field(gt=0, le=30)
    actor: str = Field(max_length=40)
    target: str = Field(max_length=40)
    outcome: Literal['hit','dodged']
    limb: Literal['hand_l','hand_r']
    zone: Literal['head','chest'] = 'chest'


class Entity(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,39}$')
    label: str = Field(min_length=1, max_length=100)
    geometry: Literal['box', 'sphere', 'cylinder', 'mannequin', 'asset', 'train', 'building', 'sign', 'canopy']
    asset_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{16}$')
    position: Vec3
    size: Vec3 = Field(default_factory=lambda: Vec3(x=1, y=1, z=1))
    yaw: float = Field(default=0, ge=-360, le=360)
    color: str = Field(default='#4fb3cf', pattern=r'^#[a-fA-F0-9]{6}$')
    clip: Literal['idle', 'walk', 'run'] = 'idle'
    path: list[Key] = Field(default_factory=list, max_length=12)
    character: CharacterLook | None = None
    accent_color: str = Field(default='#e4b548', pattern=r'^#[a-fA-F0-9]{6}$')
    rig: Literal['procedural-v1', 'quaternius-v1'] = 'procedural-v1'
    facing: str | None = Field(default=None, max_length=40)
    actions: list[AnimationCue] = Field(default_factory=list, max_length=32)

    @model_validator(mode='after')
    def geometry_contract(self):
        if min(self.size.xyz()) <= 0:
            raise ValueError('Entity size must be positive')
        if self.geometry == 'asset' and not self.asset_id:
            raise ValueError('Select an existing asset from the library')
        if self.geometry != 'mannequin' and self.clip != 'idle':
            raise ValueError('Only the reusable mannequin rig supports motion clips')
        if self.geometry == 'mannequin' and len(set(self.size.xyz())) != 1:
            raise ValueError('The reusable mannequin uses one uniform scale')
        if self.clip in {'walk', 'run'} and len(self.path) < 2:
            raise ValueError('Locomotion requires an explicit travel path')
        if (self.actions or self.rig == 'quaternius-v1') and self.geometry != 'mannequin':
            raise ValueError('Authored animation requires a character rig')
        if self.actions and self.rig != 'quaternius-v1':
            raise ValueError('Select the compatible native Quaternius rig for authored clips')
        if self.rig == 'quaternius-v1' and (not self.actions or self.clip != 'idle'):
            raise ValueError('Native rigs require an explicit action timeline and clip=idle')
        return self


class SceneCamera(StrictModel):
    mode: Literal['follow', 'orbit', 'track', 'static']
    target: str = Field(max_length=40)
    distance: float = Field(default=4, ge=1.5, le=20)
    height: float = Field(default=2.2, ge=.5, le=12)
    aim_height: float = Field(default=1.1, ge=0, le=6)
    lens: float = Field(default=35, ge=18, le=85)
    damping: float = Field(default=.2, ge=0, le=1)
    orbit_start: float = Field(default=180, ge=-720, le=720)
    orbit_end: float = Field(default=270, ge=-720, le=720)
    path: list[Key] = Field(default_factory=list, max_length=12)
    include: list[str] = Field(default_factory=list, max_length=4)


class SceneSpec(StrictModel):
    version: Literal['1'] = '1'
    description: str = Field(min_length=1, max_length=1200)
    reconstruction: Literal['editable-approximation'] = 'editable-approximation'
    reference_analysis: str = Field(min_length=1, max_length=1600)
    ground_color: str = Field(default='#18293b', pattern=r'^#[a-fA-F0-9]{6}$')
    sky_color: str = Field(default='#17243b', pattern=r'^#[a-fA-F0-9]{6}$')
    entities: list[Entity] = Field(min_length=1, max_length=24)
    camera: SceneCamera
    lighting: Lighting = Field(default_factory=Lighting)
    interactions: list[Interaction] = Field(default_factory=list, max_length=24)
    limitations: list[str] = Field(default_factory=lambda: ['Editable approximate geometry, not exact single-image reconstruction.', 'The mannequin uses reusable stylized clips, not realistic human motion or automatic rigging.'], max_length=10)

    def validate_timeline(self, duration: float):
        ids = [e.id for e in self.entities]
        if len(set(ids)) != len(ids) or self.camera.target not in ids:
            raise ValueError('Scene IDs must be unique and camera target must exist')
        if sum(e.geometry == 'mannequin' for e in self.entities) > 4:
            raise ValueError('Preview supports up to four reusable characters')
        for path in [e.path for e in self.entities] + [self.camera.path]:
            if path and (len(path) < 2 or path[0].time != 0 or abs(path[-1].time-duration) > .01 or any(a.time >= b.time for a,b in zip(path,path[1:]))):
                raise ValueError('Paths must cover the duration with increasing keyframe times')
        for e in self.entities:
            if e.facing and (e.facing not in ids or e.facing == e.id):
                raise ValueError('Character facing target must name a different scene entity')
            for i, cue in enumerate(e.actions):
                if cue.end > duration or cue.end <= cue.start or (cue.clip!='guard' and cue.end-cue.start < 2*cue.blend):
                    raise ValueError('Action duration must fit the scene and both blend transitions')
                if i and cue.start < e.actions[i-1].end:
                    raise ValueError('Character actions must be ordered and cannot overlap')
                source_duration = CLIPS[cue.clip][3]
                if CLIPS[cue.clip][4] in {'attack','reaction'} and source_duration and not .6 <= (cue.end-cue.start)/source_duration <= 1.8:
                    raise ValueError('Attack/reaction playback must retain plausible source speed')
            if e.path and math.dist(e.position.xyz(), e.path[0].position.xyz()) > .01:
                raise ValueError('Initial transform must match first path keyframe')
            if e.clip in {'walk','run'} and sum(math.dist(a.position.xyz(), b.position.xyz()) for a,b in zip(e.path,e.path[1:])) < .1:
                raise ValueError('Locomotion needs a moving path')
        if self.camera.mode in {'track','static'} and not self.camera.path:
            raise ValueError('Tracking/static cameras need explicit positions')
        if self.camera.mode == 'static' and len({tuple(k.position.xyz()) for k in self.camera.path}) != 1:
            raise ValueError('A static camera must keep one position')
        if any(i not in ids for i in self.camera.include):
            raise ValueError('Every camera framing subject must exist')
        entities = {e.id:e for e in self.entities}
        from .blender.paths import sample
        for beat in self.interactions:
            if beat.actor == beat.target or beat.actor not in ids or beat.target not in ids or beat.time > duration:
                raise ValueError('Interaction needs two distinct existing characters and valid time')
            actor, target = entities[beat.actor], entities[beat.target]
            if any(e.rig != 'quaternius-v1' for e in (actor,target)):
                raise ValueError('Interactions require compatible authored skeletal clips')
            attack = next((a for a in actor.actions if a.start < beat.time < a.end and CLIPS[a.clip][4]=='attack'),None)
            reaction = next((a for a in target.actions if a.start <= beat.time+.12 and a.end > beat.time and CLIPS[a.clip][4]==('reaction' if beat.outcome=='hit' else 'dodge')),None)
            if not attack or not reaction:
                raise ValueError('Every contact must synchronize an attack with a valid reaction/dodge')
            distance = math.dist(sample([p.model_dump() for p in actor.path],beat.time,actor.position.xyz()),sample([p.model_dump() for p in target.path],beat.time,target.position.xyz()))
            if not .55 <= distance <= 1.5:
                raise ValueError('Interaction spacing is outside plausible unarmed reach')
            if not {beat.actor,beat.target}.issubset({self.camera.target,*self.camera.include}):
                raise ValueError('Fight camera must frame both characters')
        return self
