"""Failure injection at the real SQLite/outbox/H3 acknowledgement boundaries."""
import asyncio
import json
import shutil
import time
import uuid

import httpx
import pytest
from redis.exceptions import ConnectionError

from test_motion_director import studio, upload, await_job, gateway
from motion_director import routes, media
from motion_director.planner import LocalDirector, ShotRequest
from motion_director.renderers import H3Renderer


def seed_job(client, **changes):
    ref = upload(client)
    shot = LocalDirector().plan(ShotRequest(prompt='Existing scene', reference_ids=[ref]))
    job_id = str(uuid.uuid4())
    with gateway.SessionLocal() as db:
        db.add(gateway.MotionJob(id=job_id, shot_json=shot.model_dump_json(), created_at=gateway._now_utc(),
                                status='queued', message='Queued', **changes))
        db.commit()
    return job_id


def wait_status(client, job_id, status):
    for _ in range(150):
        row = client.get(f'/api/v1/motion-director/jobs/{job_id}').json()
        if row['status'] == status:
            return row
        time.sleep(.01)
    raise AssertionError(row)


def test_redis_outage_cannot_strand_submission_or_status(studio, monkeypatch):
    client, fake = studio
    class Offline:
        async def set(self, *a, **kw): raise ConnectionError('DNS unavailable')
        async def get(self, *a, **kw): raise ConnectionError('Redis stopped')
    monkeypatch.setattr(gateway, 'rdb', Offline())
    ref = upload(client)
    response = client.post('/api/v1/motion-director/jobs', json={'prompt':'scene','reference_ids':[ref]})
    assert response.status_code == 202
    job = await_job(client, response.json()['job_id'])
    assert job['status'] == 'completed' and len(fake.submissions) == 1
    assert client.get('/jobs/'+job['job_id']).json()['status'] == 'completed'


def test_idempotent_ack_replay_returns_same_job_without_h3_or_planning(studio):
    client, fake = studio
    ref = upload(client)
    body = {'prompt':'scene','reference_ids':[ref]}
    headers = {'Idempotency-Key':str(uuid.uuid4())}
    a = client.post('/api/v1/motion-director/jobs', json=body, headers=headers)
    assert a.status_code == 202
    assert await_job(client,a.json()['job_id'])['status'] == 'completed'
    fake.busy = True
    b = client.post('/api/v1/motion-director/jobs', json=body, headers=headers)
    assert b.status_code == 202 and b.json()['job_id'] == a.json()['job_id']
    assert len(fake.submissions) == 1
    c = client.post('/api/v1/motion-director/jobs', json={**body,'prompt':'changed'}, headers=headers)
    assert c.status_code == 409


def test_four_legacy_orphans_are_paused_and_individually_retryable(studio):
    client, fake = studio
    ids = [seed_job(client) for _ in range(4)]
    client.portal.call(routes.resume_motion_jobs, gateway)
    for job_id in ids:
        row = wait_status(client,job_id,'interrupted')
        assert row['can_retry'] and 'Retry' in row['message']
    assert not fake.submissions
    response = client.post(f'/api/v1/motion-director/jobs/{ids[0]}/retry')
    assert response.status_code == 202
    assert await_job(client,ids[0])['status'] == 'completed'
    assert len(fake.submissions) == 1
    assert all(client.get('/api/v1/motion-director/jobs/'+i).json()['status']=='interrupted' for i in ids[1:])


def test_committed_outbox_is_dispatched_after_interrupted_submission(studio):
    client, fake = studio
    job_id = seed_job(client, dispatch_version=1)
    client.portal.call(routes.resume_motion_jobs,gateway)
    assert await_job(client,job_id)['status']=='completed'
    assert len(fake.submissions)==1


