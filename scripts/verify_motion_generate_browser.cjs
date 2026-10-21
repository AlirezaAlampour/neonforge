// Deployed Generate acceptance. Requires existing uv environment and Playwright.
// Fixtures are recorded real saved plans, never replacement API responses.
// MOTION_GENERATE_LIVE=1 enables ONE real draft and controlled Redis/gateway interruptions.
const fs=require('node:fs'),assert=require('node:assert/strict'),cp=require('node:child_process'),{promisify}=require('node:util')
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/home/xxfactionsxx/desk-space-program/node_modules/playwright')
const root=process.env.MOTION_GENERATE_EVIDENCE||'.motion-runtime/generate-fix',base=process.env.MOTION_TEST_URL||'http://100.103.129.82:3000',key='neonforge-motion-director-v0.1'
const rows=JSON.parse(fs.readFileSync(root+'/plans-before.json')),trace=[],results=[]
const save=()=>{fs.writeFileSync(root+'/browser-requests.json',JSON.stringify(trace,null,2));fs.writeFileSync(root+'/browser-results.json',JSON.stringify(results,null,2))}
const row=prefix=>rows.find(r=>r.id.startsWith(prefix))
const wait=ms=>new Promise(r=>setTimeout(r,ms))
const normalized=shot=>({annotations:[],mode:'auto',preset:'professional',director:'local',engine:'h3',pipeline:shot.scene?'3d':'prompt',quality:'draft',parent_plan_id:null,revision:'',...shot.request})
const json=async(response)=>{assert(response.ok(),response.status()+' '+await response.text());return response.json()}
let browser,redisStopped=false,heldPreview=null,guard=null
;(async()=>{
 browser=await chromium.launch({headless:true,args:['--no-sandbox']})
 async function open(saved,mobile=false,pending=null){
  const p=await browser.newPage({viewport:{width:mobile?390:1440,height:mobile?844:1000},isMobile:mobile,hasTouch:mobile,timezoneId:'America/Los_Angeles'})
  p.on('response',async r=>{if(r.url().includes('/motion-director/')&&r.request().method()==='POST'){
   const body=await r.text().catch(()=>'<closed response>');trace.push({url:r.url(),request:r.request().postDataJSON(),status:r.status(),response:body});save()
  }})
  p.on('pageerror',e=>{results.push({page_error:String(e)});save()})
  const request=normalized(saved.shot),{reference_ids,...rest}=request
  const draft={...rest,references:reference_ids.map((id,i)=>({id,name:'Saved reference '+(i+1)}))}
  const preview=saved.preview?.status==='completed'?saved.preview:{id:saved.id,status:'completed',preview_url:'/api/v1/outputs/motion_director/scenes/c5778687-3489-48c8-8c8a-c899813f50f7/preview.mp4'}
  await p.addInitScript(({key,draft,plan,preview,pending})=>{if(!sessionStorage.getItem('generate-fixture-seeded')){localStorage.setItem(key,JSON.stringify(draft));localStorage.setItem(key+'-preview',JSON.stringify({plan,preview}));if(pending)localStorage.setItem(key+'-submission',JSON.stringify({body:JSON.stringify(Object.fromEntries(Object.entries(pending.request).sort(([a],[b])=>a.localeCompare(b)))),key:pending.key}));else localStorage.removeItem(key+'-submission');sessionStorage.setItem('generate-fixture-seeded','1')}},{key,draft,plan:saved.shot,preview,pending})
  await p.goto(base+'/motion-director');await p.getByRole('region',{name:'Final generation'}).waitFor()
  return p
 }
 const final=p=>p.getByRole('region',{name:'Final generation'})
 const generate=p=>final(p).getByRole('button',{name:'Generate video',exact:true})
 async function ready(p){await p.waitForFunction(()=>[...document.querySelectorAll('section[aria-label="Final generation"] button')].some(b=>b.textContent==='Generate video'&&!b.disabled),{},{timeout:180000})}
 async function capture(p,name){await final(p).scrollIntoViewIfNeeded();assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await p.screenshot({path:root+'/'+name+'.png'});save()}
 async function rebuild(p){
  const response=p.waitForResponse(r=>r.url().endsWith('/rebuild')&&r.request().method()==='POST')
  await final(p).getByRole('button',{name:'Rebuild saved scene preview',exact:true}).click()
  const data=await json(await response);assert.equal(data.plan.planning_usage.new_claude_invocations,0)
  await ready(p);return data
 }
 // Real rejected saved states must be blocked before /jobs is sent.
 for(const [prefix,mobile] of [['c5778687',false],['7e2abcad',true]]){
  const p=await open(row(prefix),mobile)
  await final(p).getByText(/old camera edit belongs to a different prompt or reference set/).waitFor()
  assert(await generate(p).isDisabled());assert.equal(trace.filter(r=>r.url.endsWith('/jobs')).length,0)
  await capture(p,'blocked-'+prefix);results.push({case:'restored-incompatible-'+prefix,generate_enabled:false});await p.close()
 }
 // An older classic request remains compatible despite its historical schema hash.
 const legacy=await open(row('5c7455af'));await ready(legacy);results.push({case:'legacy-hash',ready:true});await legacy.close()
 // Native preview absent on server, although localStorage claims completion.
 const native=await open(row('853e5a08'),true)
 await final(native).getByRole('button',{name:'Rebuild saved scene preview'}).waitFor();assert(await generate(native).isDisabled())
 const repaired=await rebuild(native);assert.deepEqual([repaired.plan.width,repaired.plan.height],[768,1344]);assert.notEqual(repaired.plan.request.plan_id,row('853e5a08').id)
 await native.getByText('Advanced · settings & shot plan',{exact:true}).click()
 await native.getByLabel('Generation quality',{exact:true}).selectOption('draft')
 await final(native).getByRole('button',{name:'Rebuild saved scene preview'}).waitFor();assert(await generate(native).isDisabled())
 const draft=await rebuild(native);assert.deepEqual([draft.plan.width,draft.plan.height],[384,672])
 await native.getByLabel('Generation quality',{exact:true}).selectOption('native')
 await final(native).getByRole('button',{name:'Rebuild saved scene preview'}).waitFor();const nativeAgain=await rebuild(native)
 assert.deepEqual([nativeAgain.plan.width,nativeAgain.plan.height],[768,1344]);await capture(native,'quality-mobile')
 results.push({case:'native-draft-native',ready:true,claude_invocations:0});await native.close()
 // Upgrade legacy scene data, then create a real new CPU Blender camera scene.
 const p=await open(row('2794503c'),true)
 await final(p).getByRole('button',{name:'Rebuild saved scene preview'}).waitFor()
 const upgraded=await rebuild(p);assert.notEqual(upgraded.plan.request.plan_id,row('2794503c').id)
 await p.getByLabel('Correct the scene',{exact:true}).fill('Move the camera to 5.3 metres behind the runner.')
 const revisedResponse=p.waitForResponse(r=>r.url().endsWith('/revise')&&r.request().method()==='POST')
 await p.getByRole('button',{name:'Apply correction & preview',exact:true}).click()
 const revised=await json(await revisedResponse);assert.equal(revised.planning_usage.new_claude_invocations,0);assert.equal(revised.scene.camera.distance,5.3)
 await ready(p)
 const video=p.getByLabel('Blender 3D preview',{exact:true});await video.evaluate(v=>new Promise((resolve,reject)=>{if(v.readyState>=1)return resolve();v.onloadedmetadata=resolve;v.onerror=()=>reject(Error('Real Blender preview did not decode'))}))
 assert.equal(await video.evaluate(v=>v.videoWidth),384)
 fs.writeFileSync(root+'/reviewed-plan.json',JSON.stringify(revised,null,2))
 const freshState=await json(await p.request.get(base+`/api/v1/motion-director/plans/${revised.request.plan_id}/preview`))
 results.push({case:'new-camera-scene',ready:true,preview:freshState})
 await p.reload();await ready(p);assert.equal(await p.getByLabel('Cinematic prompt').inputValue(),revised.request.prompt)
 // A server-side preview loss between validation and /jobs must show HTTP 409 beside Generate.
 const previewFile=process.cwd()+`/.motion-runtime/scene-outputs/motion_director/scenes/${revised.request.plan_id}/preview.mp4`
 assert(!rows.some(r=>r.id===revised.request.plan_id));assert(fs.existsSync(previewFile))
 await p.route('**/api/v1/motion-director/jobs',async route=>{
  if(route.request().method()!=='POST')return route.continue()
  heldPreview=[previewFile,previewFile+'.generate-verification'];fs.renameSync(...heldPreview)
  try{const response=await route.fetch();assert.equal(response.status(),409);await route.fulfill({response})}
  finally{fs.renameSync(heldPreview[1],heldPreview[0]);heldPreview=null}
 })
 const rejected=p.waitForResponse(r=>r.url().endsWith('/jobs')&&r.request().method()==='POST')
 await generate(p).click();const rejectedResponse=await rejected;assert.equal(rejectedResponse.status(),409)
 await final(p).getByRole('alert').waitFor();await final(p).scrollIntoViewIfNeeded()
 assert(await final(p).getByRole('alert').evaluate(e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight}))
 await capture(p,'inline-server-409-mobile');await p.unroute('**/api/v1/motion-director/jobs')
 results.push({case:'server-409-after-validation',status:409,inline:true,detail:await rejectedResponse.json()})
 // Existing takes reuse their server plan and cached preview without /plan or Claude.
 const beforePlanCalls=trace.filter(r=>r.url.endsWith('/motion-director/plan')).length
 const take=p.getByRole('article',{name:'Take 1fe0024b-3958-4384-87ac-5e8ee42bd2f6'})
 await take.getByRole('button',{name:'Reuse shot',exact:true}).click();await ready(p)
 assert.equal(trace.filter(r=>r.url.endsWith('/motion-director/plan')).length,beforePlanCalls)
 results.push({case:'reuse-take',ready:true,claude_invocations:0})
 await p.close()
 // Restore the newly reviewed scene for the single optional live draft.
 const live=await open({id:revised.request.plan_id,shot:revised,preview:freshState},true);await ready(live)
 await capture(live,'ready-mobile');await live.setViewportSize({width:1440,height:1000});await capture(live,'ready-desktop')
 if(process.env.MOTION_GENERATE_LIVE==='1'){
  assert(!fs.existsSync(root+'/accepted.json'),'An H3 draft was already accepted; reconcile it instead of submitting another.')
  assert.equal(revised.request.quality,'draft');assert(revised.request.duration<=10)
  const cap=await json(await live.request.get(base+'/api/v1/motion-director/capabilities?quality=draft'))
  assert(cap.h3.render_ready&&cap.resources.memory.available_gb>=90&&cap.resources.gpu.temperature_c<50&&cap.resources.gpu.cap_active)
  let currentPrompt=null,safetyError=null;const samples=[]
  const sample=()=>{
   const kb=Number(fs.readFileSync('/proc/meminfo','utf8').match(/^MemAvailable:\s+(\d+)/m)[1])
   const [temperature,clock,utilization]=cp.execFileSync('nvidia-smi',['--query-gpu=temperature.gpu,clocks.gr,utilization.gpu','--format=csv,noheader,nounits'],{encoding:'utf8'}).trim().split(',').map(Number)
   samples.push({at:new Date().toISOString(),available_gib:kb/1048576,temperature,clock,utilization});fs.writeFileSync(root+'/resources.json',JSON.stringify(samples,null,2))
   if(temperature>=70||kb/1048576<40||clock>2000){safetyError='Resource guard blocked acceptance';if(currentPrompt)fetch('http://127.0.0.1:8189/interrupt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt_id:currentPrompt})}).catch(()=>{})}
  }
  sample();guard=setInterval(sample,2000)
  cp.execFileSync('docker',['stop','--time','10','ai-redis']);redisStopped=true
  const acceptedResponse=live.waitForResponse(r=>r.url().endsWith('/jobs')&&r.request().method()==='POST')
  // Two DOM clicks in one event turn test the synchronous submission guard.
  await generate(live).evaluate(button=>{button.click();button.click()})
  const response=await acceptedResponse;assert.equal(response.status(),202,await response.text());const accepted=await response.json()
  fs.writeFileSync(root+'/accepted.json',JSON.stringify({request:response.request().postDataJSON(),key:response.request().headers()['idempotency-key'],job:accepted},null,2))
  assert.equal((await json(await live.request.get(base+'/jobs/'+accepted.job_id))).job_id,accepted.job_id)
  cp.execFileSync('docker',['start','ai-redis']);redisStopped=false
  await live.reload();await final(live).getByText(/Video submitted/).waitFor();assert(await generate(live).isDisabled())
  let restarted=false,resumed=false,result=null;const began=Date.now()
  while(Date.now()-began<10*60000){
   assert(!safetyError,safetyError)
   let job
   try{job=await json(await live.request.get(base+'/api/v1/motion-director/jobs/'+accepted.job_id))}catch(e){await wait(2000);continue}
   currentPrompt=job.prompt_id||currentPrompt
   console.log(job.status,job.message)
   if(!restarted&&job.prompt_id&&job.status==='running'){
    restarted=true;await promisify(cp.execFile)('docker',['restart','--time','15','ai-gateway']);await wait(2500)
   }else if(restarted&&!resumed&&job.status==='interrupted'&&job.can_retry){
    const article=live.getByRole('article',{name:'Take '+accepted.job_id});const retryResponse=live.waitForResponse(r=>r.url().endsWith('/'+accepted.job_id+'/retry'));await article.getByRole('button',{name:'Retry remaining clips'}).click();const retried=await retryResponse;if(retried.status()===202)resumed=true;else assert.equal(retried.status(),409)
   }else if(['completed','failed','blocked','needs_inspection','cancelled'].includes(job.status)){result=job;break}
   await wait(3000)
  }
  assert(result,'Accepted job did not terminate');assert.equal(result.status,'completed',JSON.stringify(result));assert(restarted)
  const prompts=result.segments.map(s=>s.prompt_id);assert.equal(new Set(prompts).size,result.total_segments);assert.equal(result.completed_segments,result.total_segments)
  // Replay the accepted acknowledgement after Redis/worker restarts; no new inference.
  const receipt=JSON.parse(fs.readFileSync(root+'/accepted.json'))
  const replay=await live.request.post(base+'/api/v1/motion-director/jobs',{data:receipt.request,headers:{'Idempotency-Key':receipt.key}})
  assert.equal(replay.status(),202);assert.equal((await replay.json()).job_id,result.job_id)
  fs.writeFileSync(root+'/result.json',JSON.stringify(result,null,2));results.push({case:'real-draft',status:202,result:result.status,job_id:result.job_id,prompts,restarted,resumed})
  await live.getByRole('article',{name:'Take '+result.job_id}).getByRole('link',{name:'Download video',exact:true}).waitFor()
  await capture(live,'completed-desktop')
 }
 if(process.env.MOTION_GENERATE_REPLAY==='1'){
  const receipt=JSON.parse(fs.readFileSync(root+'/accepted.json')),result=JSON.parse(fs.readFileSync(root+'/result.json'))
  assert.equal(result.status,'completed')
  const state=await json(await live.request.get(base+'/api/v1/motion-director/plans/'+result.shot.request.plan_id+'/preview'))
  const replay=await open({id:result.shot.request.plan_id,shot:result.shot,preview:state},true,receipt);await ready(replay)
  await replay.route('**/api/v1/motion-director/jobs',async route=>{
   if(route.request().method()==='POST'){
    if(route.request().headers()['idempotency-key']!==receipt.key||JSON.stringify(route.request().postDataJSON())!==JSON.stringify(receipt.request)){
     // Compare parsed fields without relying on their serialized key order.
     assert.equal(route.request().headers()['idempotency-key'],receipt.key);assert.deepEqual(route.request().postDataJSON(),receipt.request)
    }
   }
   await route.continue()
  })
  const response=replay.waitForResponse(r=>r.url().endsWith('/jobs')&&r.request().method()==='POST')
  await generate(replay).evaluate(b=>{b.click();b.click()})
  const acknowledged=await response;assert.equal(acknowledged.status(),202);assert.equal((await acknowledged.json()).job_id,result.job_id)
  await final(replay).getByText(/Video submitted/).waitFor();assert(await generate(replay).isDisabled())
  await replay.reload();await final(replay).getByText(/Video submitted/).waitFor();assert(await generate(replay).isDisabled())
  await capture(replay,'restored-ack-mobile');await replay.close();results.push({case:'restored-lost-ack-mobile',status:202,job_id:result.job_id})
 }
 save();assert(!results.some(r=>r.page_error),JSON.stringify(results));console.log('PASS deployed Generate contract, rebuild/reuse/revision/restoration/quality and inline server errors'+(process.env.MOTION_GENERATE_LIVE==='1'?', real interrupted draft':'')+(process.env.MOTION_GENERATE_REPLAY==='1'?', mobile acknowledgement replay':''))
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{
 if(guard)clearInterval(guard)
 if(heldPreview)fs.renameSync(heldPreview[1],heldPreview[0])
 if(redisStopped)cp.execFileSync('docker',['start','ai-redis'])
 if(browser)await browser.close()
 save()
})
