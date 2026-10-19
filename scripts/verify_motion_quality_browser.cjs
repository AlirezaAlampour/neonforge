// Replay the REAL benchmark plan/preview into the live UI. Edit POSTs are
// intercepted to verify wiring without another subscription or GPU invocation.
const assert=require('node:assert/strict'), fs=require('node:fs'), crypto=require('node:crypto')
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/home/xxfactionsxx/desk-space-program/node_modules/playwright')
const base=process.env.MOTION_TEST_URL||'http://100.103.129.82:3000'
const state='.motion-runtime/quality/', manifest=JSON.parse(fs.readFileSync(state+'comparison-manifest.json'))
const sceneId=JSON.parse(fs.readFileSync(state+'improved-preview.json')).id
const plan=JSON.parse(fs.readFileSync(manifest.folder+'/D-plan.json'))
plan.request.plan_id=sceneId
const {reference_ids,...request}=plan.request
const draft={...request,references:reference_ids.map((id,i)=>({id,name:`Running reference ${i+1}`}))}
const prefix=`/api/v1/outputs/motion_director/scenes/${sceneId}/`
const preview={id:sceneId,status:'completed',message:'Blender preview ready',preview_url:prefix+'preview.mp4',export_url:prefix+'grok-export.zip'}
;(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']})
 try{
  const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true})
  const errors=[];page.on('pageerror',e=>errors.push(String(e)))
  await page.addInitScript(({draft,plan,preview})=>{
   localStorage.setItem('neonforge-motion-director-v0.1',JSON.stringify(draft))
   localStorage.setItem('neonforge-motion-director-v0.1-preview',JSON.stringify({plan,preview}))
  },{draft,plan,preview})
  let edit
  await page.route('**/motion-director/plans/*/revise',async route=>{
   edit=route.request().postDataJSON().instruction
   const revised=structuredClone(plan);revised.request.plan_id=crypto.randomUUID()
   revised.h3.summary='Maintain the same rear follow shot with a slightly wider composition.'
   await route.fulfill({json:revised})
  })
  await page.route('**/motion-director/plans/*/preview',route=>route.fulfill({json:preview}))
  await page.goto(base+'/motion-director')
  await page.getByLabel('Blender 3D preview',{exact:true}).waitFor()
  await page.getByLabel('Blender 3D preview').evaluate(v=>new Promise((resolve,reject)=>{
   if(v.readyState>=1)return resolve();v.onloadedmetadata=resolve;v.onerror=()=>reject(Error('Real preview failed to decode'))
  }))
  assert.deepEqual(await page.getByLabel('Blender 3D preview').evaluate(v=>[v.videoWidth,v.videoHeight,v.duration]),[768,1344,10])
  assert.equal(await page.getByLabel('Cinematic prompt').inputValue(),'Character running and camera following.')
  await page.getByText('AI Shot Plan · optional review & edit',{exact:true}).click()
  assert.equal(await page.getByLabel('Edit AI Shot Plan').inputValue(),plan.h3.summary)
  await page.getByLabel('Edit AI Shot Plan').fill('Maintain the same rear follow shot with a slightly wider composition.')
  const editedResponse=page.waitForResponse(r=>r.url().endsWith('/revise'))
  await page.getByRole('button',{name:'Apply edited plan & preview',exact:true}).click()
  await editedResponse
  assert.match(edit,/slightly wider composition/)
  assert.equal(await page.getByLabel('Cinematic prompt').inputValue(),plan.request.prompt)
  await page.getByText('Advanced · settings & shot plan',{exact:true}).click()
  assert.equal(await page.getByLabel('Generation quality',{exact:true}).inputValue(),'native')
  await page.getByText('Advanced · settings & shot plan',{exact:true}).click()
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:manifest.folder+'/ui-mobile.png',fullPage:true})
  await page.setViewportSize({width:1440,height:1000})
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:manifest.folder+'/ui-desktop.png',fullPage:true})
  await page.goto(base+`/api/v1/outputs/motion_director/scenes/${manifest.id}/comparison.html`)
  assert.equal(await page.locator('video').count(),4)
  await page.locator('video').last().evaluate(v=>new Promise((resolve,reject)=>{
   if(v.readyState>=1)return resolve();v.onloadedmetadata=resolve;v.onerror=()=>reject(Error('Native H3 output failed to decode'))
  }))
  assert.deepEqual(await page.locator('video').last().evaluate(v=>[v.videoWidth,v.videoHeight]),[768,1344])
  await page.getByRole('button',{name:'Play all from start',exact:true}).click()
  await page.waitForTimeout(1200)
  assert.equal(await page.locator('video').last().evaluate(v=>v.currentTime>0),true)
  await page.screenshot({path:manifest.folder+'/comparison-browser.png',fullPage:true})
  assert.deepEqual(errors,[])
  console.log('PASS: real 768×1344 Blender/H3 decoding, optional editable AI plan (replayed edit), unchanged simple prompt, mobile/desktop layouts, A/B/C/D comparison playback; no new inference calls')
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1})
