// One real reviewed fight draft. Existing acceptance receipt prevents resubmission.
const fs=require('node:fs'),assert=require('node:assert/strict'),cp=require('node:child_process')
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/home/xxfactionsxx/desk-space-program/node_modules/playwright')
const root='.motion-runtime/daidyra',base=process.env.MOTION_TEST_URL||'http://100.103.129.82:3000',key='neonforge-motion-director-v0.1'
const variant=process.env.DAIDYRA_NO_GUIDE==='1'?'candidate':process.env.DAIDYRA_CONTROL==='1'?'control':'fight'
const shot=JSON.parse(fs.readFileSync(root+(variant==='control'?'/control-plan.json':variant==='candidate'?'/candidate-plan.json':'/study-plan.json'))),sleep=ms=>new Promise(r=>setTimeout(r,ms))
let browser,timer,currentPrompt=null,violation=null;const samples=[]
function sample(){
 const kb=Number(fs.readFileSync('/proc/meminfo','utf8').match(/^MemAvailable:\s+(\d+)/m)[1])
 const [temperature,clock,utilization]=cp.execFileSync('nvidia-smi',['--query-gpu=temperature.gpu,clocks.gr,utilization.gpu','--format=csv,noheader,nounits'],{encoding:'utf8'}).trim().split(',').map(Number)
 samples.push({at:new Date().toISOString(),available_kb:kb,available_gib:kb/1048576,temperature,clock,utilization});fs.writeFileSync(root+'/'+variant+'-resources.json',JSON.stringify(samples,null,2))
 if(temperature>=70||kb/1048576<40||clock>2000){violation='Resource guard violation';if(currentPrompt)fetch('http://127.0.0.1:8189/interrupt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt_id:currentPrompt})}).catch(()=>{})}
}
;(async()=>{
 let accepted=fs.existsSync(root+'/'+variant+'-accepted.json')?JSON.parse(fs.readFileSync(root+'/'+variant+'-accepted.json')):null
 if(!accepted){
  assert.equal(process.env.DAIDYRA_RENDER,'1','Set DAIDYRA_RENDER=1 to authorize the one real draft')
  const cap=await (await fetch(base+'/api/v1/motion-director/capabilities?quality=draft')).json()
  assert(cap.h3.render_ready&&cap.resources.memory.available_gb>=90&&cap.resources.gpu.temperature_c<50&&cap.resources.gpu.cap_active)
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});const p=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true})
  await p.addInitScript(({key,shot})=>{const {reference_ids,...request}=shot.request;localStorage.setItem(key,JSON.stringify({...request,references:reference_ids.map(id=>({id,name:'Original fight reference'}))}));localStorage.setItem(key+'-preview',JSON.stringify({plan:shot,preview:{id:shot.request.plan_id,status:'completed'}}))},{key,shot})
  await p.goto(base+'/motion-director');const generate=p.getByLabel('Final generation',{exact:true}).getByRole('button',{name:'Generate video',exact:true})
  await p.waitForFunction(()=>document.querySelector('[aria-label="Final generation"] button')?.disabled===false,{},{timeout:60000})
  const video=p.getByLabel('Blender 3D preview',{exact:true});await video.evaluate(v=>new Promise((resolve,reject)=>{if(v.readyState>=1)return resolve();v.onloadedmetadata=resolve;v.onerror=()=>reject(Error('Blender preview failed to decode'))}));assert.equal(await video.evaluate(v=>v.duration),5)
  await video.scrollIntoViewIfNeeded();await p.screenshot({path:root+'/'+variant+'-reviewed-phone.png'})
  sample();assert(!violation);timer=setInterval(sample,2000)
  const pending=p.waitForResponse(r=>r.url().endsWith('/jobs')&&r.request().method()==='POST');await generate.evaluate(b=>{b.click();b.click()});const response=await pending
  assert.equal(response.status(),202,await response.text());accepted={job:await response.json(),key:response.request().headers()['idempotency-key'],request:response.request().postDataJSON()}
  fs.writeFileSync(root+'/'+variant+'-accepted.json',JSON.stringify(accepted,null,2));console.log('ACCEPTED',accepted.job.job_id)
  await browser.close();browser=null
 }else{sample();timer=setInterval(sample,2000)}
 const began=Date.now();let last=''
 while(Date.now()-began<12*60000){
  assert(!violation,violation)
  const response=await fetch(base+'/api/v1/motion-director/jobs/'+accepted.job.job_id);assert(response.ok);const job=await response.json();currentPrompt=job.prompt_id||currentPrompt
  if(job.message!==last){console.log(job.status,job.message);last=job.message}
  fs.writeFileSync(root+'/'+variant+'-result.json',JSON.stringify(job,null,2))
  if(job.status==='completed'){
   const media=await fetch(base+job.preview_url);assert(media.ok);fs.writeFileSync(root+'/'+variant+'-h3.mp4',Buffer.from(await media.arrayBuffer()));console.log('COMPLETED',job.job_id);return
  }
  assert(!['failed','blocked','interrupted','cancelled','needs_inspection'].includes(job.status),JSON.stringify(job));await sleep(3000)
 }
 throw Error('Accepted job remains active; reconcile the receipt instead of resubmitting')
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(timer)clearInterval(timer);if(browser)await browser.close()})
