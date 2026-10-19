'use client'

import { useEffect, useRef, useState } from 'react'
import { Clapperboard, Upload } from 'lucide-react'
import { AnnotationCanvas } from '@/components/motion-annotations'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { motionApi, downloadMotionJSON, type Annotation, type Capabilities, type MotionJob, type ShotRequest, type ShotSpec, type ScenePreview, type Tool } from '@/lib/motion-director'

type Reference = { id: string; name: string }
type Draft = Omit<ShotRequest, 'reference_ids'> & { references: Reference[] }
const KEY = 'neonforge-motion-director-v0.1'
const initial: Draft = { references: [], annotations: [], prompt: '', mode: 'auto', preset: 'professional', duration: 5, orientation: 'vertical', workflow: 'reference', seed: 42, director: 'opus', engine: 'h3', pipeline: '3d' }
const tools: Tool[] = ['arrow', 'path', 'orbit', 'waypoint', 'subject', 'entry', 'exit']
const toolNames: Record<Tool, string> = { arrow: 'Camera arrow', path: 'Camera path', orbit: 'Orbit around', waypoint: 'Camera stop', subject: 'Mark subject', entry: 'Enters here', exit: 'Exits here' }
const toolHelp: Record<Tool, string> = { arrow: 'Drag from the camera’s starting point toward its destination.', path: 'Draw the path you want the camera to follow.', orbit: 'Drag from the subject outwards to draw an orbit.', waypoint: 'Tap each camera stop in order.', subject: 'Tap the person or object to keep in focus.', entry: 'Tap where the subject enters the scene.', exit: 'Tap where the subject leaves the scene.' }
const selectClass = 'min-h-11 w-full rounded-md border border-white/15 bg-card px-3 text-sm'
const terminal = (job: MotionJob) => ['completed', 'failed'].includes(job.status)
const payload = ({ references, ...rest }: Draft): ShotRequest => ({ ...rest, reference_ids: references.map(r => r.id) })

