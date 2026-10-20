"""Observable usage, zero-call reuse, immutable appearance and physical cameras."""
import asyncio
import copy
import json
import math
from pathlib import Path

import pytest

from test_motion_director import studio,upload
from test_motion_scene import blockout
from test_motion_quality import direction
from motion_director import routes
from motion_director.planner import LocalDirector,ShotRequest
from motion_director.editing import camera_revision
from gateway.motion_director.usage import observe,record
from gateway.motion_director import scene_host
from gateway.motion_director.blender.camera import FollowRig
from gateway.motion_director.blender.paths import sample


def test_cli_usage_preserves_unknowns_and_does_not_treat_turns_as_provider_calls(tmp_path,monkeypatch):
    events=[{'type':'assistant','message':{'id':'same','content':'private'}},
            {'type':'assistant','message':{'id':'same'}},
            {'type':'result','num_turns':2,'account':'private-account','total_cost_usd':99,
             'usage':{'input_tokens':2,'cache_creation_input_tokens':7932,'output_tokens':3519}}]
    result,usage=observe('\n'.join(map(json.dumps,events)),'our-invocation',35)
    assert usage.input_tokens==2 and usage.cache_creation_input_tokens==7932 and usage.output_tokens==3519
    assert usage.cache_read_input_tokens is None and usage.provider_requests is None
    assert usage.assistant_messages==1 and usage.reported_turns==2
    assert usage.observed_retry_events is None and usage.application_retries==0
    monkeypatch.setenv('NEONFORGE_MOTION_USAGE_LOG',str(tmp_path/'usage.jsonl'));record(usage)
    persisted=(tmp_path/'usage.jsonl').read_text()
    assert 'private' not in persisted and 'cost_usd' not in persisted
    unknown=observe('{"type":"result"}','missing',1)[1]
    assert unknown.input_tokens is None and unknown.output_tokens is None


def test_camera_only_edit_preserves_appearance_geometry_and_updates_final_direction():
    shot=LocalDirector().plan(ShotRequest(prompt='Run and follow',reference_ids=['r'],duration=10))
    shot.scene=blockout();shot.h3=direction();shot.h3.detailed_description='The camera is 4 m behind the runner. His cream tunic stays the same.'
    before=copy.deepcopy(shot.scene)
    changed=camera_revision(shot,'Move the camera to 6 metres behind the runner. Set the camera height to 2 metres.')
    assert changed.scene['entities']==before['entities'] and changed.scene['lighting']==before['lighting']
    assert changed.scene['camera']['distance']==6 and '4 m behind' not in changed.renderer_prompt
    assert changed.h3.subject_definitions==shot.h3.subject_definitions
    assert changed.planning_usage.new_claude_invocations==0
    assert camera_revision(shot,'Move the camera to 6 metres behind the runner and change his coat to red.') is None


def test_seed_quality_and_camera_revisions_reuse_the_scene_without_another_opus_call(studio,monkeypatch):
    client,_=studio;ref=upload(client);calls=[]
    async def host(runtime,route,payload=None):
        assert route=='claude/plan';calls.append(route)
        shot=LocalDirector().plan(ShotRequest.model_validate(payload['shot']));shot.scene=blockout();shot.h3=direction();return shot.model_dump()
    monkeypatch.setattr(routes,'host_request',host)
    request={'prompt':'Run and follow the character','reference_ids':[ref],'duration':10,'pipeline':'3d','director':'opus'}
    first=client.post('/api/v1/motion-director/plan',json=request).json()
    cached=client.post('/api/v1/motion-director/plan',json=request).json()
    assert cached['request']['plan_id']==first['request']['plan_id']
    assert cached['planning_usage']['new_claude_invocations']==0
    second=client.post('/api/v1/motion-director/plan',json=request|{'quality':'native','seed':43}).json()
    assert second['width']==768 and second['height']==1344
    assert second['scene']==first['scene'] and second['request']['plan_id']!=first['request']['plan_id']
    revised=client.post('/api/v1/motion-director/plans/'+second['request']['plan_id']+'/revise',json={'instruction':'Make the camera smoother.'}).json()
    assert revised['scene']['camera']['damping']==.35 and revised['scene']['entities']==first['scene']['entities']
    assert revised['planning_usage']['new_claude_invocations']==0 and len(calls)==1


def test_preview_cache_uses_content_and_renderer_not_generation_quality(tmp_path):
    asset=tmp_path/'prop.glb';asset.write_bytes(b'asset-one')
    data={'scene':blockout(),'duration':10,'width':384,'height':672,'generation_width':384,'assets':{'prop':'assets/prop.glb'}}
    one=scene_host.render_key(data,{'prop':asset})
    assert scene_host.render_key(data|{'generation_width':768},{'prop':asset})==one
    asset.write_bytes(b'asset-two')
    assert scene_host.render_key(data,{'prop':asset})!=one


def test_damped_follow_retains_distance_during_constant_speed_and_explicit_paths():
    spec={'distance':3.6,'height':1.5,'aim_height':1.2,'damping':.2}
    rig=FollowRig(spec)
    for i in range(240):
        target=(0,i/8,0);position,aim=rig.step(target,(0,3,0),math.pi,1/24)
        assert math.hypot(position[0]-target[0],position[1]-target[1])==pytest.approx(3.6)
    position,_=rig.step((0,30,0),(0,3,0),math.pi,1/24,explicit=(2,3,4))
    assert position==(2,3,4)


def test_heading_step_is_damped_and_world_path_does_not_overshoot_or_move_during_stop():
    rig=FollowRig({'distance':4,'height':2,'aim_height':1,'damping':.2})
    rig.step((0,0,0),(0,0,0),0,1/24)
    position,_=rig.step((0,0,0),(0,0,0),math.pi/2,1/24)
    assert -1<position[0]<0 # direct heading would jump to x=-4
    path=[{'time':t,'position':{'x':x,'y':y,'z':0}} for t,x,y in [(0,0,0),(2,0,6),(4,5,11),(6,5,17),(8,5,17),(10,5,17)]]
    for i in range(241):
        p=sample(path,i/24,(0,0,0))
        assert 0<=p[0]<=5 and 0<=p[1]<=17
        if i>=144:assert p==(5,17,0)
