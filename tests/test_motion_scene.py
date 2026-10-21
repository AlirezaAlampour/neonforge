"""3D admission, source conditioning, revisions and safe host capabilities."""
import asyncio
import copy
import json
import math
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from test_motion_director import studio, upload, gateway
from motion_director import routes
from motion_director.planner import ShotRequest, LocalDirector
from motion_director.scene import SceneSpec
from motion_director.renderers import build_h3_graph
from gateway.motion_director import scene_host, claude_host
from shared.host_auth import Verifier, signed_headers


def blockout(duration=10):
    return SceneSpec.model_validate({'description':'Running character with independently following camera',
        'reference_analysis':'Approximate floor and obstacle depth from a reference image, not exact reconstruction.',
        'entities':[{'id':'runner','label':'Runner','geometry':'mannequin','position':{'x':0,'y':0,'z':0},'clip':'run',
                     'path':[{'time':0,'position':{'x':0,'y':0,'z':0}},{'time':duration,'position':{'x':0,'y':duration*3,'z':0}}]},
                    {'id':'prop','label':'Fixed environment','geometry':'box','position':{'x':3,'y':7,'z':1}}],
        'camera':{'mode':'follow','target':'runner','distance':4,'height':2}}).model_dump()


@pytest.mark.parametrize('change', ['missing-target','duplicate-id','wrong-end','zero-travel','non-rig-clip','invalid-asset','reverse-time'])
def test_scene_validation_rejects_incoherent_or_unsafe_geometry(change):
    data=blockout()
    if change=='missing-target': data['camera']['target']='absent'
    if change=='duplicate-id': data['entities'][1]['id']='runner'
    if change=='wrong-end': data['entities'][0]['path'][-1]['time']=9
    if change=='zero-travel': data['entities'][0]['path'][-1]['position']['y']=0
    if change=='non-rig-clip': data['entities'][1]['clip']='run'
    if change=='invalid-asset': data['entities'][1].update(geometry='asset',asset_id='../escape')
    if change=='reverse-time': data['entities'][0]['path'][0]['time']=10
    with pytest.raises(ValueError): SceneSpec.model_validate(data).validate_timeline(10)


def test_real_video_conditioning_replaces_context_without_dropping_boundary():
    shot=LocalDirector().plan(ShotRequest(prompt='Run with camera behind',reference_ids=['r'],duration=5))
    shot.scene=blockout(5)
    graph=build_h3_graph(shot,['identity.png'],'job',{'image':'boundary.png','video':'context.mp4'},'rendered-blender.mp4')
    assert graph['136']['inputs']['ref_videos.ref_video_0']==['blender_frames',0]
    assert graph['blender_video']['inputs']['file']=='rendered-blender.mp4'
    assert graph['136']['inputs']['ref_images.ref_image_0']==['scale_0',0]
    assert graph['126']['inputs']['conditioning']==['boundary_guide',0]
    assert 'context_video' not in graph and 'context_frames' not in graph
    assert '<Video 1>' in graph['138']['inputs']['value'] and '22 frames' in graph['138']['inputs']['value']


def test_3d_requires_valid_opus_plan_and_completed_preview_before_gpu_admission(studio,monkeypatch):
    client,fake=studio;ref=upload(client);calls=[]
    async def host(runtime,route,payload=None):
        calls.append((route,payload))
        if route=='claude/plan':
            shot=LocalDirector().plan(ShotRequest.model_validate(payload['shot']));shot.scene=blockout(10);shot.planner='claude-code-opus'
            return shot.model_dump()
        if route=='scene/start': return {'id':payload['id'],'status':'queued'}
        if route=='scene/status':return {'id':payload['id'],'status':'rendering'}
        raise AssertionError(route)
    monkeypatch.setattr(routes,'host_request',host)
    body={'prompt':'Run through the scene','reference_ids':[ref],'duration':10,'pipeline':'3d','director':'opus'}
    assert client.post('/api/v1/motion-director/jobs',json=body).status_code==409
    assert not fake.claims
    shot=client.post('/api/v1/motion-director/plan',json=body).json();sid=shot['request']['plan_id']
    assert shot['scene'] and shot['planner']=='claude-code-opus'
    assert client.post(f'/api/v1/motion-director/plans/{sid}/preview').status_code==202
    assert client.post('/api/v1/motion-director/jobs',json=shot['request']).status_code==409
    assert not fake.claims and not fake.submissions
    revised=client.post(f'/api/v1/motion-director/plans/{sid}/revise',json={'instruction':'Move the camera to 6 metres behind'}).json()
    assert revised['request']['parent_plan_id']==sid
    assert calls[-1][0]=='claude/plan' and calls[-1][1]['previous_scene']==shot['scene']
    # Original plan is immutable; revisions get a new plan/preview identity.
    assert revised['request']['plan_id'] != sid


def test_unavailable_opus_does_not_fake_a_3d_scene(studio,monkeypatch):
    client,fake=studio;ref=upload(client)
    async def host(*args,**kwargs): raise RuntimeError('No subscription')
    monkeypatch.setattr(routes,'host_request',host)
    response=client.post('/api/v1/motion-director/plan',json={'prompt':'run','reference_ids':[ref],'pipeline':'3d','director':'opus'})
    assert response.status_code==503 and not fake.submissions


def test_scene_socket_scope_cannot_launch_arbitrary_code(tmp_path):
    (tmp_path/'gateway.key').write_text('test-key')
    verifier=Verifier();body=b'{}'
    allowed=signed_headers(str(tmp_path/'gateway.key'),'gateway','POST','/scene/start',body)
    assert verifier.check(allowed,'POST','/scene/start',body,tmp_path)
    denied=signed_headers(str(tmp_path/'gateway.key'),'gateway','POST','/gpu/acquire',body)
    assert not verifier.check(denied,'POST','/gpu/acquire',body,tmp_path)
    assert not verifier.check(allowed,'POST','/scene/start',body,tmp_path) # no replay


