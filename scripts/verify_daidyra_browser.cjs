// Live desktop/phone acceptance. Real API/media only; no mocked responses or GPU submissions.
const fs=require('node:fs'),assert=require('node:assert/strict')
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/home/xxfactionsxx/desk-space-program/node_modules/playwright')
const base=process.env.MOTION_TEST_URL||'http://100.103.129.82:3000',root=process.env.DAIDYRA_EVIDENCE||'.motion-runtime/daidyra'
const key='neonforge-motion-director-v0.1',results=[],trace=[]
const fixtures=JSON.parse(fs.readFileSync('.motion-runtime/generate-fix/plans-before.json'))
const audit='.motion-runtime/claude-verification.jsonl',before=fs.existsSync(audit)?fs.readFileSync(audit,'utf8'):''
const save=()=>{fs.writeFileSync(root+'/browser-results.json',JSON.stringify(results,null,2));fs.writeFileSync(root+'/browser-trace.json',JSON.stringify(trace,null,2))}
const json=async r=>{assert(r.ok(),r.status()+' '+await r.text());return r.json()}
let browser
;(async()=>{
 browser=await chromium.launch({headless:true,args:['--no-sandbox']})
 async function page(mobile,fixture){
  const p=await browser.newPage({viewport:{width:mobile?390:1440,height:mobile?844:1000},isMobile:mobile,hasTouch:mobile,timezoneId:'America/Los_Angeles'})
  p.on('pageerror',e=>{results.push({error:String(e)});save()})
  p.on('response',async r=>{if(r.request().method()==='POST'&&r.url().includes('/motion-director/')){trace.push({url:r.url(),status:r.status(),request:r.request().postDataJSON(),body:await r.text().catch(()=>'<closed>')});save()}})
  if(fixture){const plan=fixture.shot;await p.addInitScript(({key,plan})=>{
   if(sessionStorage.getItem('daidyra-seeded'))return
   const {reference_ids,...request}=plan.request
   localStorage.setItem(key,JSON.stringify({...request,references:reference_ids.map(id=>({id,name:'Original reference'}))}))
   localStorage.setItem(key+'-preview',JSON.stringify({plan,preview:{id:plan.request.plan_id,status:'completed'}}))
   sessionStorage.setItem('daidyra-seeded','1')
  },{key,plan})}
  await p.goto(base+'/motion-director');await p.getByLabel('Creation workflow').waitFor()
  assert.equal(await p.title(),'Daidyra');assert.equal(await p.getByRole('heading',{name:'Motion Director',exact:true}).count(),1)
  assert.equal(await p.locator('aside').isVisible(),!mobile)
  assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
  return p
 }
 for(const mobile of [false,true]){
  const p=await page(mobile)
  await p.getByLabel('Upload reference images',{exact:true}).setInputFiles(root+'/reference.png')
  await p.getByAltText('Selected scene reference').waitFor()
  await p.getByLabel('Describe action',{exact:true}).fill('the characters start fighting')
  const status=p.getByLabel('Claude planning availability');await status.waitFor()
  const cap=await (await fetch(base+'/api/v1/motion-director/capabilities')).json()
  if(Date.parse(cap.planner.next_check_at)>Date.now())assert(await status.getByRole('button',{name:'Verify Claude subscription'}).isDisabled())
  assert(await status.getByText(/Saved|saved|unverified|expired|HTTP|Verify/).count())
  await p.getByLabel('Upload reference images',{exact:true}).setInputFiles({name:'too-large.png',mimeType:'image/png',buffer:Buffer.alloc(21*1024*1024)})
  const alert=p.getByRole('alert').filter({hasText:'too-large.png exceeds 20 MB.'});await alert.waitFor();await alert.scrollIntoViewIfNeeded()
  assert(await p.getByAltText('Selected scene reference').isVisible());await p.screenshot({path:root+'/inline-upload-'+(mobile?'phone':'desktop')+'.png'})
  await p.locator('main').evaluate(e=>e.scrollTop=0);await p.screenshot({path:root+'/deployed-'+(mobile?'phone':'desktop')+'.png'})
  results.push({case:'upload-description-planning-status',planner_state:cap.planner.verification_state,mobile,overflow:false,inline_upload_error:true});await p.close()
 }
 const legacy=fixtures.find(r=>r.id.startsWith('2794503c'));assert(legacy)
 const p=await page(true,legacy)
 const generate=p.getByLabel('Final generation',{exact:true}).getByRole('button',{name:'Generate video',exact:true})
 await generate.waitFor({state:'visible'});await p.getByLabel('Final generation',{exact:true}).getByText('Saved scene and preview verified. Ready to generate.',{exact:true}).waitFor({timeout:90000})
 assert(trace.some(t=>t.url.endsWith('/rebuild')&&t.status===200))
 let stored=JSON.parse(await p.evaluate(key=>localStorage.getItem(key+'-preview'),key));assert.notEqual(stored.plan.request.plan_id,legacy.id)
 await p.getByText('Advanced · settings & shot plan',{exact:true}).click()
 await p.getByLabel('Generation quality',{exact:true}).selectOption('native')
 await p.getByLabel('Final generation',{exact:true}).getByText('Saved scene and preview verified. Ready to generate.',{exact:true}).waitFor({timeout:90000})
 stored=JSON.parse(await p.evaluate(key=>localStorage.getItem(key+'-preview'),key));assert.equal(stored.plan.width,768)
 await p.getByLabel('Generation quality',{exact:true}).selectOption('draft')
 await p.getByLabel('Final generation',{exact:true}).getByText('Saved scene and preview verified. Ready to generate.',{exact:true}).waitFor({timeout:90000})
 await p.getByLabel('Correct the scene',{exact:true}).fill('Set the camera height to 2.1 metres.')
 const revisedResponse=p.waitForResponse(r=>r.url().endsWith('/revise')&&r.request().method()==='POST')
 await p.getByRole('button',{name:'Apply correction & preview',exact:true}).click();const revised=await json(await revisedResponse)
 assert.equal(revised.planning_usage.new_claude_invocations,0)
 await p.getByLabel('Final generation',{exact:true}).getByText('Saved scene and preview verified. Ready to generate.',{exact:true}).waitFor({timeout:90000})
 const video=p.getByLabel('Blender 3D preview',{exact:true});await video.evaluate(v=>new Promise((resolve,reject)=>{if(v.readyState>=1)return resolve();v.onloadedmetadata=resolve;v.onerror=()=>reject(Error('Real preview failed to decode'))}))
 assert.deepEqual(await video.evaluate(v=>[v.videoWidth,v.videoHeight]),[384,672]);await video.evaluate(v=>v.play());await p.waitForTimeout(400);assert(await video.evaluate(v=>v.currentTime>0));await video.evaluate(v=>v.pause())
 await p.reload();await generate.waitFor();await p.getByLabel('Final generation',{exact:true}).getByText('Saved scene and preview verified. Ready to generate.',{exact:true}).waitFor({timeout:90000})
 await video.scrollIntoViewIfNeeded();await p.screenshot({path:root+'/saved-scene-phone.png'})
 results.push({case:'automatic-reconstruction-quality-camera-reload',ready:true,new_claude_invocations:0});await p.close()
 const incompatible=fixtures.find(r=>r.id.startsWith('c5778687'));const q=await page(false,incompatible)
 await q.getByLabel('Final generation',{exact:true}).getByText(/different prompt or reference set/).waitFor()
 assert(await q.getByLabel('Final generation',{exact:true}).getByRole('button',{name:'Generate video',exact:true}).isDisabled())
 results.push({case:'incompatible-scene',generation_disabled:true,reason_visible:true});await q.close()
 const cap=await (await fetch(base+'/api/v1/motion-director/capabilities')).json()
 if(Date.parse(cap.planner.next_check_at)>Date.now()){const response=await fetch(base+'/api/v1/motion-director/planner/verify',{method:'POST'});assert(response.ok);const verified=await response.json();assert.equal(verified.available,Boolean(cap.planner.opus_available))}
 assert.equal(fs.readFileSync(audit,'utf8'),before,'Passive polling or cooldown retry contacted Claude')
 assert.equal(trace.filter(t=>t.url.endsWith('/plan')).length,0)
 assert(!results.some(r=>r.error));save();console.log('PASS',JSON.stringify(results))
})().catch(e=>{console.error(e);results.push({failure:String(e)});save();process.exitCode=1}).finally(async()=>{if(browser)await browser.close()})
