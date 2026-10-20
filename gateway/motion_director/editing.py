"""Narrow, validated camera-only edits reuse appearance and all entity data."""
import re
import time

from .continuity import camera_contract
from .scene import SceneSpec
from .usage import PlanningUsage


def camera_revision(base,instruction):
    started=time.monotonic();updates={}
    parts=re.split(r'(?<=[.!?])\s+|\s*;\s*',instruction.strip().rstrip('.'))
    number=r'(\d+(?:\.\d+)?)'
    units=r'(?:m|metres?|meters?)'
    patterns={
        'distance':rf'(?:move|set) (?:the )?(?:follow )?camera (?:(?:distance )?to|farther back to) (?:exactly )?{number}\s*{units}(?: behind (?:the )?(?:runner|subject|character))?',
        'height':rf'(?:set|keep) (?:the )?camera height (?:at|to) {number}\s*{units}',
        'lens':rf'(?:set|use) (?:the )?(?:camera )?(?:lens (?:at|to) )?{number}\s*mm(?: lens)?',
        'damping':rf'(?:set|use) (?:the )?(?:camera )?damping (?:of|to) {number}\s*(?:s|seconds?)',
    }
    for part in parts:
        part=part.strip().rstrip('.').lower()
        if part in {'make the camera smoother','smooth the camera','smooth the follow camera'}:
            updates['damping']=.35;continue
        if part in {'preserve all other scene settings unchanged','keep all other scene settings unchanged',
                    'preserve the running path, animation, every object transform, and all other scene settings unchanged'}:
            continue
        matched=False
        for key,pattern in patterns.items():
            match=re.fullmatch(pattern,part)
            if match:
                updates[key]=float(match[1]);matched=True;break
        if not matched:
            return None  # mixed/ambiguous production changes still go to Opus
    if not updates or not base.scene:
        return None
    shot=base.model_copy(deep=True);scene=SceneSpec.model_validate(shot.scene)
    scene.camera=type(scene.camera).model_validate(scene.camera.model_dump()|updates)
    shot.scene=scene.validate_timeline(base.request.duration).model_dump()
    direction=camera_contract(shot.scene)
    shot.camera.movement=direction
    if shot.h3:
        sentences=re.split(r'(?<=[.!?])\s+',shot.h3.detailed_description)
        retained=' '.join(s for s in sentences if not re.search(r'\b(camera|lens|framing)\b',s,re.I))
        shot.h3.detailed_description=direction+' '+retained
        shot.h3.summary=f"One continuous {scene.camera.mode} shot, {scene.camera.lens:g} mm lens; follow distance {scene.camera.distance:g} m, camera height {scene.camera.height:g} m."
        shot.renderer_prompt=shot.h3.compile()
    shot.planning_usage=PlanningUsage(source='local-camera',new_claude_invocations=0,
        elapsed_seconds=round(time.monotonic()-started,3),original=base.planning_usage.original if base.planning_usage else None)
    return shot
