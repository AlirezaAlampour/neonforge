// Real private UI acceptance; uses installed Playwright, never installs packages.
// uv lock --check && uv run --locked --no-sync node scripts/verify_motion_3d_browser.cjs
const assert=require('node:assert/strict'),fs=require('node:fs')
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/home/xxfactionsxx/desk-space-program/node_modules/playwright')
const base=process.env.MOTION_TEST_URL||'http://100.103.129.82:3000'
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms))
;(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']})
 const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true})
 const errors=[];page.on('pageerror',e=>errors.push(String(e)))
 try{
  await page.goto(base+'/motion-director');await page.getByRole('heading',{name:/Motion Director/}).waitFor()
  assert.equal(Math.round(await page.locator('aside').evaluate(x=>x.getBoundingClientRect().width)),56)
  for(let i=0;i<12;i++){
   const cap=await (await page.request.get(base+'/api/v1/motion-director/capabilities')).json()
   if(cap.planner.opus_available)break
   console.log(cap.planner.reason)
   if(i===11)throw Error('Existing subscription verification did not recover; no model call was made')
   await wait(15000)
  }
  await page.getByLabel('Upload reference images').setInputFiles(process.env.MOTION_REFERENCE||'/tmp/motion-actual-reference.png')
  await page.getByAltText('Selected scene reference').waitFor()
  await page.getByLabel('Cinematic prompt').fill('The main character runs forward at 3 metres per second through an approximate version of this platform scene. A third-person camera follows from BEHIND at a fixed 4 metre distance and 2 metre height, looking ahead along the path. Keep all other objects and bystanders stationary. Use the reusable running animation synchronized with the distance travelled.')
  await page.getByLabel('Duration',{exact:true}).selectOption('10')
  const planResponse=page.waitForResponse(r=>r.url().endsWith('/motion-director/plan'),{timeout:220000})
  await page.getByRole('button',{name:'Build 3D preview',exact:true}).click()
  const response=await planResponse;assert.equal(response.status(),200,await response.text())
  await page.getByLabel('Blender 3D preview',{exact:true}).waitFor({timeout:120000})
  await page.getByLabel('Blender 3D preview',{exact:true}).evaluate(v=>new Promise((resolve,reject)=>{if(v.readyState>=1)return resolve();v.onloadedmetadata=resolve;v.onerror=reject}))
  assert.equal(await page.getByLabel('Blender 3D preview').evaluate(v=>v.duration),10)
  let saved=JSON.parse(await page.evaluate(()=>localStorage.getItem('neonforge-motion-director-v0.1-preview')))
  fs.writeFileSync('.motion-runtime/browser-3d-before-revision.json',JSON.stringify(saved,null,2))
  console.log('PASS real Opus → Blender preview',saved.plan.request.plan_id)
  await page.screenshot({path:'/tmp/motion-after-mobile.png',fullPage:true})
  const before=saved
  await page.getByLabel('Correct the scene',{exact:true}).fill('Move the follow camera farther back to exactly 6 metres behind the runner. Keep the camera height at 2 metres. Preserve the running path, animation, every object transform, and all other scene settings unchanged.')
  const revisedResponse=page.waitForResponse(r=>r.url().endsWith('/revise'),{timeout:220000})
  await page.getByRole('button',{name:'Apply correction & preview',exact:true}).click()
  const revised=await revisedResponse;assert.equal(revised.status(),200,await revised.text())
  await page.waitForFunction(id=>{const s=JSON.parse(localStorage.getItem('neonforge-motion-director-v0.1-preview')||'null');return s?.preview?.status==='completed'&&s?.plan?.request?.plan_id!==id},saved.plan.request.plan_id,{timeout:120000})
  saved=JSON.parse(await page.evaluate(()=>localStorage.getItem('neonforge-motion-director-v0.1-preview')))
  assert.equal(saved.plan.scene.camera.distance,6)
  assert.deepEqual(saved.plan.scene.entities,before.plan.scene.entities)
  fs.writeFileSync('.motion-runtime/browser-3d-acceptance.json',JSON.stringify(saved,null,2))
  console.log('PASS natural-language camera correction, preserved entities',saved.plan.request.plan_id)
  const exported=await page.request.get(base+saved.preview.export_url);assert.equal(exported.status(),200)
  fs.writeFileSync('.motion-runtime/browser-grok-export.zip',await exported.body())
  await page.reload();await page.getByLabel('Blender 3D preview',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Build 3D preview',exact:true}).count(),0)
  await page.screenshot({path:'/tmp/motion-after-revised-mobile.png',fullPage:true})
  if(process.env.MOTION_3D_RENDER==='1'){
   await page.getByRole('button',{name:'Generate video',exact:true}).click()
   console.log('Submitted one real H3 take from the reviewed Blender plan')
   await page.getByRole('button',{name:'Compare with Blender',exact:true}).first().waitFor({timeout:600000})
   await page.getByRole('button',{name:'Compare with Blender',exact:true}).first().click()
   await page.getByRole('button',{name:'Play reference & final',exact:true}).click()
   await wait(2000)
  }
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.setViewportSize({width:1440,height:1000});await wait(400);assert.equal(Math.round(await page.locator('aside').evaluate(x=>x.getBoundingClientRect().width)),220);await page.screenshot({path:'/tmp/motion-after-desktop.png',fullPage:true})
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  assert.deepEqual(errors,[])
  console.log('PASS export, persisted scene, mobile and desktop widths, decoded 10s preview, no page errors')
 }catch(e){await page.screenshot({path:'/tmp/motion-live-error.png',fullPage:true});throw e}
 finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1})