def test_worker_restart_tracks_acknowledged_prompt_without_resubmitting(studio, monkeypatch):
    client, fake = studio
    entered=[]
    original=H3Renderer.history
    async def pause(*args):
        entered.append(True)
        await asyncio.sleep(300)
    monkeypatch.setattr(H3Renderer,'history',pause)
    ref=upload(client)
    job_id=client.post('/api/v1/motion-director/jobs',json={'prompt':'scene','reference_ids':[ref]}).json()['job_id']
    for _ in range(100):
        if entered:break
        time.sleep(.01)
    assert entered and len(fake.submissions)==1
    client.portal.call(routes.shutdown_motion_jobs)
    monkeypatch.setattr(H3Renderer,'history',original)
    client.portal.call(routes.resume_motion_jobs,gateway)
    assert wait_status(client,job_id,'completed')['preview_url']
    assert len(fake.submissions)==1 and len(fake.releases)==1


def test_lost_ack_is_recovered_by_client_id_and_never_reposted(studio):
    client,fake=studio
    ref=upload(client);fake.ambiguous=True
    job_id=client.post('/api/v1/motion-director/jobs',json={'prompt':'scene','reference_ids':[ref]}).json()['job_id']
    assert await_job(client,job_id)['status']=='needs_inspection'
    original=fake.handle
    def handler(request):
        if request.url.path=='/history':
            return httpx.Response(200,json={'test-prompt':{'prompt':[1,'test-prompt',{}, {'client_id':job_id+'-0'}]}})
        return original(request)
    async def replace():
        await gateway.http_client.aclose()
        gateway.http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.portal.call(replace)
    client.post(f'/api/v1/motion-director/jobs/{job_id}/reconcile')
    assert wait_status(client,job_id,'completed')['preview_url']
    assert len(fake.submissions)==1


def test_unknown_ack_cannot_be_retried_or_released(studio):
    client,fake=studio
    fake.ambiguous=True;ref=upload(client)
    job_id=client.post('/api/v1/motion-director/jobs',json={'prompt':'scene','reference_ids':[ref]}).json()['job_id']
    row=await_job(client,job_id)
    assert row['can_reconcile'] and not row['can_retry']
    assert client.post(f'/api/v1/motion-director/jobs/{job_id}/retry').status_code==409
    assert len(fake.submissions)==1 and not fake.releases


def test_memory_block_after_ack_is_visible_and_retry_uses_saved_plan(studio,monkeypatch):
    client,fake=studio
    job_id=seed_job(client,dispatch_version=1)
    monkeypatch.setattr(gateway,'get_memory_status',lambda:{'available_gb':80})
    client.portal.call(routes.resume_motion_jobs,gateway)
    row=await_job(client,job_id)
    assert row['status']=='blocked' and '90' in row['error'] and row['can_retry']
    assert not fake.submissions
    monkeypatch.setattr(gateway,'get_memory_status',lambda:{'available_gb':95})
    assert client.post(f'/api/v1/motion-director/jobs/{job_id}/retry').status_code==202
    assert await_job(client,job_id)['status']=='completed'
    assert len(fake.submissions)==1


def test_cancel_queued_work_never_submits(studio):
    client,fake=studio
    job_id=seed_job(client,dispatch_version=1)
    assert client.post(f'/api/v1/motion-director/jobs/{job_id}/cancel').json()['status']=='cancelled'
    client.portal.call(routes.resume_motion_jobs,gateway)
    assert not fake.submissions


def test_retrieval_failure_can_recover_without_duplicate_inference(studio,monkeypatch):
    client,fake=studio
    original=H3Renderer.retrieve
    async def fail(*args):raise OSError('Temporary output disk failure')
    monkeypatch.setattr(H3Renderer,'retrieve',fail)
    ref=upload(client)
    job_id=client.post('/api/v1/motion-director/jobs',json={'prompt':'scene','reference_ids':[ref]}).json()['job_id']
    assert await_job(client,job_id)['status']=='failed'
    assert client.post(f'/api/v1/motion-director/jobs/{job_id}/retry').status_code==409
    monkeypatch.setattr(H3Renderer,'retrieve',original)
    client.post(f'/api/v1/motion-director/jobs/{job_id}/reconcile')
    assert wait_status(client,job_id,'completed')['preview_url']
    assert len(fake.submissions)==1


