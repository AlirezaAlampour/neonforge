"""Generate validates the server's saved plan/preview, never browser cache claims."""
import json

import pytest

from test_motion_director import studio, upload, await_job, gateway
from test_motion_scene import blockout
from motion_director import routes, media
from motion_director.planner import LocalDirector, ShotRequest

BASE='/api/v1/motion-director'


def saved_scene(client,monkeypatch,quality='draft'):
    calls=[];states={}
    async def host(runtime,route,payload=None):
        calls.append(route)
        if route=='claude/plan':
            shot=LocalDirector().plan(ShotRequest.model_validate(payload['shot']))
            shot.scene=blockout(2);return shot.model_dump()
        if route=='scene/status':return states.get(payload['id'],{'id':payload['id'],'status':'missing'})
        if route=='scene/start':
            path=gateway.resolve_output_path(f"motion_director/scenes/{payload['id']}/preview.mp4")
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'existing-preview')
            states[payload['id']]={'id':payload['id'],'status':'completed','generation_width':payload['width'],'generation_height':payload['height']}
            return states[payload['id']]
        raise AssertionError('No planning or other host calls during saved-scene generation: '+route)
    monkeypatch.setattr(routes,'host_request',host)
    async def prepared_window(*args,**kwargs):return None
    monkeypatch.setattr(media,'scene_window',prepared_window)
    request={'prompt':'A running scene','reference_ids':[upload(client)],'duration':2,'pipeline':'3d','director':'opus','quality':quality}
    response=client.post(BASE+'/plan',json=request);assert response.status_code==200,response.text
    plan=response.json();assert client.post(BASE+'/plans/'+plan['request']['plan_id']+'/preview').status_code==202
    calls.clear();return plan,calls,states


def test_saved_plan_generates_without_planning_and_duplicate_ack_is_one_job(studio,monkeypatch):
    client,fake=studio;plan,calls,_=saved_scene(client,monkeypatch)
    assert client.post(BASE+'/validate',json=plan['request']).json()['ready']
    headers={'Idempotency-Key':'one-reviewed-scene'}
    accepted=client.post(BASE+'/jobs',json=plan['request'],headers=headers)
    assert accepted.status_code==202,accepted.text
    result=await_job(client,accepted.json()['job_id']);assert result['status']=='completed'
    replay=client.post(BASE+'/jobs',json=plan['request'],headers=headers)
    assert replay.status_code==202 and replay.json()['job_id']==result['job_id']
    assert len(fake.submissions)==1 and 'claude/plan' not in calls


@pytest.mark.parametrize('problem',['canvas','schema_version','preview_missing','preview_canvas','quality_change'])
def test_one_click_rebuild_preserves_saved_data_and_never_calls_claude(studio,monkeypatch,problem):
    client,fake=studio;plan,calls,states=saved_scene(client,monkeypatch,quality='native' if problem=='canvas' else 'draft')
    ident=plan['request']['plan_id'];request=plan['request'].copy()
    with gateway.SessionLocal() as db:
        row=db.get(gateway.MotionPlan,ident);before=json.loads(row.shot_json)
        if problem=='canvas':before.update(width=384,height=672)
        if problem=='schema_version':before['production_version']=None
        row.shot_json=json.dumps(before);db.commit()
    if problem=='preview_missing':states[ident]={'id':ident,'status':'missing'}
    if problem=='preview_canvas':states[ident]['generation_width']=768
    if problem=='quality_change':request['quality']='native'
    invalid=client.post(BASE+'/validate',json=request).json()
    assert not invalid['ready'] and invalid['can_rebuild'],invalid
    rebuilt=client.post(BASE+'/plans/'+ident+'/rebuild',json=request)
    assert rebuilt.status_code==200,rebuilt.text
    updated=rebuilt.json()['plan'];new_id=updated['request']['plan_id']
    assert new_id!=ident and updated['reference_ids']==plan['reference_ids'] and updated['scene']==plan['scene']
    assert updated['planning_usage']['new_claude_invocations']==0 and 'claude/plan' not in calls
    assert client.post(BASE+'/validate',json=updated['request']).json()['ready']
    if request['quality']=='native':assert (updated['width'],updated['height'])==(768,1344)
    with gateway.SessionLocal() as db:assert json.loads(db.get(gateway.MotionPlan,ident).shot_json)==before
    assert not fake.submissions


