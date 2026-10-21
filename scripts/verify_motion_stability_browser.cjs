// Real deployed UI, cached planning/Blender artifacts, optional ONE real draft.
// No package installs. MOTION_REAL_DRAFT=1 explicitly enables H3 acceptance.
const assert=require('node:assert/strict'),fs=require('node:fs'),cp=require('node:child_process')
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/home/xxfactionsxx/desk-space-program/node_modules/playwright')
const base=process.env.MOTION_TEST_URL||'http://100.103.129.82:3000'
const folder='.motion-runtime/stabilization', key='neonforge-motion-director-v0.1'
;(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']})
 let redisStopped=false
 try{
  const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true,timezoneId:'America/Los_Angeles'})
  const errors=[];page.on('pageerror',e=>errors.push(String(e)))
  const jobs=(await(await page.request.get(base+'/api/v1/motion-director/jobs')).json()).items
  const original=jobs.find(j=>j.shot.scene&&j.shot.production_version==='iteration-v1')
  assert(original,'A real cached scene is required')
  const sourcePlan=JSON.parse(fs.readFileSync('.motion-runtime/stabilization/cached-valid-plan.json'))
  const {reference_ids,...request}=sourcePlan.request
  request.quality='draft';request.plan_id=undefined
  const draft={...request,references:reference_ids.map((id,i)=>({id,name:`Saved reference ${i+1}`}))}
  await page.addInitScript(({key,draft})=>{localStorage.setItem(key,JSON.stringify(draft));localStorage.removeItem(key+'-preview')},{key,draft})
  await page.goto(base+'/motion-director')
  await page.getByRole('group',{name:'Filter takes'}).waitFor()
  await page.getByRole('button',{name:/Needs attention/}).click()
  for(const job of jobs.filter(j=>j.status==='interrupted')){
   const take=page.getByRole('article',{name:`Take ${job.job_id}`})
   await take.getByRole('button',{name:'Retry remaining clips'}).waitFor()
   assert.equal(await take.getByText('interrupted',{exact:true}).isVisible(),true)
  }
  assert.equal(await page.locator('.animate-pulse').count(),0)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:folder+'/attention-mobile.png',fullPage:true})
  const planned=page.waitForResponse(r=>r.url().endsWith('/motion-director/plan')&&r.request().method()==='POST')
  await page.getByRole('button',{name:'Build 3D preview',exact:true}).click()
  const planResponse=await planned;assert.equal(planResponse.status(),200,await planResponse.text())
  const plan=await planResponse.json();assert.equal(plan.planning_usage.new_claude_invocations,0)
  fs.writeFileSync(folder+'/draft-plan.json',JSON.stringify(plan,null,2))
  const preview=page.getByLabel('Blender 3D preview',{exact:true})
  await preview.waitFor({timeout:120000})
  await preview.evaluate(v=>new Promise((resolve,reject)=>{if(v.readyState>=1)return resolve();v.onloadedmetadata=resolve;v.onerror=()=>reject(Error('Blender decode failed'))}))
  assert.deepEqual(await preview.evaluate(v=>[v.videoWidth,v.videoHeight]),[384,672])
  assert.equal(await page.getByLabel('Cinematic prompt').inputValue(),sourcePlan.request.prompt)
  assert.equal(await page.locator('.animate-pulse').count(),0)
  await page.screenshot({path:folder+'/preview-mobile.png',fullPage:true})
  let final=jobs.find(j=>j.status==='completed'&&j.blender_preview_url)
  if(process.env.MOTION_REAL_DRAFT==='1'){
   assert(!fs.existsSync(folder+'/draft-accepted.json'),'A draft was already accepted. Reconcile that job instead of submitting another.')
   const generate=page.getByRole('button',{name:'Generate video',exact:true})
   await generate.waitFor();assert.equal(await generate.isEnabled(),true)
   if(process.env.MOTION_REDIS_OUTAGE==='1'){
    cp.execFileSync('docker',['stop','--time','10','ai-redis']);redisStopped=true
   }
   const responsePromise=page.waitForResponse(r=>r.url().endsWith('/motion-director/jobs')&&r.request().method()==='POST')
   await generate.click()
   const response=await responsePromise;assert.equal(response.status(),202,await response.text())
   assert(response.request().headers()['idempotency-key'])
   const accepted=await response.json()
   fs.writeFileSync(folder+'/draft-accepted.json',JSON.stringify(accepted,null,2))
   if(redisStopped){
    // The durable job acknowledgement and visible status work while Redis is down.
    const durable=await(await page.request.get(base+'/jobs/'+accepted.job_id)).json()
    assert.equal(durable.job_id,accepted.job_id)
    cp.execFileSync('docker',['start','ai-redis']);redisStopped=false
   }
   const started=Date.now(),samples=[]
   while(Date.now()-started<12*60000){
    const row=await(await page.request.get(base+'/api/v1/motion-director/jobs/'+accepted.job_id)).json()
    const cap=await(await page.request.get(base+'/api/v1/motion-director/capabilities?quality=draft')).json()
    samples.push({at:new Date().toISOString(),status:row.status,completed:row.completed_segments,gpu:cap.resources?.gpu,memory:cap.resources?.memory})
    fs.writeFileSync(folder+'/draft-samples.json',JSON.stringify(samples,null,2))
    console.log(row.status,row.message,cap.resources?.gpu?.temperature_c)
    if(['completed','failed','blocked','needs_inspection','interrupted','cancelled'].includes(row.status)){final=row;break}
    await page.waitForTimeout(5000)
   }
   fs.writeFileSync(folder+'/draft-result.json',JSON.stringify(final,null,2))
   assert.equal(final.job_id,accepted.job_id);assert.equal(final.status,'completed',JSON.stringify(final))
   assert.equal(final.generation_claude_invocations,0)
   assert.equal(final.completed_segments,final.total_segments)
   assert(samples.every(s=>(s.gpu?.temperature_c||0)<70))
   assert(samples.every(s=>(s.gpu?.clock_mhz||0)<=2000))
  }
  await page.getByRole('button',{name:/Ready ·/}).click()
  const take=page.getByRole('article',{name:`Take ${final.job_id}`})
  await take.getByRole('button',{name:'Compare with Blender'}).click()
  await page.getByRole('button',{name:'Play reference & final'}).click()
  const comparison=page.getByRole('heading',{name:'Blender vs final clip'}).locator('..').locator('..')
  const videos=comparison.locator('video')
  await page.waitForTimeout(1200)
  for(const video of await videos.all())assert.equal(await video.evaluate(v=>v.currentTime>0&&v.videoWidth>0),true)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:folder+'/after-mobile.png',fullPage:true})
  await page.setViewportSize({width:1440,height:1000})
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:folder+'/after-desktop.png',fullPage:true})
  await take.getByRole('button',{name:'Reuse shot'}).click()
  assert.equal(await page.getByLabel('Cinematic prompt').inputValue(),final.shot.request.prompt)
  await page.getByLabel('Blender 3D preview',{exact:true}).waitFor()
  // Read-only compatibility checks against every current creator route.
  for(const route of ['voiceover','video','character','avatar','lipsync','status']){
   const response=await page.goto(base+'/'+route);assert.equal(response.status(),200)
  }
  for(const route of ['studio','voice','broll'])assert.equal((await page.goto(base+'/'+route)).status(),404)
  assert.deepEqual(errors,[])
  console.log('PASS deployed mobile/desktop, real cached plan/Blender, take controls, comparison playback, reuse, route compatibility'+(process.env.MOTION_REAL_DRAFT==='1'?', real H3 draft':''))
 }finally{if(redisStopped)cp.execFileSync('docker',['start','ai-redis']);await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1})