export default function MotionDirectorPage() {
  const [draft, setDraft] = useState<Draft>(initial)
  const [restored, setRestored] = useState(false)
  const [selected, setSelected] = useState('')
  const [tool, setTool] = useState<Tool>('arrow')
  const [label, setLabel] = useState('')
  const [plan, setPlan] = useState<ShotSpec | null>(null)
  const [preview, setPreview] = useState<ScenePreview | null>(null)
  const [correction, setCorrection] = useState('')
  const [sceneCompare, setSceneCompare] = useState<string | null>(null)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [jobs, setJobs] = useState<MotionJob[]>([])
  const [compare, setCompare] = useState<string[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const videos = useRef<Record<string, HTMLVideoElement | null>>({})
  const revision = useRef(0)
  const patch = (change: Partial<Draft>) => { revision.current += 1; setDraft(prev => ({ ...prev, plan_id: undefined, ...change })); setPlan(null); setPreview(null) }

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || 'null')
      if (saved && Array.isArray(saved.references) && Array.isArray(saved.annotations)) {
        setDraft({ ...initial, ...saved, pipeline: saved.pipeline || '3d', director: saved.pipeline ? saved.director : 'opus', workflow: saved.pipeline ? saved.workflow : 'reference', duration: [5, 10, 15, 30].includes(saved.duration) ? saved.duration : 5 }); setSelected(saved.references[0]?.id || '')
        const previous = JSON.parse(localStorage.getItem(KEY+'-preview') || 'null')
        if(previous?.plan?.scene) { setPlan(previous.plan); setPreview(previous.preview) }
      }
    } catch { /* A private browser can still create shots. */ }
    setRestored(true)
    let cancelled = false
    const load = async () => {
      try { const data = await motionApi.jobs(); if (!cancelled) setJobs(data.items) }
      catch { if (!cancelled) setError('Could not load shots. Check the gateway connection.') }
    }
    const check = async () => {
      try { const data = await motionApi.capabilities(); if (!cancelled) setCapabilities(data) }
      catch { if (!cancelled) setCapabilities(null) }
    }
    void load(); void check()
    const poll = setInterval(() => void load(), 2500)
    const availability = setInterval(() => void check(), 20000)
    return () => { cancelled = true; clearInterval(poll); clearInterval(availability) }
  }, [])
  useEffect(() => {
    if (restored) { try { localStorage.setItem(KEY, JSON.stringify(draft)) } catch { /* Storage is optional. */ } }
  }, [draft, restored])
  useEffect(() => { if(restored) { try { if(plan?.scene) localStorage.setItem(KEY+'-preview',JSON.stringify({plan,preview})); else localStorage.removeItem(KEY+'-preview') } catch {} } }, [plan,preview,restored])
  useEffect(() => {
    if(!plan?.request.plan_id || !preview || !['queued','rendering'].includes(preview.status)) return
    let cancelled=false
    const poll=setInterval(async()=>{try {const state=await motionApi.preview(plan.request.plan_id!);if(!cancelled) {setPreview(state);if(state.error)setError(state.error)}} catch(e) {if(!cancelled)setError(String(e))}},1800)
    return()=>{cancelled=true;clearInterval(poll)}
  },[plan?.request.plan_id,preview?.status])

  const upload = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy('upload'); setError('')
    const references = [...draft.references]
    try {
      if (references.length + files.length > 4) throw new Error('Use up to four references per shot.')
      for (const file of Array.from(files)) {
        if (file.size > 20 * 1024 * 1024) throw new Error(`${file.name} exceeds 20 MB.`)
        const asset = await motionApi.upload(file)
        references.push({ id: asset.id, name: asset.original_filename })
      }
    } catch (e) { setError(e instanceof Error ? e.message : 'Image upload failed') }
    finally { patch({ references, workflow: references.length > 1 ? 'reference' : draft.workflow }); setSelected(references.at(-1)?.id || ''); setBusy('') }
  }
  const planShot = async () => {
    setBusy('plan'); setError('')
    const current = revision.current
    try { const planned = await motionApi.plan(payload(draft)); if (revision.current === current) {setPlan(planned); if(planned.scene) setPreview(await motionApi.buildPreview(planned.request.plan_id!))} }
    catch (e) { setError(e instanceof Error ? e.message : 'Planning failed') }
    finally { setBusy('') }
  }
  const reviseScene = async () => {
    if(!plan?.request.plan_id || !correction.trim()) return
    setBusy('revise');setError('');const current=revision.current
    try {const planned=await motionApi.revise(plan.request.plan_id,correction);if(revision.current===current){setPlan(planned);setPreview(await motionApi.buildPreview(planned.request.plan_id!));setCorrection('')}}
    catch(e){setError(e instanceof Error?e.message:'Scene correction failed')}
    finally{setBusy('')}
  }
  const generate = async () => {
    setBusy('render'); setError('')
    try { const job = await motionApi.submit(plan?.request || payload(draft)); setPlan(job.shot); setJobs(prev => [job, ...prev]) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not submit H3 shot') }
    finally { setBusy('') }
  }
  const add = (annotation: Annotation) => patch({ annotations: [...draft.annotations, annotation],
    mode: ['arrow', 'path', 'orbit', 'waypoint'].includes(annotation.kind) ? 'manual' : draft.mode })
  const undo = () => {
    const last = draft.annotations.map(a => a.reference_id).lastIndexOf(selected)
    if (last >= 0) patch({ annotations: draft.annotations.filter((_, i) => i !== last) })
  }
  const completed = jobs.filter(j => j.status === 'completed' && j.preview_url)
  const comparison = completed.filter(j => compare.includes(j.job_id))
  return <div className="space-y-6 pb-12">
    <div className="flex items-start gap-3"><Clapperboard className="mt-1 h-7 w-7 shrink-0 text-sky-300" /><div><h1 className="text-2xl font-semibold">Motion Director <span className="text-sm text-muted-foreground">v0.1</span></h1><p className="mt-1 text-sm text-muted-foreground">Set the scene. Preview the motion. Make the film.</p></div></div>
    <div className="flex flex-wrap gap-2 text-xs text-sky-200"><span className="rounded-full bg-sky-400/10 px-3 py-2">1 · Reference & direction</span><span className="rounded-full bg-sky-400/10 px-3 py-2">2 · 3D preview & corrections</span><span className="rounded-full bg-sky-400/10 px-3 py-2">3 · Final clip</span></div>
    {error && <p role="alert" className="break-words rounded-xl border border-red-300/20 bg-red-400/5 p-3 text-sm text-red-200">{error}</p>}
    <section className="space-y-4 rounded-xl border border-white/10 bg-card/40 p-3 sm:p-5">
      <h2 className="font-semibold">1 · Your scene</h2>
      <label className="flex min-h-14 cursor-pointer items-center justify-center gap-2 rounded-lg border border-dashed border-sky-300/40 p-3 text-sm">
        <Upload className="h-5 w-5" />{busy === 'upload' ? 'Uploading references…' : 'Choose reference images'}
        <input aria-label="Upload reference images" type="file" accept="image/png,image/jpeg,image/webp,image/bmp" multiple disabled={Boolean(busy)} className="sr-only" onChange={e => { void upload(e.target.files); e.target.value = '' }} />
      </label>
      <p className="text-xs text-muted-foreground">Up to four reference images, 20 MB each. Your originals are preserved.</p>
      <label className="grid gap-2 text-sm">Cinematic prompt<Textarea rows={4} maxLength={4000} value={draft.prompt} placeholder="Describe the scene, what happens and how you want the camera to move…" onChange={e => patch({ prompt: e.target.value })} /></label>
      {draft.references.length > 0 && <>
        {selected && <img className="max-h-64 w-full rounded-lg bg-black/30 object-contain" src={`/api/v1/motion-director/references/${selected}`} alt="Selected scene reference" />}
        <div className="flex flex-wrap gap-2">{draft.references.map((ref, i) => <Button key={ref.id} variant={selected === ref.id ? 'default' : 'outline'} className="min-h-11 max-w-full truncate" onClick={() => setSelected(ref.id)}>Picture {i + 1} · {ref.name.slice(0, 25)}</Button>)}</div>
        <details className="space-y-3"><summary className="min-h-11 cursor-pointer py-3 font-medium">Optional drawing · camera & subjects</summary>
        <div className="my-3 grid grid-cols-2 gap-2 sm:grid-cols-4" role="group" aria-label="Drawing tools">{tools.map(t => <button key={t} type="button" aria-pressed={tool === t} className={`min-h-12 rounded-md border px-3 text-sm ${tool === t ? 'border-sky-400 bg-sky-400/15 text-sky-200' : 'border-white/10'}`} onClick={() => setTool(t)}>{toolNames[t]}</button>)}</div>
        <label className="grid gap-1 text-sm">Marker label<Input value={label} maxLength={120} placeholder="Main subject, doorway, camera target…" className="min-h-11" onChange={e => setLabel(e.target.value)} /></label>
        {selected && <AnnotationCanvas key={selected} referenceId={selected} annotations={draft.annotations} tool={tool} label={label} onAdd={add} />}
        <p className="my-3 text-sm text-sky-200" role="status">{toolHelp[tool]}</p>
        <div className="flex flex-wrap gap-2"><Button variant="outline" className="min-h-11" onClick={undo}>Undo</Button><Button variant="outline" className="min-h-11" onClick={() => patch({ annotations: draft.annotations.filter(a => a.reference_id !== selected) })}>Clear this image</Button><Button variant="ghost" className="min-h-11" onClick={() => {
          const references = draft.references.filter(r => r.id !== selected)
          patch({ references, annotations: draft.annotations.filter(a => a.reference_id !== selected) }); setSelected(references[0]?.id || '')
        }}>Remove reference</Button></div>
        <Button variant="ghost" className="min-h-11" onClick={() => downloadMotionJSON({ version: '0.1', ...payload(draft) }, 'motion-director-drawing.json')}>Download editable drawing data</Button>
        </details>
      </>}
    </section>
    <section className="space-y-4 rounded-xl border border-white/10 bg-card/40 p-3 sm:p-5">
      <h2 className="font-semibold">2 · Direct & preview</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="grid gap-1 text-sm">Duration<select aria-label="Duration" className={selectClass} value={draft.duration} onChange={e => patch({ duration: Number(e.target.value) as Draft['duration'] })}>{[5, 10, 15, 30].map(n => <option key={n} value={n}>{n} seconds</option>)}</select></label>
        {draft.pipeline === '3d' ? <div className="grid gap-1 text-sm"><span>Director</span><div className="flex min-h-11 items-center rounded-md border border-white/10 px-3 text-sky-200">Claude Opus + Blender</div></div> : (<label className="grid gap-1 text-sm">Engine<select aria-label="Engine" className={selectClass} value={draft.engine === 'grok' ? 'grok' : draft.director || 'auto'} onChange={e => patch(e.target.value === 'grok' ? { engine: 'grok' } : { engine: 'h3', director: e.target.value as Draft['director'] })}><option value="auto">H3 · automatic cinematic director</option><option value="local">H3 · local cinematic director</option><option value="opus" disabled={!capabilities?.planner.opus_available}>H3 · Claude Code Opus {capabilities?.planner.opus_available ? '(Pro subscription)' : '(unavailable)'}</option><option value="grok" disabled={!capabilities?.grok.available}>Grok via Hermes · {capabilities?.grok.available ? 'OAuth' : 'manual import below'}</option></select></label>)}
      </div>
      <p className="text-xs text-muted-foreground">{draft.pipeline === '3d' ? 'Claude Opus + Blender turn your reference into an editable approximation. Review the movement before making the AI clip.' : 'Classic prompt direction. Switch to 3D in Advanced for a Blender preview.'}</p>
      {draft.pipeline === '3d' && <Button className="min-h-12 w-full" disabled={Boolean(busy) || !draft.prompt.trim() || !draft.references.length || !capabilities?.blender?.available || ['queued','rendering'].includes(preview?.status || '')} onClick={() => void planShot()}>{busy === 'plan' ? 'Opus is directing your scene…' : plan?.scene ? 'Rebuild 3D preview' : 'Build 3D preview'}</Button>}
      {draft.pipeline === '3d' && !capabilities?.blender?.available && <p className="text-sm text-amber-200">{capabilities?.blender?.reason || 'Checking Blender availability…'}</p>}
      {preview && <div className="space-y-3" aria-live="polite"><p className="text-sm text-sky-200">{preview.message}</p>{['queued','rendering'].includes(preview.status) && <div className="h-1 animate-pulse rounded bg-sky-400/40" />}
        {preview.preview_url && <><video aria-label="Blender 3D preview" className="max-h-[55vh] w-full rounded-lg bg-black" src={preview.preview_url} controls playsInline loop preload="metadata" />
          <p className="text-xs text-muted-foreground">Blender motion preview · approximate geometry and stylized animation. H3 may change poses, paths and object behavior.</p>
          <label className="grid gap-2 text-sm">Correct the scene<Textarea value={correction} maxLength={1200} rows={2} onChange={e=>setCorrection(e.target.value)} placeholder="Move the camera farther back, turn the path left, keep the train still…" /></label>
          <Button variant="outline" className="min-h-11 w-full" disabled={Boolean(busy) || !correction.trim()} onClick={()=>void reviseScene()}>{busy==='revise'?'Opus is revising your scene…':'Apply correction & preview'}</Button>
          <a className="inline-flex min-h-11 items-center text-sm text-sky-300" href={preview.export_url} download>Export for Grok · MP4, PNG & prompt</a>
        </>}
      </div>}
      <details className="space-y-3 text-sm"><summary className="min-h-11 cursor-pointer py-3">Advanced · settings & shot plan</summary>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <label className="grid gap-1 text-sm">Workflow<select aria-label="Workflow" className={selectClass} value={draft.pipeline} onChange={e=>patch({pipeline:e.target.value as Draft['pipeline'],director:e.target.value==='3d'?'opus':'auto',workflow:'reference'})}><option value="3d">3D scene · Claude + Blender</option><option value="prompt">Classic · prompt direction</option></select></label>
        <label className="grid gap-1 text-sm">Director<select aria-label="Director" className={selectClass} value={draft.mode} onChange={e => patch({ mode: e.target.value as Draft['mode'] })}><option value="auto">Auto Director</option><option value="manual">Manual Director</option></select></label>
        <label className="grid gap-1 text-sm">Cinematic preset<select aria-label="Cinematic preset" className={selectClass} value={draft.preset} onChange={e => patch({ preset: e.target.value as Draft['preset'] })}><option value="professional">Professional Cinematic Director</option><option value="intimate">Intimate portrait</option><option value="reveal">Environmental reveal</option><option value="action">Readable action</option></select></label>
        <label className="grid gap-1 text-sm">Output<select aria-label="Output" className={selectClass} value={draft.orientation} onChange={e => patch({ orientation: e.target.value as Draft['orientation'] })}><option value="vertical">Vertical · phone</option><option value="horizontal">Horizontal · landscape</option></select></label>
        <label className="grid gap-1 text-sm">Reference use<select aria-label="Reference use" className={selectClass} value={draft.workflow} onChange={e => patch({ workflow: e.target.value as Draft['workflow'] })}><option value="reference">Reference-to-video · identity & style</option><option value="image" disabled={draft.references.length !== 1 || !capabilities?.h3.workflows?.image?.available}>Image-to-video · first frame {capabilities?.h3.workflows?.image?.available ? '' : '(weights unavailable)'}</option></select></label>
      </div>
      <p className="text-xs text-muted-foreground">{capabilities?.planner.reason || 'The local planner remains available.'} Opus plans are saved once and reused during rendering.</p>
      <label className="grid gap-1">Seed<Input type="number" min={0} max={9007199254740991} value={draft.seed} onChange={e => patch({ seed: Number(e.target.value) })} /></label>
      {capabilities?.resources && <p className="text-xs text-muted-foreground">GPU {capabilities.resources.gpu.utilization_pct ?? '—'}% · {capabilities.resources.gpu.temperature_c ?? '—'}°C · {capabilities.resources.gpu.cap_active ? '2000 MHz safety cap active' : 'Safety cap unverified'}</p>}
      {draft.pipeline !== '3d' && <Button className="min-h-11 w-full sm:w-auto" disabled={Boolean(busy) || !draft.prompt.trim() || !draft.references.length} onClick={() => void planShot()}>{busy === 'plan' ? 'Planning…' : 'Preview shot plan'}</Button>}
    {plan && <section className="space-y-3 rounded-xl border border-sky-300/20 bg-sky-300/5 p-4">
      <h2 className="font-semibold">Shot plan</h2>{plan.scene && <><p className="text-sm">{plan.scene.reference_analysis}</p><p className="text-xs text-muted-foreground">{plan.scene.entities.length} objects · {plan.scene.camera.mode} camera · {plan.scene.camera.distance} m distance</p>{plan.scene.limitations.map(note=><p key={note} className="text-xs text-amber-200">{note}</p>)}</>}<p className="text-sm">{plan.camera.movement}</p><p className="text-xs text-muted-foreground">{plan.composition}</p>
      <p className="text-xs text-sky-200">Planned by {plan.planner === 'claude-code-opus' ? 'Claude Code Opus' : 'Local Cinematic Director'}</p>
      <ol className="space-y-2 text-sm">{plan.timing.map((beat, i) => <li key={i}><span className="text-sky-300">{beat.start.toFixed(1)}–{beat.end.toFixed(1)}s</span> · {beat.action}</li>)}</ol>
      <p className="text-xs text-muted-foreground">{plan.width} × {plan.height} · {plan.frames} frames at {plan.fps} fps · {(plan.frames / plan.fps).toFixed(2)}s rendered · {plan.request.workflow === 'image' ? 8 : 4}-step H3 draft</p>
      {plan.notes.map(note => <p key={note} className="text-xs text-amber-200/80">{note}</p>)}
      <Button variant="outline" className="min-h-11" onClick={() => downloadMotionJSON(plan, 'motion-director-shot.json')}>Download shot plan</Button>
      {capabilities?.h3.workflows?.[plan.request.workflow]?.reason && <p className="text-xs text-amber-200">{capabilities.h3.workflows[plan.request.workflow].reason}</p>}
    </section>}
      </details>
    </section>
    <section className="space-y-3 rounded-xl border border-sky-300/20 bg-sky-300/5 p-3 sm:p-5"><h2 className="font-semibold">3 · Make the final clip</h2><p className="text-sm text-muted-foreground">{draft.pipeline === '3d' ? 'Happy with the preview? Use its motion as a reference for H3, or export it for Grok.' : 'Generate a clip using your reference and direction.'}</p>
      <Button className="min-h-12 w-full" disabled={Boolean(busy) || !draft.prompt.trim() || !draft.references.length || (draft.pipeline === '3d' && preview?.status !== 'completed') || (!capabilities?.h3.render_ready && !capabilities?.h3.can_recover_memory)} onClick={() => void generate()}>{busy === 'render' ? 'Preparing your final clip…' : 'Generate video'}</Button>
      {capabilities?.resources && <p className="text-xs text-muted-foreground">{capabilities.resources.memory?.available_gb} GiB available · safe H3 generation needs 90 GiB</p>}
    </section>
    {!capabilities ? <p role="status" className="text-sm text-muted-foreground">Checking local H3 availability…</p> : !capabilities.h3.render_ready && <p role="status" className="rounded-lg border border-amber-400/20 p-3 text-sm text-amber-200">{capabilities.h3.reason || 'H3 is unavailable.'} Planning and drawings remain available.</p>}
    {capabilities?.h3.can_recover_memory && <Button variant="outline" className="min-h-11" disabled={Boolean(busy)} onClick={async () => { setBusy('memory'); try { await motionApi.recoverMemory(); setCapabilities(await motionApi.capabilities()) } catch(e) { setError(String(e)) } finally { setBusy('') } }}>Release idle H3 memory</Button>}
    <details className="rounded-xl border border-white/10 p-3"><summary className="min-h-11 cursor-pointer py-3">Grok · export, import & compare</summary><p className="my-3 text-sm text-muted-foreground">Export the Blender reference above, open Grok, and use its PNG and prompt to create your clip. If your Grok browser supports video references, use the MP4 too. Download the result and import it here.</p><p className="my-3 text-xs text-muted-foreground">{capabilities?.grok.reason}</p><a href="https://grok.com/imagine" target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center text-sky-300">Open Grok on your phone</a><label className="mt-3 flex min-h-12 cursor-pointer items-center justify-center rounded-lg border border-dashed p-3 text-sm">{busy === 'import' ? 'Preparing imported video…' : 'Import Grok MP4'}<input aria-label="Import Grok MP4" type="file" accept="video/mp4" className="sr-only" disabled={Boolean(busy)} onChange={async e => { const file=e.target.files?.[0]; e.target.value=''; if(!file) return; setBusy('import'); try { const reference=completed.find(j => j.service !== 'grok'); const job=await motionApi.importGrok(file,plan?.scene?undefined:reference?.job_id,plan?.scene?plan.request.plan_id||undefined:undefined); setJobs(prev=>[job,...prev]); if(job.blender_preview_url)setSceneCompare(job.job_id);else if(reference) setCompare([reference.job_id,job.job_id]) } catch(err) { setError(String(err)) } finally { setBusy('') } }} /></label><p className="mt-2 text-xs text-muted-foreground">Your original MP4 is preserved. Automatic Grok generation stays disabled.</p></details>
    <section className="space-y-4"><h2 className="font-semibold">Your takes</h2>
      {!jobs.length && <p className="text-sm text-muted-foreground">Completed videos appear here for preview, download and comparison.</p>}
      {completed.filter(j=>j.job_id===sceneCompare && j.blender_preview_url).map(job=><div key={job.job_id} className="space-y-3 rounded-xl border border-sky-400/30 p-3"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">Blender vs final clip</h3><Button variant="outline" className="min-h-11" onClick={()=>['blender-'+job.job_id,'final-'+job.job_id].forEach(id=>{const v=videos.current[id];if(v){v.currentTime=0;void v.play().catch(()=>{})}})}>Play reference & final</Button></div><div className="grid gap-3 md:grid-cols-2">{[{label:'Blender · planned motion',src:job.blender_preview_url!,key:'blender-'},{label:job.service==='grok'?'Grok · imported result':'H3 · generated result',src:job.preview_url!,key:'final-'}].map(v=><div key={v.key}><p className="mb-2 text-sm">{v.label}</p><video ref={node=>{videos.current[v.key+job.job_id]=node}} className="max-h-[55vh] w-full rounded-lg bg-black" src={v.src} controls playsInline preload="metadata" /></div>)}</div><p className="text-xs text-amber-200">Compare travel direction, camera distance, turns and stationary objects. Reference conditioning cannot guarantee exact movement or appearance.</p></div>)}
      {comparison.length === 2 && <div className="space-y-3 rounded-xl border border-sky-400/30 p-3"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">Compare takes</h3><Button variant="outline" className="min-h-11" onClick={() => comparison.forEach(j => { const video = videos.current[j.job_id]; if (video) { video.currentTime = 0; void video.play().catch(() => {}) } })}>Play both from start</Button></div><div className="grid gap-3 md:grid-cols-2">{comparison.map((job, i) => <div key={job.job_id}><p className="mb-2 text-sm">Take {i + 1} · {job.shot.request.preset}</p><video ref={node => { videos.current[job.job_id] = node }} className="max-h-[60vh] w-full rounded-lg bg-black" src={job.preview_url!} playsInline controls preload="metadata" /></div>)}</div></div>}
      <div className="grid gap-4 lg:grid-cols-2">{jobs.map(job => <article key={job.job_id} className="min-w-0 space-y-3 rounded-xl border border-white/10 p-3 sm:p-4">
        <div className="flex items-center justify-between gap-2"><span className="text-sm font-medium capitalize">{job.status}</span><span className="text-xs text-muted-foreground">{new Date(job.created_at).toLocaleString()}</span></div>
        <p className="line-clamp-2 text-sm">{job.shot.request.prompt}</p><p role="status" className="text-xs text-sky-200">{job.message}</p>
        {!terminal(job) && <div className="h-1 overflow-hidden rounded bg-white/10"><div className="h-full w-1/3 animate-pulse rounded bg-sky-400" /></div>}
        {Boolean(job.total_segments) && <p className="text-xs text-muted-foreground">{job.completed_segments}/{job.total_segments} clips saved</p>}
        {job.error && <p className="break-words text-xs text-red-300">{job.error}</p>}
        {job.preview_url && <video className="max-h-[55vh] w-full rounded-lg bg-black" src={job.preview_url} controls playsInline preload="metadata" />}
        <div className="flex flex-wrap gap-2">{job.download_url && <a className="flex min-h-11 items-center rounded-md border border-white/15 px-3 text-sm" href={job.download_url}>Download video</a>}
          {job.blender_preview_url && job.status==='completed' && <Button variant="outline" className="min-h-11" onClick={()=>setSceneCompare(job.job_id)}>Compare with Blender</Button>}
          {job.status === 'completed' && <label className="flex min-h-11 cursor-pointer items-center gap-2 px-2 text-sm"><input type="checkbox" checked={compare.includes(job.job_id)} disabled={!compare.includes(job.job_id) && compare.length >= 2} onChange={e => setCompare(prev => e.target.checked ? [...prev, job.job_id] : prev.filter(id => id !== job.job_id))} />Compare take</label>}
          {job.shot.planner !== 'manual-import' && <Button variant="ghost" className="min-h-11" onClick={() => { const { reference_ids, ...request } = job.shot.request; setDraft({ ...initial, ...request, pipeline: request.pipeline || 'prompt', duration: [5,10,15,30].includes(request.duration) ? request.duration : 5, plan_id: undefined, references: reference_ids.map((id, i) => ({ id, name: `Picture ${i + 1}` })) }); setSelected(reference_ids[0]); setPlan(null) }}>Reuse shot</Button>}
          {job.status === 'failed' && job.prompt_id && <Button variant="ghost" className="min-h-11" onClick={() => void motionApi.reconcile(job.job_id).catch(e => setError(String(e)))}>Check accepted H3 shot</Button>}
        </div>
      </article>)}</div>
    </section>
  </div>
}