def test_changed_direction_is_explicitly_blocked_and_rebuild_cannot_reinterpret_it(studio,monkeypatch):
    client,fake=studio;plan,calls,_=saved_scene(client,monkeypatch)
    request=plan['request']|{'prompt':'The characters fight','reference_ids':[upload(client)]}
    checked=client.post(BASE+'/validate',json=request).json()
    assert not checked['ready'] and not checked['can_rebuild']
    assert client.post(BASE+'/plans/'+plan['request']['plan_id']+'/rebuild',json=request).status_code==409
    assert client.post(BASE+'/jobs',json=request).status_code==409
    assert 'claude/plan' not in calls and not fake.submissions


def test_lost_server_preview_after_preflight_returns_failure_without_job(studio,monkeypatch):
    client,fake=studio;plan,calls,states=saved_scene(client,monkeypatch)
    assert client.post(BASE+'/validate',json=plan['request']).json()['ready']
    states[plan['request']['plan_id']]['status']='missing'
    response=client.post(BASE+'/jobs',json=plan['request'])
    assert response.status_code==409 and 'missing' in response.json()['detail']
    assert client.get(BASE+'/jobs').json()['items']==[] and not fake.submissions


def test_legacy_stored_hash_does_not_reject_unchanged_parsed_request(studio):
    client,fake=studio
    request={'prompt':'Existing classic shot','reference_ids':[upload(client)]}
    plan=client.post(BASE+'/plan',json=request).json()
    with gateway.SessionLocal() as db:
        row=db.get(gateway.MotionPlan,plan['request']['plan_id']);shot=json.loads(row.shot_json)
        for name in ['quality','pipeline','parent_plan_id','revision']:shot['request'].pop(name,None)
        row.shot_json=json.dumps(shot);row.request_hash='old-schema-hash';db.commit()
    assert client.post(BASE+'/validate',json=plan['request']).json()['ready']
    response=client.post(BASE+'/jobs',json=plan['request']);assert response.status_code==202,response.text
    assert await_job(client,response.json()['job_id'])['status']=='completed' and len(fake.submissions)==1


def test_recovery_retry_is_not_advertised_until_worker_cleanup_finishes(studio,monkeypatch):
    import asyncio
    import shutil
    import time
    import uuid
    client,fake=studio
    ref=upload(client);shot=LocalDirector().plan(ShotRequest(prompt='Accepted sequence',reference_ids=[ref],duration=10));ident=str(uuid.uuid4())
    with gateway.SessionLocal() as db:
        db.add(gateway.MotionJob(id=ident,shot_json=shot.model_dump_json(),status='running',message='Generating',prompt_id='test-prompt',claim_id=ident,created_at=gateway._now_utc()))
        db.add(gateway.MotionSegment(id=ident+'-0',job_id=ident,position=0,status='running',prompt_id='test-prompt'));db.commit()
    entered=[];release=asyncio.Event()
    async def cleanup(*args):entered.append(True);await release.wait();return True
    async def trim(source,target,*args,**kwargs):target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    monkeypatch.setattr(gateway,'release_workload',cleanup);monkeypatch.setattr(media,'trim',trim)
    client.portal.call(routes.resume_motion_jobs,gateway)
    for _ in range(100):
        if entered:break
        time.sleep(.01)
    assert entered
    row=client.get(BASE+'/jobs/'+ident).json()
    assert row['status']=='interrupted' and not row['can_retry']
    client.portal.call(release.set)
    for _ in range(100):
        row=client.get(BASE+'/jobs/'+ident).json()
        if row['can_retry']:break
        time.sleep(.01)
    assert row['can_retry'] and row['completed_segments']==1 and not fake.submissions


def test_rejected_scene_reports_the_actual_actionable_correction(studio,monkeypatch):
    client,fake=studio
    from motion_director.host_client import PlanningRefused
    async def rejected(runtime,route,payload=None):
        raise PlanningRefused('Scene plan needs correction: Fight camera must frame both characters',422)
    monkeypatch.setattr(routes,'host_request',rejected)
    result=client.post(BASE+'/plan',json={'prompt':'fight','reference_ids':[upload(client)],'pipeline':'3d','director':'opus'})
    assert result.status_code==422
    assert 'Fight camera must frame both characters' in result.json()['detail']
    assert not fake.claims and not fake.submissions
