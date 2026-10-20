"""Saved native frames, camera transforms and measured common-grid proxies.

Use the verified uv environment. These image proxies do not measure identity;
visual review is recorded separately. No model, GPU or Opus is invoked.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from scripts.analyze_motion_quality import command,probe,image_metrics,TIMES
from scripts.benchmark_motion_iteration import manifest,ROOT


def camera(path,target):
    frames=json.loads(path.read_text())['frames']
    positions=np.array([f['camera'] for f in frames]);subject=np.array([f['entities'][target]['position'] for f in frames])
    steps=np.diff(positions,axis=0);accel=np.diff(steps,axis=0);jerk=np.diff(accel,axis=0)
    result={'max_camera_step_m':float(np.linalg.norm(steps,axis=1).max()),
        'camera_jerk_rms_m_per_frame_cubed':float(np.sqrt(np.mean(np.sum(jerk**2,axis=1)))),
        'minimum_horizontal_follow_m':float(np.linalg.norm((positions-subject)[:,:2],axis=1).min()),
        'maximum_horizontal_follow_m':float(np.linalg.norm((positions-subject)[:,:2],axis=1).max()),
        'target_y_range_m':[float(subject[:,1].min()),float(subject[:,1].max())],
        'target_displacement_after_6_seconds_m':float(np.linalg.norm(subject[144:]-subject[144],axis=1).max())}
    return {k:round(v,8) if isinstance(v,float) else v for k,v in result.items()}


def frames(video,destination,times):
    destination.mkdir(parents=True,exist_ok=True)
    for i,t in enumerate(times):
        command('ffmpeg','-v','error','-y','-threads','2','-ss',str(t),'-i',str(video),'-frames:v','1','-threads','2',str(destination/f'{i}.png'))
    contact=destination.parent/(destination.name+'-contact.png')
    command('ffmpeg','-v','error','-y','-threads','2','-framerate','1','-i',str(destination/'%d.png'),'-vf','scale=384:672:flags=lanczos,tile=3x2','-frames:v','1','-threads','2',str(contact))
    return contact


def main():
    value=manifest();folder=Path(value['folder']);results={'seed_per_segment':[42,43],'sample_times':TIMES,'cases':{},'cameras':{}}
    labels={'before-draft':'Baseline draft','after-draft':'Rejected continuity-text trial','final-draft':'Final implementation draft','before-native':'Preserved native baseline','after-native':'Experimental native trial','final-native':'Final implementation native'}
    for name in ('before-draft','after-draft','final-draft','before-native','after-native','final-native'):
        video=folder/(name+'.mp4')
        if not video.exists():continue
        item={'video':probe(video),'proxies':image_metrics(video)}
        benchmark=folder/(name+'-benchmark.json')
        if benchmark.exists():
            data=json.loads(benchmark.read_text());item['benchmark']={k:data.get(k) for k in ('runtime_seconds','minimum_available_gib','peak_temperature_c','max_clock_mhz','minimum_admission_gib')}
            item['benchmark']['shared_uma_consumption_from_admission_gib']=round(data['before_gib']-data['minimum_available_gib'],3)
            execution=[]
            for history in sorted(folder.glob(name+'-clip*-history.json')):
                events=json.loads(history.read_text())['status']['messages']
                start=next(e[1]['timestamp'] for e in events if e[0]=='execution_start')
                end=next(e[1]['timestamp'] for e in events if e[0]=='execution_success')
                execution.append((end-start)/1000)
            item['benchmark']['successful_h3_execution_seconds']=round(sum(execution),3)
            item['benchmark']['generation_claude_invocations']=0
            if data.get('interrupted_attempts'):item['benchmark']['runtime_note']=data['runtime_note']
        results['cases'][name]=item
        frames(video,folder/'frames'/name,TIMES)
    for name in ('before-preview','after-preview','turn-before','turn-after'):
        path=folder/name
        if not (path/'transforms.json').exists():continue
        scene=json.loads((path/'scene.json').read_text())['scene']
        results['cameras'][name]=camera(path/'transforms.json',scene['camera']['target'])
        frames(path/'preview.mp4',folder/'frames'/name,[.5,2.5,4.5,6.5,8,9.5])
    results['memory_note']='Shared UMA from /proc/meminfo, not dedicated VRAM. Consumption delta includes host/page-cache effects.'
    results['original_reference_sha256']=value.get('original_reference_sha256')
    if (folder/'native-trial-summary.json').exists():results['native_trial']=json.loads((folder/'native-trial-summary.json').read_text())
    results['cpu']=json.loads((folder/'cpu.json').read_text())
    usage=ROOT/'.motion-runtime/iteration/observed-opus-plan.json'
    if usage.exists():results['observed_planning_usage']=json.loads(usage.read_text())['planning_usage']
    (folder/'measurements.json').write_text(json.dumps(results,indent=2))
    cards=''.join(f'<article><h2>{labels[name]}</h2><video controls muted playsinline preload="metadata" src="{name}.mp4"></video><p>{item["video"]["width"]} × {item["video"]["height"]} · {item["video"]["duration"]:.3f}s</p><a href="frames/{name}-contact.png"><img src="frames/{name}-contact.png" alt="Native samples reduced for display"></a><a href="{name}.mp4" download>Download clip</a></article>' for name,item in results['cases'].items())
    controls=''.join(f'<article><h3>{name}</h3><video controls muted playsinline preload="metadata" src="{name}/preview.mp4"></video><a href="frames/{name}-contact.png"><img src="frames/{name}-contact.png" alt="Camera control preview samples"></a></article>' for name in results['cameras'])
    (folder/'comparison.html').write_text('''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Motion Director iteration comparison</title><style>body{background:#11131c;color:#eee;font:16px system-ui;margin:24px}a{color:#9acbff}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:20px}article{background:#202432;padding:16px;border-radius:12px}video,img{width:100%;max-height:65vh;object-fit:contain}p{max-width:90ch}button{padding:12px;font:inherit}</style><h1>Running character · iteration comparison</h1><p>Same reference, scene specification and segment seeds 42/43. Final: damped Blender follow rig, bounded motion preview, cached scene reuse and observed usage. The deployed H3 prompt/graph and inference settings are unchanged. Native output remains 768×1344; no upscaling. Before clips are preserved from the preceding controlled benchmark.</p><p>The continuity-text trial was rejected: no clear appearance gain and slower draft inference. Its two native attempts stopped at the unchanged 70°C guard before completing a segment, including a retry from 40°C. There is no completed new native clip. The existing validated native baseline remains available below.</p><p>Inspect costume, body scale, train geometry and bystanders throughout. Similarity proxies are affected by action and perspective; they are not identity scores.</p><button onclick="document.querySelectorAll('main:first-of-type video').forEach(v=>{v.currentTime=0;v.play()})">Play H3 comparisons from start</button><p><a href="measurements.json">Measurements and observed token usage</a> · <a href="visual-review.json">Visual assessment</a> · <a href="native-trial-summary.json">Thermally interrupted native trial</a></p><main>'''+cards+'</main><h2>Blender controls · straight follow and turn/stop fixture</h2><p>Camera samples: 0.5, 2.5, 4.5, 6.5, 8 and 9.5 seconds. The turn fixture stops at 6 seconds; its purpose is to expose overshoot, heading resets and camera jumps. These are deterministic control renders, not H3 appearance outputs.</p><main>'+controls+'</main>')
    print(json.dumps(results,indent=2))


if __name__=='__main__':main()