def test_host_rejects_unknown_assets_and_shared_memory_shortage(tmp_path,monkeypatch):
    monkeypatch.setattr(scene_host,'OUTPUTS',tmp_path)
    monkeypatch.setattr(scene_host,'asset_catalog',lambda:{})
    scene=blockout();scene['entities'][1].update(geometry='asset',asset_id='a'*16)
    with pytest.raises(ValueError):scene_host.start('a'*8+'-'+ 'a'*4+'-'+ 'a'*4+'-'+ 'a'*4+'-'+ 'a'*12,scene,10,384,672,'run')


def test_subscription_status_caches_bounded_checks_and_refuses_provider_env(monkeypatch,tmp_path):
    monkeypatch.setattr(claude_host,'VERIFICATION_STATE',tmp_path/'verification.json')
    monkeypatch.setattr(claude_host,'status_cache',None);calls=[]
    async def fresh():calls.append(True);return {'available':True,'subscription':'pro','reason':''}
    monkeypatch.setattr(claude_host,'_subscription_status',fresh)
    async def scenario():
        assert (await claude_host.subscription_status())['available']
        assert (await claude_host.subscription_status())['available']
        monkeypatch.setenv('ANTHROPIC_API_KEY','test-not-a-key')
        assert not (await claude_host.subscription_status())['available']
    asyncio.run(scenario());assert len(calls)==1


@pytest.mark.parametrize('state,allowed', [({'Status':'exited','Running':False,'Pid':0},True),({'Status':'running','Running':True,'Pid':12},False),({'Status':'paused','Running':True,'Paused':True,'Pid':12},False),({},False)])
def test_offline_service_requires_authoritative_stopped_container(monkeypatch,state,allowed):
    from gateway.motion_director import host_control
    async def command(*args):
        assert args==('docker','inspect','--format','{{json .State}}','3d-generator-trellis')
        return json.dumps(state)
    monkeypatch.setattr(host_control,'command',command)
    assert asyncio.run(host_control.verified_stopped_container('3d-generator-trellis')) is allowed
    assert asyncio.run(host_control.verified_stopped_container('unrelated')) is False


def test_offline_trellis_blocks_when_container_is_running_or_unknown(monkeypatch):
    from gateway.motion_director import host_control
    coordinator=host_control.Coordinator()
    def handle(request):
        if request.url.path=='/health':return __import__('httpx').Response(200,json={'worker':{},'queue_depth':0})
        if request.url.path=='/system':return __import__('httpx').Response(503)
        return __import__('httpx').Response(200,json={'queue_running':[],'queue_pending':[]})
    async def scenario():
        await coordinator.http.aclose();coordinator.http=__import__('httpx').AsyncClient(transport=__import__('httpx').MockTransport(handle))
        async def stopped(name):return False
        monkeypatch.setattr(host_control,'verified_stopped_container',stopped)
        with pytest.raises(RuntimeError,match='not verified stopped'):await coordinator.idle_checks()
        async def stopped(name):return True
        monkeypatch.setattr(host_control,'verified_stopped_container',stopped)
        await coordinator.idle_checks()
        await coordinator.http.aclose();await coordinator.client.aclose()
    asyncio.run(scenario())


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='External FFmpeg/ffprobe required; exercised in DGX acceptance')
def test_manual_video_import_keeps_selected_blender_plan_for_comparison(studio,monkeypatch,tmp_path):
    import subprocess
    client,fake=studio;ref=upload(client)
    async def host(runtime,route,payload=None):
        shot=LocalDirector().plan(ShotRequest.model_validate(payload['shot']));shot.scene=blockout(10);shot.planner='claude-code-opus';return shot.model_dump()
    monkeypatch.setattr(routes,'host_request',host)
    shot=client.post('/api/v1/motion-director/plan',json={'prompt':'Run with rear camera','reference_ids':[ref],'duration':10,'pipeline':'3d','director':'opus'}).json()
    source=tmp_path/'manual-browser-fixture.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=navy:s=128x128:r=24','-t','1','-c:v','libx264','-threads','1',str(source)],check=True)
    with source.open('rb') as stream:
        response=client.post('/api/v1/motion-director/imports/grok',files={'file':('manual-fixture.mp4',stream,'video/mp4')},data={'reference_plan_id':shot['request']['plan_id']})
    assert response.status_code==200,response.text
    imported=response.json()
    assert imported['blender_preview_url'].endswith('/'+shot['request']['plan_id']+'/preview.mp4')
    assert imported['shot']['scene']==shot['scene']
    assert imported['shot']['planner']=='manual-import'
    assert not fake.submissions and not fake.claims


def test_capabilities_do_not_offer_idle_recovery_for_resident_unrelated_models(studio,monkeypatch):
    client,fake=studio
    monkeypatch.setattr(gateway,'HOST_CONTROL_SOCKET','/missing-test-adapter')
    monkeypatch.setattr(gateway,'get_memory_status',lambda:{'available_gb':80})
    async def host(runtime,route,payload=None):
        if route=='claude/status':return {'available':False,'reason':'fixture'}
        if route=='resources':return {'shared_gpu':{'releasable':{'items':[{'kind':'ollama_model','name':'qwen'}]}}}
        if route=='scene/capabilities':return {'available':True}
        raise AssertionError(route)
    monkeypatch.setattr(routes,'host_request',host)
    result=client.get('/api/v1/motion-director/capabilities').json()
    assert not result['h3']['render_ready'] and not result['h3']['can_recover_memory']
