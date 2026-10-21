"""Persist the explicitly authored acceptance fixture after a real Blender preview.

This adds one immutable plan, never alters previous media/plans and never invokes
Claude or H3. It is not an automatic planning fallback. Uses the existing image
chosen for the prior train-platform fight test. Run with uv and verified uv.lock.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from gateway.motion_director.choreography import fight_study
from gateway.motion_director.planner import ShotRequest, LocalDirector, H3Direction, Beat
from gateway.motion_director.usage import PlanningUsage


def main():
    root=Path('.motion-runtime/daidyra')
    sid=(root/'study-id.txt').read_text().strip()
    folder=Path('.motion-runtime/scene-outputs/motion_director/scenes')/sid
    state=json.loads((folder/'status.json').read_text())
    assert state['status']=='completed' and state.get('timeline_verified')
    assert json.loads((folder/'timeline-validation.json').read_text())['valid']
    spec=fight_study().model_dump()
    assert json.loads((folder/'scene.json').read_text())['scene']==spec
    request=ShotRequest(prompt='the characters start fighting',reference_ids=['ded2ec85-1150-4aa4-b221-971f831cd60e'],duration=5,
                        orientation='vertical',quality='draft',director='local',pipeline='3d',seed=42)
    shot=LocalDirector().plan(request);shot.request.plan_id=sid
    shot.scene=spec;shot.planner='authored-fight-study';shot.production_version='iteration-v1';shot.conditioning='reference-video'
    shot.planning_usage=PlanningUsage(source='local',new_claude_invocations=0,elapsed_seconds=0)
    shot.h3=H3Direction(summary='Human and alien exchange punches, react, dodge, and recover in one continuous shot.',
        subject_definitions=['<Subject 1> is the young human in ivory tunic, gold winged headpiece and sandals, brown satchel from <Picture 1>, on the left.',
                             '<Subject 2> is the grey-skinned alien in the dark striped tracksuit and headphones from <Picture 1>, on the right.'],
        retention_analysis=['Retain both original faces, anatomy, clothing, accessories, wet neon train platform, train on the right and distant city. The winged cat stays above.',
                            '<Video 1> supplies only two-character movement, contact timing, relative layout and slow lateral camera. Exclude its simplified body surfaces and materials.'],
        detailed_description='One continuous medium-wide 30mm shot keeps both fighters fully visible and on stable screen sides. They step into range. At 1.3s the human jabs and the alien recoils; at 2.68s the alien counters and the human recoils. At 3.95s the human throws a cross while the alien ducks under it. Both recover to guard. Preserve body proportions and safe spacing. The camera tracks slowly sideways without cuts; the train and platform stay fixed.')
    shot.renderer_prompt=shot.h3.compile()
    shot.notes=['Explicit local authored acceptance study; no Claude planning invocation. Review the actual validated Blender preview before H3.']
    shot.camera.movement='A slow 0.05m/s lateral track, framing both fighters throughout.'
    shot.timing=[{'start':0,'end':.9,'action':'Two subjects step into guard.'},{'start':.9,'end':1.8,'action':'Human jabs; alien head reaction at 1.3s.'},{'start':1.8,'end':3.3,'action':'Alien counters; human chest reaction at 2.68s.'},{'start':3.3,'end':5,'action':'Human crosses; alien crouch dodge at 3.95s; recover.'}]
    shot.timing=[Beat.model_validate(beat) for beat in shot.timing]
    data=request.model_dump(exclude={'plan_id','quality'})
    key=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    # SQLite transaction inserts only this independently reviewed fixture ID.
    with sqlite3.connect('/srv/ai/outputs/neonforge_history.sqlite3') as db:
        existing=db.execute('SELECT shot_json FROM motion_director_plans WHERE id=?',(sid,)).fetchone()
        if existing:assert json.loads(existing[0])==shot.model_dump(mode='json')
        else:db.execute('INSERT INTO motion_director_plans (id,request_hash,shot_json,created_at) VALUES (?,?,?,?)',
                        (sid,key,shot.model_dump_json(),datetime.now(timezone.utc).isoformat()))
    (root/'study-plan.json').write_text(shot.model_dump_json(indent=2))
    print('Prepared reviewed authored study',sid,'(no Claude or H3 invocation)')


if __name__=='__main__':main()
