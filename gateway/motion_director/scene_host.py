"""Bounded host-side CPU Blender previews, served through scoped socket calls."""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sysconfig
import uuid
import zipfile
import time

from . import media
from .scene import SceneSpec
from .animation_library import CLIPS, ROOT as ANIMATION_ROOT, catalog as animation_catalog
from shared.motion_quality import minimum_for_size

OUTPUTS = Path(os.getenv('NEONFORGE_HOST_OUTPUTS_DIR',str(Path(__file__).resolve().parents[2]/'.motion-runtime/scene-outputs')))
BLENDER = Path(os.getenv('NEONFORGE_BLENDER_BIN', str(Path.home()/'opt/blender-5.2.0-arm64/cmake-make/bin/blender')))
FORGE = Path(os.getenv('NEONFORGE_FORGE_ASSETS', str(Path.home()/'3d-generator/data/outputs')))
render_lock = asyncio.Semaphore(1)
tasks = {}


def digest_file(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024**2),b''):result.update(chunk)
    return result.hexdigest()


def render_key(data,catalog):
    compiler={p.name:digest_file(p) for p in sorted((Path(__file__).parent/'blender').glob('*.py'))}
    value={k:data[k] for k in ('scene','duration','width','height','assets')}
    value.update(compiler=compiler,format_version=3,asset_content={k:digest_file(catalog[k]) for k in data['assets']},
                 animation_content={p.name:digest_file(p) for p in ANIMATION_ROOT.glob('*') if p.is_file()} if data.get('animation_library') else {})
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def cache_source(key):
    path=OUTPUTS/'motion_director/.preview-cache'/f'{key}.json'
    try:
        source=folder_for(json.loads(path.read_text())['id'])
        state=read_status(source.name)
        if state.get('status')=='completed' and state.get('render_key')==key and all((source/name).is_file() for name in ('preview.mp4','first-frame.png','scene.blend','transforms.json')):
            return source
    except (OSError,ValueError,KeyError):pass
    return None


def asset_catalog():
    # Read-only retrieval; no Forge process, model load or profile modification.
    result={}
    if FORGE.is_dir():
        for path in sorted(FORGE.glob('**/*.glb'))[:100]:
            if path.is_symlink() or not path.resolve().is_relative_to(FORGE.resolve()) or path.stat().st_size>100*1024**2:
                continue
            key=hashlib.sha256(str(path.relative_to(FORGE)).encode()).hexdigest()[:16]
            result[key]=path
    return result


def status():
    return {'available':BLENDER.is_file(), 'cpu_preview':True,
            'reason':'' if BLENDER.is_file() else 'The existing Blender binary is unavailable. Configure NEONFORGE_BLENDER_BIN; no installation is performed.',
            'assets':[{'id':k,'label':p.parent.name+' / '+p.stem,'rigged':False} for k,p in asset_catalog().items()],
            'clips':['idle','walk','run'], 'authored_clips':animation_catalog(),
            'animation_limitations':['Native Quaternius humanoid rigs only; no arbitrary mesh retargeting.', 'No licensed compatible kick or unarmed block clip is bundled.'],
            'conditioning':'reference-video; approximate motion, not strict trajectory control'}


def folder_for(scene_id):
    return OUTPUTS/'motion_director/scenes'/str(uuid.UUID(scene_id))


def read_status(scene_id):
    folder=folder_for(scene_id);path=folder/'status.json'
    if not path.is_file():
        return {'id':scene_id,'status':'missing'}
    state=json.loads(path.read_text())
    if state['status'] in {'rendering','queued'} and scene_id not in tasks:
        state.update(status='failed',error='Blender preview interrupted by host restart; rebuild this preview.')
    return state


def write_status(folder,state):
    part=folder/'status.tmp';part.write_text(json.dumps(state));part.replace(folder/'status.json')


