"""Native resolution, reference retention, resolved intent and memory rechecks."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from test_motion_director import studio, gateway
from motion_director.planner import ShotRequest, LocalDirector, H3Direction, segment_shot
from motion_director.renderers import build_h3_graph
from motion_director import media
from supervisor.resource_manager import ResourceManager, ServicePolicy, Claim, workload_minimum_gb


def direction():
    return H3Direction(subject_definitions=['<Subject 1> is the runner in <Picture 1>, wearing the same tunic and satchel.'],
        summary='One continuous rear follow shot.',retention_analysis=['<Picture 1> supplies identity and lighting; <Video 1> supplies motion only.'],
        detailed_description='[Shot 1] The runner runs continuously; the camera follows at constant distance with the same 28mm lens. No other objects move.')


def test_resolved_context_survives_segmentation_without_obsolete_raw_directions():
    shot=LocalDirector().plan(ShotRequest(prompt='obsolete camera distance and mannequin implementation',reference_ids=['r'],duration=10))
    shot.h3=direction();shot.request.revision='obsolete revision history'
    clip=segment_shot(shot,1)
    assert clip.request.seed==43 and clip.frames==124
    assert 'subject_definitions:' in clip.renderer_prompt and '<Video 1>' in clip.renderer_prompt
    assert 'obsolete' not in clip.renderer_prompt and 'mannequin' not in clip.renderer_prompt
    assert 'Continue from the supplied boundary frame' in clip.renderer_prompt


def test_native_canvas_keeps_original_identity_and_guide_while_motion_remains_separate():
    shot=LocalDirector().plan(ShotRequest(prompt='Character running and camera following',reference_ids=['r'],duration=5,quality='native'))
    assert (shot.width,shot.height)==(768,1344)
    graph=build_h3_graph(shot,['original.png'],'job',scene_video='motion.mp4')
    assert graph['136']['inputs']['width']==768 and graph['136']['inputs']['height']==1344
    assert graph['136']['inputs']['ref_images.ref_image_0']==['scale_0',0]
    assert graph['136']['inputs']['ref_videos.ref_video_0']==['blender_frames',0]
    assert graph['scale_0']['inputs']['longer_edge']==1344
    assert graph['appearance_guide']['inputs']['image']==['ref_0',0]
    assert graph['126']['inputs']['conditioning']==['appearance_guide',0]
    assert graph['92']['inputs']['format.codec.encoding.crf']==16
    assert graph['124']['inputs']['steps']==4 # preserve installed Turbo contract
    continued=build_h3_graph(shot,['original.png'],'job',{'image':'previous.png','video':'context.mp4'},'motion.mp4')
    assert continued['126']['inputs']['conditioning']==['boundary_guide',0]
    assert 'appearance_guide' not in continued


def test_full_motion_window_is_real_time_native_grid_with_bounded_motion_pixels(tmp_path,monkeypatch):
    calls=[]
    async def command(*args,**kwargs):calls.append(args)
    monkeypatch.setattr(media,'command',command)
    asyncio.run(media.scene_window(tmp_path/'native.mp4',tmp_path,1,768,1344,'native'))
    args=calls[0]
    assert args[args.index('-ss')+1]=='5'
    assert args[args.index('-frames:v')+1]=='124'
    assert 'scale=384:672' in args[args.index('-vf')+1]
    assert 'fps=24' in args[args.index('-vf')+1] # no temporal speed-up
    assert args[args.index('-crf')+1]=='16'


def test_native_second_clip_rechecks_its_higher_admission_without_lowering_floor():
    h3=ServicePolicy('h3','',None,'http://h3','/queue','video',90,0,lifecycle_managed=False)
    memory={'available_gb':100};calls=[]
    manager=ResourceManager(compose_dir='/tmp',policies={'h3':h3},protected_services=set(),memory_reader=lambda:dict(memory),memory_wait_sec=0)
    manager._host=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={})))
    manager.claims['job']=Claim('job','h3','job',1,'native',1,110,100,minimum_available_gb=105)
    temperatures=iter([64,48])
    async def request(route,payload=None):
        calls.append((route,payload));memory['available_gb']=108
        return {'freed':True} if route=='h3/free-idle' else {'gpu':{'temperature_c':next(temperatures),'utilization_pct':0,'clock_mhz':1989}}
    manager.host_request=request
    async def run():
        await manager._http.aclose();manager._http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'queue_running':[],'queue_pending':[]})))
        result=await manager.next_h3_segment('job');assert result['minimum_available_gb']==105
        await manager.close()
    asyncio.run(run());assert calls[0][0]=='h3/free-idle' and calls[0][1]['minimum_gb']==105
    assert workload_minimum_gb('h3','motion-h3',frames=124,width=384,height=672)==90
    assert [c[0] for c in calls]==['h3/free-idle','resources','resources']


def test_native_capability_does_not_promise_readiness_at_draft_memory(studio):
    client,fake=studio
    response=client.get('/api/v1/motion-director/capabilities?quality=native')
    assert response.status_code==200
    assert response.json()['h3']['minimum_gb']==105
    assert not response.json()['h3']['render_ready']
    assert client.get('/api/v1/motion-director/capabilities?quality=unbounded').status_code==422


@pytest.mark.parametrize('prompt,revision,rejected', [
    ('Character running and camera following.', '', True),
    ('Character running and camera following.', 'Use an orbit camera instead.', False),
    ('The character follows a commuter while the camera orbits.', '', False),
])
def test_opus_follow_guard_preserves_camera_revisions_and_subject_intent(monkeypatch,tmp_path,prompt,revision,rejected):
    from gateway.motion_director import claude_host
    from test_motion_scene import blockout
    scene=blockout(5);scene['camera']['mode']='orbit'
    structured={'camera_movement':'Orbit the moving character.', 'targets':['runner'],
        'composition':'One continuous shot.', 'subject_constraints':['Preserve appearance.'],
        'timing':[{'start':0,'end':5,'action':'Run continuously.'}], 'scene':scene, 'h3':direction().model_dump()}
    async def status():return {'available':True}
    async def cli(*args,**kwargs):return {'structured_output':structured}
    monkeypatch.setattr(claude_host,'subscription_status',status)
    monkeypatch.setattr(claude_host,'run_cli',cli)
    request=claude_host.ShotRequest(prompt=prompt,reference_ids=['r'],duration=5,pipeline='3d',revision=revision)
    if rejected:
        with pytest.raises(ValueError,match='follow camera'):
            asyncio.run(claude_host.plan_with_opus(request,[],tmp_path))
    else:
        assert asyncio.run(claude_host.plan_with_opus(request,[],tmp_path)).scene['camera']['mode']=='orbit'
