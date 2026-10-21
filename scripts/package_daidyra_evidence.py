"""Package existing real results for private browser review; never generate media."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import httpx

ROOT=Path('.motion-runtime/daidyra')
SCENES=Path('.motion-runtime/scene-outputs/motion_director/scenes')


def main():
    study=json.loads((ROOT/'study-result.json').read_text())['id']
    fresh=json.loads((ROOT/'fresh-claude-plan.json').read_text())['request']['plan_id']
    folder=SCENES/study/'acceptance';folder.mkdir(exist_ok=True)
    files={'reference.png':ROOT/'reference.png','blender.mp4':SCENES/study/'preview.mp4','claude-blender.mp4':SCENES/fresh/'preview.mp4',
           'legacy-draft.mp4':ROOT/'control-h3.mp4','full-guide.mp4':ROOT/'fight-h3.mp4','full-no-guide.mp4':ROOT/'candidate-h3.mp4',
           'previous-fight.mp4':ROOT/'previous-fight.mp4','timeline-validation.json':SCENES/fresh/'timeline-validation.json'}
    for name,source in files.items():shutil.copy2(source,folder/name)
    records={}
    for variant in ('control','fight','candidate'):
        job=json.loads((ROOT/(variant+'-result.json')).read_text());samples=json.loads((ROOT/(variant+'-resources.json')).read_text())
        pid=job['prompt_id'];history=httpx.get('http://127.0.0.1:8189/history/'+pid,timeout=15).raise_for_status().json()
        (ROOT/(variant+'-h3-history.json')).write_text(json.dumps(history,indent=2))
        graph=history[pid]['prompt'][2]
        motion=Path('/srv/ai/outputs/motion_director')/job['job_id']/'blender-motion-0.mp4'
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-show_entries','stream=nb_read_frames,width,height','-show_entries','format=duration','-of','json',str(motion)]))
        prefix=graph['138']['inputs']['value'].split('\n<Video 1> supplies motion timing, layout and camera tracking ONLY.')[0]
        records[variant]={'job_id':job['job_id'],'status':job['status'],'seed':graph['129']['inputs']['noise_seed'],'steps':graph['124']['inputs']['steps'],
            'canvas':{k:graph['136']['inputs'][k] for k in ('width','height','length')},'image':graph['ref_0']['inputs']['image'],
            'appearance_guide':'appearance_guide' in graph,'motion_frames':int(probe['streams'][0]['nb_read_frames']),
            'prompt_sha256':hashlib.sha256(prefix.encode()).hexdigest(),'min_available_gib':min(s['available_gib'] for s in samples),
            'max_temperature_c':max(s['temperature'] for s in samples),'max_clock_mhz':max(s['clock'] for s in samples),'resource_samples':len(samples)}
    assert len({r['image'] for r in records.values()})==1
    assert len({r['prompt_sha256'] for r in records.values()})==1
    assert all(r['seed']==42 and r['steps']==4 and r['canvas']=={'width':384,'height':672,'length':124} for r in records.values())
    assert [records[v]['motion_frames'] for v in ('control','fight','candidate')]==[22,124,124]
    assert [records[v]['appearance_guide'] for v in ('control','fight','candidate')]==[False,True,False]
    report={'study_id':study,'fresh_claude_scene':fresh,'controlled_trials':records,
        'identical_blender_video':json.loads((ROOT/'controlled-settings.json').read_text()),
        'selection':'Opening-window draft: best original alien identity/character count in this one-scene comparison. Full motion remains native behavior; draft exact pose transfer is limited.',
        'observations':{'blender':'Real authored skeletal punches, timed reactions, crouch dodge and recovery; foot IK; both actors framed.',
            'h3':'All trials show fighting and retain clothing/environment motifs. Full-video trials morph the alien face into a mask and add alien-like figures. Hands, exact contact and background motion remain approximate.',
            'limits':'Qualitative inspection of one scene and one seed; no claim of general identity or interaction accuracy. Resource samples do not establish instantaneous peaks.'}}
    (folder/'results.json').write_text(json.dumps(report,indent=2));(ROOT/'comparison-results.json').write_text(json.dumps(report,indent=2))
    html='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Daidyra · Fight study</title>
<style>body{margin:0;background:#101117;color:#eee9df;font:15px/1.6 system-ui}main{max-width:1300px;margin:auto;padding:24px}h1{font-size:clamp(28px,4vw,42px);font-weight:500;letter-spacing:-.03em}h2{font-size:18px;font-weight:500}small,p{color:#b8b7bf}.eyebrow{color:#d5bd8c;text-transform:uppercase;letter-spacing:.2em;font-size:11px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px}article,details{border:1px solid #33323a;border-radius:15px;padding:16px;background:#151720}video,img{width:100%;height:430px;object-fit:contain;background:#08090d;border-radius:8px}button,a{display:inline-flex;align-items:center;min-height:44px;color:#dfc598}button{border:1px solid #6a5b40;border-radius:8px;background:#26231e;padding:0 18px;cursor:pointer}summary{cursor:pointer;min-height:44px}details{margin-top:24px}.links{display:flex;gap:24px;flex-wrap:wrap}@media(max-width:600px){main{padding:16px}video,img{height:400px}}</style>
<main><p class="eyebrow">Daidyra · Motion Director · Real acceptance evidence</p><h1>From reference to a readable fight.</h1><p>Five seconds. Two independent rigs. Authored skeletal clips. One original reference image.</p><button onclick="playAll()">Play comparisons from start</button>
<div class="grid"><article><h2>Original reference</h2><img src="reference.png" alt="Original neon platform with human, alien and winged cat"><p>Identity, clothing, environment and relative screen sides.</p></article><article><h2>Reusable Blender study</h2><video src="blender.mp4" controls playsinline muted preload="metadata"></video><p>Actual motion supplied to all three H3 trials.</p></article><article><h2>Selected draft recipe</h2><video src="legacy-draft.mp4" controls playsinline preload="metadata"></video><p>Opening motion window + original image. Best alien appearance in this comparison; final timing/contact remain approximate.</p></article></div>
<details open><summary>Fresh Claude prompt → real Blender output</summary><div class="grid"><article><h2>“the characters start fighting”</h2><video src="claude-blender.mp4" controls playsinline muted preload="metadata"></video></article><article><h2>Verified scene timeline</h2><p>Human left jab / alien head reaction at 1.30s.<br>Alien counter / human chest reaction at 2.68s.<br>Human cross / alien crouch dodge at 3.95s.<br>Return to guard.</p><p>Actual fresh Claude planning after official Pro sign-in refresh. The CPU Blender preview passed evaluated contact and framing checks.</p><a href="timeline-validation.json">Evaluated timeline report</a></article></div></details>
<details><summary>Matched conditioning comparison · same reference, prompt, seed 42, 384×672, four steps</summary><div class="grid"><article><h2>124 motion frames + extra pose guide</h2><video src="full-guide.mp4" controls playsinline preload="metadata"></video><p>Readable choreography; alien face becomes a mask.</p></article><article><h2>124 motion frames without extra guide</h2><video src="full-no-guide.mp4" controls playsinline preload="metadata"></video><p>Face drift and extra alien-like figures remain. Removing the extra guide alone does not resolve proxy appearance transfer.</p></article><article><h2>Prior saved “fight” output</h2><video src="previous-fight.mp4" controls playsinline preload="metadata"></video><p>Runs from a mismatched old scene and uses another reference. Shown for context; not a matched control.</p></article></div></details>
<p>This one-scene comparison does not establish general realistic interaction support. No compatible kick or unarmed block asset is bundled. Native rig retargeting to arbitrary user meshes is unavailable. H3 can change faces, hands, exact contacts and background objects.</p><div class="links"><a href="results.json">Settings, receipts & resource samples</a><a href="../scene.blend">Download editable Blender study</a><a href="legacy-draft.mp4" download>Download selected H3 draft</a></div></main>
<script>async function playAll(){const v=[...document.querySelectorAll('video')];await Promise.all(v.map(async x=>{x.pause();x.currentTime=0;x.muted=true;try{await x.play()}catch{}}))}</script></html>'''
    (folder/'comparison.html').write_text(html)
    print('/api/v1/outputs/motion_director/scenes/'+study+'/acceptance/comparison.html')


if __name__=='__main__':main()