def test_h3_disconnect_has_bounded_visible_recovery_and_holds_claim(studio,monkeypatch):
    client,fake=studio
    async def offline(*args):raise httpx.ConnectError('H3 restarting')
    monkeypatch.setattr(H3Renderer,'history',offline)
    monkeypatch.setattr(routes,'CONNECTION_TIMEOUT',-1)
    ref=upload(client)
    job_id=client.post('/api/v1/motion-director/jobs',json={'prompt':'scene','reference_ids':[ref]}).json()['job_id']
    row=await_job(client,job_id)
    assert row['status']=='needs_inspection' and row['can_reconcile']
    assert len(fake.submissions)==1 and not fake.releases


def test_partial_sequence_recovery_saves_accepted_clip_and_pauses_unsubmitted_tail(studio,monkeypatch):
    client,fake=studio
    ref=upload(client)
    shot=LocalDirector().plan(ShotRequest(prompt='Existing scene',reference_ids=[ref],duration=10))
    job_id=str(uuid.uuid4())
    with gateway.SessionLocal() as db:
        db.add(gateway.MotionJob(id=job_id,shot_json=shot.model_dump_json(),status='running',message='Generating',prompt_id='test-prompt',claim_id=job_id,created_at=gateway._now_utc()))
        db.add(gateway.MotionSegment(id=job_id+'-0',job_id=job_id,position=0,status='running',prompt_id='test-prompt'))
        db.commit()
    async def trim(source,target,*args,**kwargs):
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    monkeypatch.setattr(media,'trim',trim)
    client.portal.call(routes.resume_motion_jobs,gateway)
    row=wait_status(client,job_id,'interrupted')
    assert row['completed_segments']==1 and row['total_segments']==2 and row['can_retry']
    assert not fake.submissions and len(fake.releases)==1
    assert list(gateway.OUTPUTS_ROOT.glob(f'motion_director/{job_id}/clip-0.mp4'))


def test_thermal_recheck_preserves_completed_clip_and_allows_explicit_continuation(studio,monkeypatch):
    client,fake=studio
    original=fake.handle
    def handler(request):
        if request.url.path=='/recheck/h3':return httpx.Response(409,json={'detail':'Native H3 needs the idle GPU to cool to 50°C'})
        return original(request)
    async def replace():
        await gateway.http_client.aclose()
        gateway.http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.portal.call(replace)
    async def trim(source,target,*args,**kwargs):
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    async def context(previous,folder,*args):return None
    monkeypatch.setattr(media,'trim',trim);monkeypatch.setattr(media,'continuation',context)
    ref=upload(client)
    job_id=client.post('/api/v1/motion-director/jobs',json={'prompt':'scene','reference_ids':[ref],'duration':10}).json()['job_id']
    row=await_job(client,job_id)
    assert row['status']=='blocked' and '50°C' in row['error'] and row['completed_segments']==1
    assert row['can_retry'] and len(fake.submissions)==1 and len(fake.releases)==1


def test_additive_migration_preserves_legacy_rows_and_is_repeatable(studio):
    client,fake=studio
    job_id=seed_job(client)
    gateway.run_sqlite_schema_migrations();gateway.run_sqlite_schema_migrations()
    assert client.get('/api/v1/motion-director/jobs/'+job_id).json()['status']=='queued'
    assert not fake.submissions


def test_active_shared_lease_with_null_releasable_is_reported_as_blocked(studio,monkeypatch):
    client,fake=studio
    monkeypatch.setattr(gateway,'HOST_CONTROL_SOCKET','private-test-socket')
    async def host(runtime,route,payload=None):
        if route=='resources':return {'shared_gpu':{'exclusive':True,'releasable':None},'exclusive_claims':{'active':{}}}
        if route=='claude/status':return {'available':False,'reason':'subscription check deferred'}
        if route=='scene/capabilities':return {'available':True}
        if route=='grok/status':return {'available':False}
        raise AssertionError(route)
    monkeypatch.setattr(routes,'host_request',host)
    row=client.get('/api/v1/motion-director/capabilities').json()
    assert not row['h3']['render_ready'] and 'active' in row['h3']['reason']
    assert not fake.submissions
