// Replay the separately observed real Opus usage and real cached motion video.
// UI coverage only: no planning or inference request is submitted.
const assert=require('node:assert/strict'),fs=require('node:fs')
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/home/xxfactionsxx/desk-space-program/node_modules/playwright')
const base=process.env.MOTION_TEST_URL||'http://100.103.129.82:3000'
const manifest=JSON.parse(fs.readFileSync('.motion-runtime/iteration/manifest.json'))
const cpu=JSON.parse(fs.readFileSync(manifest.folder+'/cpu.json'))
const plan=JSON.parse(fs.readFileSync('.motion-runtime/iteration/observed-opus-plan.json'))
plan.width=768;plan.height=1344;plan.request.quality='native';plan.request.plan_id=cpu.after.id
const {reference_ids,...request}=plan.request
const draft={...request,references:reference_ids.map(id=>({id,name:'Running reference'}))}
const preview={...cpu.after,preview_url:`/api/v1/outputs/motion_director/scenes/${cpu.after.id}/preview.mp4`}
;(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']})
 try{
  const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true})
  const errors=[];page.on('pageerror',e=>errors.push(String(e)))
  await page.addInitScript(({draft,plan,preview})=>{
   localStorage.setItem('neonforge-motion-director-v0.1',JSON.stringify(draft))
   localStorage.setItem('neonforge-motion-director-v0.1-preview',JSON.stringify({plan,preview}))
  },{draft,plan,preview})
  await page.goto(base+'/motion-director')
  const video=page.getByLabel('Blender 3D preview',{exact:true})
  await video.evaluate(v=>new Promise((resolve,reject)=>{if(v.readyState>=1)return resolve();v.onloadedmetadata=resolve;v.onerror=()=>reject(Error('Motion preview failed to decode'))}))
  assert.deepEqual(await video.evaluate(v=>[v.videoWidth,v.videoHeight,v.duration]),[384,672,10])
  await page.getByText('Motion preview 384 × 672. Final generation 768 × 1344.',{exact:true}).waitFor()
  assert.equal(await page.getByText(/Recorded tokens:/).isVisible(),false)
  await page.getByText('Advanced · settings & shot plan',{exact:true}).click()
  await page.getByText('Usage · optional',{exact:true}).first().click()
  await page.getByText('Recorded tokens: 2 uncached input · 0 cache read · 7,932 cache creation · 3,519 output.',{exact:true}).waitFor()
  await page.getByText(/Internal provider request\/retry totals are not exposed/).first().waitFor()
  assert.equal(await page.getByLabel('Cinematic prompt').inputValue(),'Character running and camera following.')
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:manifest.folder+'/usage-mobile.png',fullPage:true})
  await page.setViewportSize({width:1440,height:1000})
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:manifest.folder+'/usage-desktop.png',fullPage:true})
  await page.goto(base+`/api/v1/outputs/motion_director/scenes/${manifest.id}/comparison.html`)
  const native=page.locator('video[src="before-native.mp4"]')
  if(await native.count()){
   await native.evaluate(v=>new Promise((resolve,reject)=>{if(v.readyState>=1)return resolve();v.onloadedmetadata=resolve;v.onerror=()=>reject(Error('Native after clip failed to decode'))}))
   assert.deepEqual(await native.evaluate(v=>[v.videoWidth,v.videoHeight]),[768,1344])
   await page.getByRole('button',{name:'Play H3 comparisons from start'}).click()
   await page.waitForTimeout(1200)
   assert.equal(await native.evaluate(v=>v.currentTime>0),true)
  }
  await page.screenshot({path:manifest.folder+'/comparison-browser.png',fullPage:true})
  assert.deepEqual(errors,[])
  console.log('PASS: optional observed usage, honest preview/final sizes, available comparison clip decoding/playback, mobile/desktop; no new Opus or H3 calls')
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1})