def start(scene_id, scene, duration, width, height, prompt):
    started=time.monotonic()
    folder=folder_for(scene_id);folder.mkdir(parents=True,exist_ok=True)
    spec=SceneSpec.model_validate(scene).validate_timeline(duration)
    minimum_for_size(width, height)
    if duration not in {2,3,5,10,15,30}:
        raise ValueError('Preview dimensions/duration exceed the existing shot limits')
    catalog=asset_catalog();ids={e.asset_id for e in spec.entities if e.geometry=='asset'}
    if ids-set(catalog):
        raise ValueError('A selected Forge3D asset is no longer available')
    if sum(catalog[k].stat().st_size for k in ids) > 256*1024**2:
        raise ValueError('Preview assets exceed the 256 MiB scene budget')
    # H3 consumes this bounded motion canvas even when generating native detail.
    # Final generation stays at the requested resolution; this is not an upscale.
    pw,ph=(384,672) if height>width else (672,384)
    data={'scene':spec.model_dump(),'duration':duration,'width':pw,'height':ph,
          'generation_width':width,'generation_height':height,'assets':{k:f'assets/{k}.glb' for k in ids}}
    if any(e.rig=='quaternius-v1' for e in spec.entities):
        data['animation_library']=CLIPS
    digest=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    if (folder/'status.json').exists():
        old=read_status(scene_id)
        if not old.get('render_key') and old.get('status')=='completed':
            # Preserve legacy immutable artifacts; a new plan gets the new rig.
            previous=json.loads((folder/'scene.json').read_text())
            if (SceneSpec.model_validate(previous['scene']).model_dump()==spec.model_dump()
                and (previous['duration'],previous['width'],previous['height'])==(duration,width,height)):
                return {**old,'cache_hit':True,'blender_invocations':0,'elapsed_seconds':round(time.monotonic()-started,3)}
        if old.get('digest')!=digest:
            raise ValueError('Saved preview differs from the scene plan; create a revision')
        if old['status']!='failed':return {**old,'cache_hit':old['status']=='completed','blender_invocations':0,'elapsed_seconds':round(time.monotonic()-started,3)}
    if len(tasks) >= 4:
        raise ValueError('Four Blender previews are already waiting')
    state={'id':scene_id,'status':'queued','digest':digest,'render_key':render_key(data,catalog),
           'preview_width':pw,'preview_height':ph,'generation_width':width,'generation_height':height,
           'message':'Waiting for the Blender CPU preview…'}
    write_status(folder,state)
    task=asyncio.create_task(render(folder,data,prompt,catalog,state))
    tasks[scene_id]=task;task.add_done_callback(lambda done:tasks.pop(scene_id,None))
    return state


