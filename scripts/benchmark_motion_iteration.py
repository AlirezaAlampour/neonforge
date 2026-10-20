"""Matched iteration benchmark; existing models, authentication and resource gates.

Verify uv.lock first. CPU preparation freezes the preceding compiler at 5e71329.
GPU cases reuse the previous C/D plan, reference, segment seeds and inference
settings. --release-qwen requires the owner's existing unload/restore consent.
Never resubmit a previously accepted or uncertain incomplete case.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import sysconfig
import time
from types import SimpleNamespace
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.benchmark_motion_quality import BASE,ROOT,gpu_sample,host,memory,render_case
from gateway.motion_director import media,scene_host
from gateway.motion_director.planner import ShotSpec
import httpx

STATE=ROOT/'.motion-runtime/iteration'


def manifest():
    STATE.mkdir(exist_ok=True)
    path=STATE/'manifest.json'
    if path.exists():return json.loads(path.read_text())
    sid=str(uuid.uuid4());folder=scene_host.folder_for(sid);folder.mkdir(parents=True)
    value={'id':sid,'folder':str(folder),'baseline_commit':'5e71329','reference_ids':['ded2ec85-1150-4aa4-b221-971f831cd60e'],'segment_seeds':[42,43]}
    path.write_text(json.dumps(value,indent=2));return value


async def frozen_preview(folder,data):
    folder.mkdir(parents=True,exist_ok=True);compiler=folder/'blender';compiler.mkdir(exist_ok=True)
    for name in ('__init__.py','render.py','clips.py','geo.py','mannequin.py','rig.py','sets.py'):
        process=await asyncio.create_subprocess_exec('git','show','5e71329:gateway/motion_director/blender/'+name,stdout=asyncio.subprocess.PIPE)
        source,_=await process.communicate()
        if process.returncode:raise RuntimeError('Frozen baseline compiler unavailable')
        (compiler/name).write_bytes(source)
    (folder/'scene.json').write_text(json.dumps(data,indent=2))
    launcher=folder/'scene.py'
    launcher.write_text('import json,sys\nfrom pathlib import Path\nroot=Path(__file__).resolve().parent\nsys.path.insert(0,str(root))\nfrom blender.render import main\nmain(json.loads((root/"scene.json").read_text()),root)\n')
    env=dict(os.environ,OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='1',CUDA_VISIBLE_DEVICES='')
    env['LD_LIBRARY_PATH']=':'.join([str(scene_host.BLENDER.parent.parent/'libExt'),str(Path(sysconfig.get_path('purelib'))/'numpy.libs'),env.get('LD_LIBRARY_PATH','')])
    start=time.monotonic()
    with (folder/'blender.log').open('wb') as log:
        process=await asyncio.create_subprocess_exec(str(scene_host.BLENDER),'--background','--factory-startup','--disable-autoexec','--threads','2','--python-exit-code','1','--python',str(launcher),env=env,stdout=log,stderr=log)
        try:await asyncio.wait_for(process.wait(),1800)
        except (TimeoutError,asyncio.CancelledError):process.kill();await process.wait();raise
    if process.returncode:raise RuntimeError('Frozen Blender render failed; inspect bounded local log')
    await media.command('ffmpeg','-v','error','-y','-framerate','24','-i',str(folder/'frames/frame-%04d.png'),'-c:v','libx264','-crf','16','-preset','medium','-pix_fmt','yuv420p','-threads','2',str(folder/'preview.mp4'))
    shutil.rmtree(folder/'frames')
    return {'seconds':round(time.monotonic()-start,3),'width':data['width'],'height':data['height'],'blender_invocations':1}


async def current_preview(scene,quality,prompt):
    sid=str(uuid.uuid4());width,height=(768,1344) if quality=='native' else (384,672)
    started=time.monotonic();scene_host.start(sid,scene,10,width,height,prompt)
    await scene_host.tasks[sid]
    state=scene_host.read_status(sid)
    if state['status']!='completed':raise RuntimeError(state)
    state['wall_seconds']=round(time.monotonic()-started,3)
    return state


async def cpu(value,shot):
    folder=Path(value['folder']);record=folder/'cpu.json'
    if record.exists():return json.loads(record.read_text())
    if memory()<40:raise RuntimeError('CPU preview admission needs 40 GiB')
    print('CPU preview admission:',round(memory(),1),'GiB',flush=True)
    data={'scene':shot.scene,'duration':10,'width':768,'height':1344,'assets':{}}
    result={'before':await frozen_preview(folder/'before-preview',data)}
    print('Baseline native preview:',result['before'],flush=True)
    result['after']=await current_preview(shot.scene,'native',shot.renderer_prompt)
    result['warm']=await current_preview(shot.scene,'draft',shot.renderer_prompt+'\nCurrent draft prompt.')
    print('Current cold/warm previews:',result['after']['wall_seconds'],result['warm']['wall_seconds'],flush=True)
    fixture=json.loads(json.dumps(shot.scene));target=next(e for e in fixture['entities'] if e['id']==fixture['camera']['target'])
    target['path']=[{'time':t,'position':dict(x=x,y=y,z=0)} for t,x,y in [(0,0,0),(2,0,6),(4,-5,11),(6,-5,17),(8,-5,17),(10,-5,17)]]
    result['turn_before']=await frozen_preview(folder/'turn-before',data|{'scene':fixture,'width':384,'height':672})
    result['turn_after']=await current_preview(fixture,'draft',shot.renderer_prompt)
    for name,state in [('after-preview',result['after']),('turn-after',result['turn_after'])]:
        source=scene_host.folder_for(state['id']);destination=folder/name;destination.mkdir(exist_ok=True)
        for filename in ('preview.mp4','scene.blend','transforms.json','scene.json'):shutil.copy2(source/filename,destination/filename)
    record.write_text(json.dumps(result,indent=2));return result


async def main(args):
    value=manifest();folder=Path(value['folder'])
    shot=ShotSpec.model_validate_json((ROOT/'.motion-runtime/quality/optimized-opus-plan.json').read_text())
    results=await cpu(value,shot)
    baseline=Path(json.loads((ROOT/'.motion-runtime/quality/comparison-manifest.json').read_text())['folder'])
    for label,old in [('before-draft','C'),('before-native','D')]:
        for suffix in ('.mp4','-benchmark.json','-plan.json'):
            shutil.copy2(baseline/(old+suffix),folder/(label+suffix))
        for i in range(2):
            shutil.copy2(baseline/f'{old}-clip{i}-history.json',folder/f'{label}-clip{i}-history.json')
    if not args.run:return
    connection=sqlite3.connect('file:/srv/ai/outputs/neonforge_history.sqlite3?mode=ro',uri=True);connection.row_factory=sqlite3.Row
    assets=[SimpleNamespace(**dict(connection.execute('SELECT * FROM comfyui_assets WHERE id=?',(r,)).fetchone())) for r in shot.reference_ids];connection.close()
    qwen=None
    async with httpx.AsyncClient(timeout=180) as client:
        queue=(await client.get(BASE+'/queue')).json()
        if queue.get('queue_running') or queue.get('queue_pending'):raise RuntimeError('Existing H3 work preserved')
        resident=(await client.get('http://100.103.129.82:11434/api/ps')).json()['models']
        if resident:
            if not args.release_qwen or [m['name'] for m in resident]!=['qwen3.6:35b-a3b']:raise RuntimeError('Explicit existing Qwen release consent required; other models preserved')
            qwen=resident[0];(folder/'qwen-before.json').write_text(json.dumps(qwen,indent=2))
            response=await client.post('http://100.103.129.82:11434/api/generate',json={'model':qwen['name'],'keep_alive':0});response.raise_for_status()
            for _ in range(60):
                if not (await client.get('http://100.103.129.82:11434/api/ps')).json()['models']:break
                await asyncio.sleep(.5)
        try:
            for quality in args.run:
                name=args.case_prefix+'-'+quality;path=folder/(name+'-benchmark.json')
                resume=None
                if path.exists():
                    previous=json.loads(path.read_text())
                    if previous.get('status')=='completed':continue
                    if not args.resume_verified_interruption:raise RuntimeError('Incomplete benchmark retained; inspect accepted prompt before any explicit recovery')
                    last=previous['clips'][-1];prompt=last.get('prompt_id')
                    queue=(await client.get(BASE+'/queue')).json()
                    history=(await client.get(BASE+'/history/'+str(prompt))).json().get(prompt,{})
                    status=history.get('status',{})
                    if (not prompt or queue.get('queue_running') or queue.get('queue_pending') or status.get('status_str')!='error'
                        or not any(m[0]=='execution_interrupted' for m in status.get('messages',[]))
                        or any(c.get('status')!='completed' or not (folder/c['trimmed']).is_file() for c in previous['clips'][:-1])):
                        raise RuntimeError('Only a verified terminal interruption with intact predecessors can resume')
                    shutil.copy2(path,folder/f'{name}-interrupted-{prompt}.json')
                    (folder/f'{name}-interrupted-{prompt}-history.json').write_text(json.dumps(history,indent=2))
                    previous['clips'].pop();previous.setdefault('interrupted_attempts',[]).append(last)
                    previous['runtime_note']='Includes prior sampled active time and interrupted attempt; excludes offline cooling pause.'
                    resume=previous
                    print('Verified terminal interruption; explicitly resuming only clip',last['index']+1,prompt,flush=True)
                width,height=(768,1344) if quality=='native' else (384,672)
                case=shot.model_copy(deep=True);case.request.quality=quality;case.width,case.height=width,height
                (folder/(name+'-plan.json')).write_text(case.model_dump_json(indent=2))
                await render_case(name,case,folder/'after-preview/preview.mp4',folder,assets,resume,cool_start_c=args.cool_start_c)
        finally:
            queue=(await client.get(BASE+'/queue')).json()
            if qwen and not queue.get('queue_running') and not queue.get('queue_pending'):
                await host('h3/reclaim',{})
                response=await client.post('http://100.103.129.82:11434/api/generate',json={'model':qwen['name'],'keep_alive':-1,'stream':False,'options':{'num_ctx':qwen.get('context_length',262144)}});response.raise_for_status()
                restored=(await client.get('http://100.103.129.82:11434/api/ps')).json()['models']
                if not any(m['name']==qwen['name'] and m.get('context_length')==qwen.get('context_length') for m in restored):raise RuntimeError('Qwen residency verification failed')
                print('Restored original Qwen residency/context',flush=True)
            elif qwen:print('H3 still active; preserve exclusive reservation and defer Qwen restoration',flush=True)
    print('Artifacts:',folder,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',nargs='+',choices=['draft','native']);parser.add_argument('--release-qwen',action='store_true');parser.add_argument('--resume-verified-interruption',action='store_true');parser.add_argument('--cool-start-c',type=int,choices=[40,45,50],default=50);parser.add_argument('--case-prefix',choices=['after','final'],default='final');asyncio.run(main(parser.parse_args()))
