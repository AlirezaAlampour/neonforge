"""Real A/B/C/D acceptance using existing authenticated GPU coordination.

Run with verified uv.lock. No model download, API key, service installation or
automatic retry. --release-qwen is an explicit opt-in for the owner's already
approved temporary unload/restore; all other resident models remain blockers.
"""
import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from gateway.motion_director import media, scene_host
from gateway.motion_director.planner import ShotSpec, segment_shot
from gateway.motion_director.renderers import H3Renderer
from shared.host_auth import signed_headers
from shared.motion_quality import wait_native_cooldown
from supervisor.resource_manager import workload_minimum_gb

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.motion-runtime/quality'
BASE='http://127.0.0.1:8189'


def memory():
    return next(int(l.split()[1])/1048576 for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:'))


async def host(route,payload=None):
    async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds='/run/user/1000/neonforge-motion/control.sock'),timeout=180) as client:
        req=client.build_request('GET' if payload is None else 'POST','http://localhost/'+route,json=payload)
        req.headers.update(signed_headers(str(ROOT/'.motion-runtime/supervisor.key'),'supervisor',req.method,req.url.path,req.content))
        response=await client.send(req);response.raise_for_status();return response.json()


async def gpu_sample():
    process=await asyncio.create_subprocess_exec('nvidia-smi','--query-gpu=utilization.gpu,temperature.gpu,clocks.gr,power.draw','--format=csv,noheader,nounits',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    out,_=await process.communicate();values=[float(v) for v in out.decode().strip().split(',')]
    return dict(zip(['gpu_percent','temperature_c','clock_mhz','power_w'],values))


async def render_case(name,shot,preview,folder,assets,resume=None):
    # Policy is checked with the real canvas; never declare a draft while
    # actually submitting a larger graph. The host owns the exclusive claim.
    minimum=workload_minimum_gb('h3','motion-h3',frames=124,width=shot.width,height=shot.height)
    if memory()<minimum:await host('h3/reclaim',{})
    if memory()<minimum:raise RuntimeError(f'{name}: requires {minimum} GiB; {memory():.1f} available')
    claim=str(uuid.uuid4());record=resume or {'case':name,'canvas':[shot.width,shot.height],'quality':shot.request.quality,'minimum_admission_gib':minimum,'before_gib':memory(),'clips':[],'samples':[]}
    prior_elapsed=record['samples'][-1]['elapsed'] if resume else 0
    record.update(claim=claim,status='running')
    record.pop('error',None)
    statusfile=folder/f'{name}-benchmark.json';statusfile.write_text(json.dumps(record,indent=2))
    await host('gpu/acquire',{'claim_id':claim,'service':'h3','minimum_gb':minimum})
    terminal=True;uncertain=False;start=time.monotonic();outputs=[folder/c['trimmed'] for c in record['clips']]
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            runtime=SimpleNamespace(H3_COMFYUI_URL=BASE,http_client=client,COMFYUI_UPLOADS_DIR=Path('/srv/ai/assets/comfyui/uploads'),
                resolve_asset_path=lambda relative,root:root/relative,resolve_output_path=lambda relative:folder/relative)
            renderer=H3Renderer(runtime)
            for i in range(len(outputs),2):
                if i:
                    await host('h3/free-idle',{'claim_id':claim,'service':'h3','minimum_gb':minimum})
                    if memory()<minimum:raise RuntimeError('Next clip did not recover its native admission headroom')
                if shot.request.quality=='native':
                    async def read_gpu():
                        sample=await gpu_sample()
                        return {**sample,'utilization_pct':sample['gpu_percent']}
                    waited=await wait_native_cooldown(read_gpu)
                    record.setdefault('cooling_waits',[]).append({'clip':i,'seconds':waited,'maximum_start_c':50})
                bounded=segment_shot(shot,i)
                window=await media.scene_window(preview,folder/name,i,shot.width,shot.height,shot.request.quality)
                context=await media.continuation(outputs[-1],folder/name,i,shot.width,shot.height) if outputs else None
                record['clips'].append({'index':i,'seed':bounded.request.seed,'admission_gib':memory(),'motion':str(window.relative_to(folder)),'intent':'submitting'})
                statusfile.write_text(json.dumps(record,indent=2));uncertain=True
                prompt=await renderer.submit(bounded,assets,f'{name}-{claim}-{i}',context,scene_path=window)
                uncertain=False;terminal=False;record['clips'][-1].update(prompt_id=prompt,intent='accepted')
                statusfile.write_text(json.dumps(record,indent=2));print(name,'clip',i+1,'accepted',prompt,flush=True)
                while True:
                    sample={'elapsed':round(prior_elapsed+time.monotonic()-start,2),'available_gib':round(memory(),3),**await gpu_sample()};record['samples'].append(sample)
                    if sample['clock_mhz']>2000:raise RuntimeError('Existing GPU cap exceeded; reservation retained for inspection')
                    if sample['available_gib']<40 or sample['temperature_c']>=70:
                        queue=await renderer.queue()
                        running=[r[1] for r in queue.get('queue_running',[])]
                        if running==[prompt]:
                            await client.post(BASE+'/interrupt',json={})
                            print('Stopped only our exclusive benchmark at its resource guard',sample,flush=True)
                    entry=await renderer.history(prompt)
                    if entry:
                        state=entry.get('status',{})
                        if state.get('status_str')=='error':
                            terminal=True;raise RuntimeError(name+': H3 execution failed; no retry')
                        if state.get('completed') or state.get('status_str')=='success':
                            terminal=True
                            (folder/f'{name}-clip{i}-history.json').write_text(json.dumps(entry,indent=2))
                            raw=await renderer.retrieve(entry,f'{name}-{claim}-{i}')
                            target=folder/name/f'clip-{i}.mp4'
                            await media.trim(folder/raw,target,5,bool(i),quality=shot.request.quality)
                            outputs.append(target);record['clips'][-1].update(status='completed',raw=raw,trimmed=str(target.relative_to(folder)))
                            break
                    statusfile.write_text(json.dumps(record,indent=2));await asyncio.sleep(1)
            await media.assemble(outputs,folder/f'{name}.mp4',10)
            record.update(status='completed',runtime_seconds=round(prior_elapsed+time.monotonic()-start,2),minimum_available_gib=min(s['available_gib'] for s in record['samples']),peak_temperature_c=max(s['temperature_c'] for s in record['samples']),max_clock_mhz=max(s['clock_mhz'] for s in record['samples']))
            statusfile.write_text(json.dumps(record,indent=2));print(name,'COMPLETE',record['runtime_seconds'],'seconds, min',record['minimum_available_gib'],'GiB',flush=True)
    except Exception as exc:
        record.update(status='failed',error=str(exc)[:300],partial_elapsed_seconds=round(prior_elapsed+time.monotonic()-start,2))
        statusfile.write_text(json.dumps(record,indent=2));raise
    finally:
        if terminal and not uncertain:
            await host('h3/free-idle',{'claim_id':claim,'service':'h3','minimum_gb':minimum})
            await host('gpu/release',{'claim_id':claim,'service':'h3','minimum_gb':minimum})
        else:
            print('Reservation retained: an accepted or uncertain H3 prompt needs inspection. No retry.',flush=True)


async def main(args):
    baseline=json.loads((STATE/'baseline-job.json').read_text());old=ShotSpec.model_validate(baseline['shot'])
    optimized=ShotSpec.model_validate_json((STATE/'optimized-opus-plan.json').read_text())
    manifest=STATE/'comparison-manifest.json'
    if manifest.exists():data=json.loads(manifest.read_text());folder=Path(data['folder'])
    else:
        sid=str(uuid.uuid4());folder=scene_host.folder_for(sid);folder.mkdir(parents=True,exist_ok=True)
        data={'id':sid,'folder':str(folder),'baseline_job':baseline['job_id'],'reference_ids':old.reference_ids,'seed':42,'cases':{}}
        manifest.write_text(json.dumps(data,indent=2))
    shutil.copy2(Path('/srv/ai/outputs')/baseline['result_path'],folder/'A.mp4')
    old_preview=scene_host.folder_for(old.request.plan_id)/'preview.mp4';shutil.copy2(old_preview,folder/'A-preview.mp4')
    # Render the improved scene at native resolution once; motion references
    # remain bounded. This does not upscale an old Blender or H3 video.
    scene_record=STATE/'improved-preview.json'
    if scene_record.exists():scene_id=json.loads(scene_record.read_text())['id']
    else:
        scene_id=str(uuid.uuid4());scene_record.write_text(json.dumps({'id':scene_id}))
    if scene_host.read_status(scene_id).get('status')!='completed':
        scene_host.start(scene_id,optimized.scene,10,768,1344,optimized.renderer_prompt)
        await scene_host.tasks[scene_id]
        if scene_host.read_status(scene_id)['status']!='completed':raise RuntimeError(scene_host.read_status(scene_id))
    improved=scene_host.folder_for(scene_id)/'preview.mp4';shutil.copy2(improved,folder/'C-preview.mp4')
    print('Native Blender scene ready:',scene_id,flush=True)
    if args.preview_only:return
    connection=sqlite3.connect('file:/srv/ai/outputs/neonforge_history.sqlite3?mode=ro',uri=True);connection.row_factory=sqlite3.Row
    assets=[SimpleNamespace(**dict(connection.execute('SELECT * FROM comfyui_assets WHERE id=?',(r,)).fetchone())) for r in old.reference_ids]
    qwen=None
    async with httpx.AsyncClient(timeout=180) as client:
        resident=(await client.get('http://100.103.129.82:11434/api/ps')).json()['models']
        if resident:
            if not args.release_qwen or [m['name'] for m in resident]!=['qwen3.6:35b-a3b']:raise RuntimeError('Resident models preserved; explicit approved Qwen release is needed')
            qwen=resident[0];(STATE/'qwen-before-quality.json').write_text(json.dumps(qwen,indent=2))
            response=await client.post('http://100.103.129.82:11434/api/generate',json={'model':qwen['name'],'keep_alive':0});response.raise_for_status()
            for _ in range(60):
                if not (await client.get('http://100.103.129.82:11434/api/ps')).json()['models']:break
                await asyncio.sleep(.5)
        try:
            cases={'B':(old.model_copy(update={'h3':optimized.h3,'renderer_prompt':optimized.renderer_prompt}),old_preview),
                   'C':(optimized,improved),
                   'D':(optimized.model_copy(update={'request':optimized.request.model_copy(update={'quality':'native'}),'width':768,'height':1344}),improved)}
            for name,(shot,preview) in cases.items():
                if args.only and name!=args.only:continue
                record=folder/f'{name}-benchmark.json'
                resume=None
                if record.exists():
                    existing=json.loads(record.read_text())
                    if existing.get('status')=='completed':continue
                    if any(c.get('prompt_id') or c.get('intent')=='submitting' for c in existing.get('clips',[])):
                        if not args.resume_verified_interruption or args.only!=name:
                            raise RuntimeError('Earlier accepted/submitting prompt requires inspection; no automatic resubmission')
                        queue=(await client.get(BASE+'/queue')).json()
                        last=existing['clips'][-1];prompt=last.get('prompt_id')
                        history=(await client.get(BASE+'/history/'+str(prompt))).json().get(prompt,{})
                        status=history.get('status',{})
                        if (queue.get('queue_running') or queue.get('queue_pending') or status.get('status_str')!='error'
                            or not any(m[0]=='execution_interrupted' for m in status.get('messages',[]))
                            or any(c.get('status')!='completed' or not (folder/c['trimmed']).is_file() for c in existing['clips'][:-1])):
                            raise RuntimeError('Only a verified terminal interruption with saved completed predecessors can resume')
                        shutil.copy2(record,folder/f'{name}-interrupted-{prompt}.json')
                        existing['clips'].pop();existing.setdefault('interrupted_attempts',[]).append(last)
                        existing['runtime_note']='Includes prior sampled active time and interrupted attempt; excludes offline cooling/build pause.'
                        resume=existing
                        print('Explicitly resuming only verified interrupted clip',last['index']+1,prompt,flush=True)
                (folder/f'{name}-plan.json').write_text(shot.model_dump_json(indent=2))
                await render_case(name,shot,preview,folder,assets,resume)
                data['cases'][name]={'clip':f'{name}.mp4','record':f'{name}-benchmark.json'};manifest.write_text(json.dumps(data,indent=2))
        finally:
            # Release is never attempted against somebody else's active queue.
            queues=(await client.get(BASE+'/queue')).json()
            if qwen and not queues.get('queue_running') and not queues.get('queue_pending'):
                if memory()<90:await host('h3/reclaim',{})
                response=await client.post('http://100.103.129.82:11434/api/generate',json={'model':qwen['name'],'keep_alive':-1,'stream':False,'options':{'num_ctx':qwen.get('context_length',262144)}});response.raise_for_status()
                print('Restored original Qwen residency/context',flush=True)
            elif qwen:print('Qwen restoration deferred while an H3 prompt remains active; do not admit competing loads',flush=True)
    print('Comparison artifact folder:',folder,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--release-qwen',action='store_true');parser.add_argument('--preview-only',action='store_true');parser.add_argument('--only',choices=['B','C','D']);parser.add_argument('--resume-verified-interruption',action='store_true');asyncio.run(main(parser.parse_args()))