async def render(folder,data,prompt,catalog,state):
    async with render_lock:
        started=time.monotonic()
        try:
            available=next(int(line.split()[1])/1048576 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
            if available<40:raise RuntimeError('Blender preview needs at least 40 GiB available shared memory')
            if not BLENDER.is_file():raise RuntimeError('Existing Blender binary is unavailable')
            state.update(status='rendering',message='Building geometry, rig, paths and camera; rendering on CPU…');write_status(folder,state)
            shutil.copytree(Path(__file__).parent/'blender', folder/'blender',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
            if data.get('animation_library'):
                shutil.copytree(ANIMATION_ROOT,folder/'animation_assets',dirs_exist_ok=True)
            (folder/'assets').mkdir(exist_ok=True)
            for key in data['assets']:shutil.copy2(catalog[key],folder/data['assets'][key])
            (folder/'scene.json').write_text(json.dumps(data,indent=2))
            # Downloadable Python generated from the validated Opus specification.
            # Only data is model-authored; the execution logic is this reviewed compiler.
            (folder/'scene.py').write_text('import json, os, sys\nfrom pathlib import Path\nroot=Path(__file__).resolve().parent\nos.chdir(root)\nsys.path.insert(0,str(root))\nfrom blender.render import main\nmain(json.loads('+repr(json.dumps(data))+'),root)\n')
            env=dict(os.environ)
            # The existing ARM64 Blender bundle lacks NumPy's OpenBLAS .so.
            # uv's pinned optional group supplies only subprocess library lookup;
            # do not replace Blender's Python/NumPy or alter its installation.
            libraries=[str(BLENDER.parent.parent/'libExt'), str(Path(sysconfig.get_path('purelib'))/'numpy.libs')]
            env['LD_LIBRARY_PATH']=':'.join(libraries)+(':'+env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
            env.update(OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='1',CUDA_VISIBLE_DEVICES='')
            source=cache_source(state['render_key'])
            state.update(cache_hit=bool(source),blender_invocations=0 if source else 1)
            rendering=time.monotonic()
            if source:
                for name in ('preview.mp4','first-frame.png','scene.blend','transforms.json'):shutil.copy2(source/name,folder/name)
                if (source/'timeline-validation.json').is_file():shutil.copy2(source/'timeline-validation.json',folder/'timeline-validation.json')
                state['cached_from']=source.name
            else:
                with (folder/'blender.log').open('wb') as log:
                    proc=await asyncio.create_subprocess_exec(str(BLENDER),'--background','--factory-startup','--disable-autoexec','--threads','2','--python-exit-code','1','--python',str(folder/'scene.py'),env=env,cwd=folder,stdout=log,stderr=log)
                    try:await asyncio.wait_for(proc.wait(),1800)
                    except (TimeoutError,asyncio.CancelledError):
                        proc.kill();await proc.wait();raise
                if proc.returncode:raise RuntimeError('Blender could not render this scene; inspect its bounded local log')
                await media.command('ffmpeg','-v','error','-y','-framerate','24','-i',str(folder/'frames/frame-%04d.png'),'-c:v','libx264','-crf','16','-preset','medium','-pix_fmt','yuv420p','-threads','2','-movflags','+faststart',str(folder/'preview.mp4'))
                shutil.copy2(folder/'frames/frame-0001.png',folder/'first-frame.png')
            if data['scene'].get('interactions'):
                report=json.loads((folder/'timeline-validation.json').read_text())
                if not report.get('valid'):raise RuntimeError('The rendered choreography did not pass contact/framing validation')
                state['timeline_verified']=True
            state['render_seconds']=round(time.monotonic()-rendering,3)
            browser_prompt = re.sub(r'<Picture (\d+)> is reference [a-zA-Z0-9_-]+\.', r'Original image \1 supplies identity and style.', prompt)
            (folder/'prompt.txt').write_text(browser_prompt+'\nUse the Blender reference for action, spatial layout and independent camera motion. Preserve the original image identity/style. One continuous shot. Reference motion is approximate, not guaranteed.\n')
            with zipfile.ZipFile(folder/'grok-export.zip','w',zipfile.ZIP_DEFLATED) as bundle:
                for path in [folder/'preview.mp4',folder/'first-frame.png',folder/'prompt.txt',folder/'scene.py',folder/'scene.json',folder/'scene.blend',folder/'transforms.json',*folder.glob('timeline-validation.json'),*sorted((folder/'blender').glob('*.py')),*sorted((folder/'assets').glob('*.glb')),*sorted((folder/'animation_assets').glob('*'))]:
                    bundle.write(path,str(path.relative_to(folder)))
            # Keep the compact video and edit sources; per-frame PNGs are temporary.
            if (folder/'frames').exists():shutil.rmtree(folder/'frames')
            state.update(status='completed',message='Reused existing Blender motion preview' if source else 'Blender preview ready',
                         elapsed_seconds=round(time.monotonic()-started,3),conditioning='reference-video; not exact motion control')
            index=OUTPUTS/'motion_director/.preview-cache';index.mkdir(exist_ok=True)
            (index/(state['render_key']+'.json')).write_text(json.dumps({'id':folder.name}))
        except (Exception,asyncio.CancelledError) as exc:
            state.update(status='failed',message='Blender preview stopped',error=str(exc)[:400] or 'Preview interrupted')
        finally:write_status(folder,state)
