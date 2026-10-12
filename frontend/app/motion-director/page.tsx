'use client'

import { useEffect, useRef, useState } from 'react'
import { Clapperboard, Upload } from 'lucide-react'
import { AnnotationCanvas } from '@/components/motion-annotations'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { motionApi, downloadMotionJSON, type Annotation, type Capabilities, type MotionJob, type ShotRequest, type ShotSpec, type Tool } from '@/lib/motion-director'

type Reference = { id: string; name: string }
type Draft = Omit<ShotRequest, 'reference_ids'> & { references: Reference[] }
const KEY = 'neonforge-motion-director-v0.1'
const initial: Draft = { references: [], annotations: [], prompt: '', mode: 'auto', preset: 'professional', duration: 2, orientation: 'vertical', workflow: 'reference', seed: 42, director: 'local' }
const tools: Tool[] = ['arrow', 'path', 'orbit', 'waypoint', 'subject', 'entry', 'exit']
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
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [jobs, setJobs] = useState<MotionJob[]>([])
  const [compare, setCompare] = useState<string[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const videos = useRef<Record<string, HTMLVideoElement | null>>({})
  const revision = useRef(0)
  const patch = (change: Partial<Draft>) => { revision.current += 1; setDraft(prev => ({ ...prev, ...change })); setPlan(null) }

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || 'null')
      if (saved && Array.isArray(saved.references) && Array.isArray(saved.annotations)) {
        setDraft({ ...initial, ...saved }); setSelected(saved.references[0]?.id || '')
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
    try { const planned = await motionApi.plan(payload(draft)); if (revision.current === current) setPlan(planned) }
    catch (e) { setError(e instanceof Error ? e.message : 'Planning failed') }
    finally { setBusy('') }
  }
  const generate = async () => {
    if (!plan) return
    setBusy('render'); setError('')
    try { const job = await motionApi.submit(plan.request); setJobs(prev => [job, ...prev]) }
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
    <div className="flex items-start gap-3"><Clapperboard className="mt-1 h-7 w-7 shrink-0 text-sky-300" /><div><h1 className="text-2xl font-semibold">Motion Director <span className="text-sm text-muted-foreground">v0.1</span></h1><p className="mt-1 text-sm text-muted-foreground">Reference → direction → cinematic shot. Local MiniMax H3.</p></div></div>
    {capabilities?.resources && <div className="rounded-lg border border-white/10 p-3 text-sm"><p>GPU-exclusive execution · {capabilities.resources.memory?.available_gb} GiB available · H3 needs 90 GiB</p><p className="mt-1 text-xs text-muted-foreground">GPU {capabilities.resources.gpu.utilization_pct ?? '—'}% · {capabilities.resources.gpu.temperature_c ?? '—'}°C · {capabilities.resources.gpu.cap_active ? '2000 MHz safety cap active' : 'Safety cap unverified'}</p></div>}
    <section className="space-y-4 rounded-xl border border-white/10 bg-card/40 p-3 sm:p-5">
      <h2 className="font-semibold">1. References & staging</h2>
      <label className="flex min-h-14 cursor-pointer items-center justify-center gap-2 rounded-lg border border-dashed border-sky-300/40 p-3 text-sm">
        <Upload className="h-5 w-5" />{busy === 'upload' ? 'Uploading references…' : 'Choose reference images'}
        <input aria-label="Upload reference images" type="file" accept="image/png,image/jpeg,image/webp,image/bmp" multiple disabled={Boolean(busy)} className="sr-only" onChange={e => { void upload(e.target.files); e.target.value = '' }} />
      </label>
      <p className="text-xs text-muted-foreground">Up to four GPT-created images, 20 MB each. Drawings stay separate from your originals.</p>
      {draft.references.length > 0 && <>
        <div className="flex flex-wrap gap-2">{draft.references.map((ref, i) => <Button key={ref.id} variant={selected === ref.id ? 'default' : 'outline'} className="min-h-11 max-w-full truncate" onClick={() => setSelected(ref.id)}>Picture {i + 1} · {ref.name.slice(0, 25)}</Button>)}</div>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Drawing tools">{tools.map(t => <button key={t} type="button" aria-pressed={tool === t} className={`min-h-11 rounded-md border px-3 text-sm capitalize ${tool === t ? 'border-sky-400 bg-sky-400/15 text-sky-200' : 'border-white/10'}`} onClick={() => setTool(t)}>{t}</button>)}</div>
        <label className="grid gap-1 text-sm">Marker label<Input value={label} maxLength={120} placeholder="Main subject, doorway, camera target…" className="min-h-11" onChange={e => setLabel(e.target.value)} /></label>
        {selected && <AnnotationCanvas key={selected} referenceId={selected} annotations={draft.annotations} tool={tool} label={label} onAdd={add} />}
        <p className="text-xs text-muted-foreground">Drag an arrow/path. Drag from orbit center to its edge. Tap to place a subject, entry, exit or waypoint.</p>
        <div className="flex flex-wrap gap-2"><Button variant="outline" className="min-h-11" onClick={undo}>Undo</Button><Button variant="outline" className="min-h-11" onClick={() => patch({ annotations: draft.annotations.filter(a => a.reference_id !== selected) })}>Clear this image</Button><Button variant="ghost" className="min-h-11" onClick={() => {
          const references = draft.references.filter(r => r.id !== selected)
          patch({ references, annotations: draft.annotations.filter(a => a.reference_id !== selected) }); setSelected(references[0]?.id || '')
        }}>Remove reference</Button></div>
        <Button variant="ghost" className="min-h-11" onClick={() => downloadMotionJSON({ version: '0.1', ...payload(draft) }, 'motion-director-drawing.json')}>Download editable drawing data</Button>
      </>}
    </section>
    <section className="space-y-4 rounded-xl border border-white/10 bg-card/40 p-3 sm:p-5">
      <h2 className="font-semibold">2. Direct the shot</h2>
      <label className="grid gap-2 text-sm">Cinematic prompt<Textarea rows={4} maxLength={4000} value={draft.prompt} placeholder="A lone traveler crosses a rain-soaked neon street. Track smoothly alongside, then settle on their face…" onChange={e => patch({ prompt: e.target.value })} /></label>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <label className="grid gap-1 text-sm">Director<select aria-label="Director" className={selectClass} value={draft.mode} onChange={e => patch({ mode: e.target.value as Draft['mode'] })}><option value="auto">Auto Director</option><option value="manual">Manual Director</option></select></label>
        <label className="grid gap-1 text-sm">Cinematic preset<select aria-label="Cinematic preset" className={selectClass} value={draft.preset} onChange={e => patch({ preset: e.target.value as Draft['preset'] })}><option value="professional">Professional Cinematic Director</option><option value="intimate">Intimate portrait</option><option value="reveal">Environmental reveal</option><option value="action">Readable action</option></select></label>
        <label className="grid gap-1 text-sm">Planning engine<select aria-label="Planning engine" className={selectClass} value={draft.director || 'local'} onChange={e => patch({ director: e.target.value as Draft['director'] })}><option value="local">Local cinematic director</option><option value="opus" disabled={!capabilities?.planner.opus_available}>Claude Code Opus · subscription {capabilities?.planner.opus_available ? '' : '(unavailable)'}</option></select></label>
        <label className="grid gap-1 text-sm">Duration<select aria-label="Duration" className={selectClass} value={draft.duration} onChange={e => patch({ duration: Number(e.target.value) as Draft['duration'] })}><option value={2}>2 seconds · short draft</option><option value={3}>3 seconds</option><option value={5}>5 seconds</option></select></label>
        <label className="grid gap-1 text-sm">Output<select aria-label="Output" className={selectClass} value={draft.orientation} onChange={e => patch({ orientation: e.target.value as Draft['orientation'] })}><option value="vertical">Vertical · phone</option><option value="horizontal">Horizontal · landscape</option></select></label>
        <label className="grid gap-1 text-sm">Reference use<select aria-label="Reference use" className={selectClass} value={draft.workflow} onChange={e => patch({ workflow: e.target.value as Draft['workflow'] })}><option value="reference">Reference-to-video · identity & style</option><option value="image" disabled={draft.references.length !== 1 || !capabilities?.h3.workflows?.image?.available}>Image-to-video · first frame {capabilities?.h3.workflows?.image?.available ? '' : '(weights unavailable)'}</option></select></label>
        <label className="grid gap-1 text-sm">Renderer<select aria-label="Renderer" className={selectClass} value="h3" onChange={() => {}}><option value="h3">MiniMax H3 · local</option><option value="grok" disabled>Grok · access unverified</option></select></label>
      </div>
      <p className="text-xs text-muted-foreground">{capabilities?.planner.reason || 'The local planner remains available.'} Opus plans are saved once and reused during rendering.</p>
      <details className="text-sm"><summary className="min-h-11 cursor-pointer py-3">Advanced: reproducible seed</summary><label className="grid gap-1">Seed<Input type="number" min={0} max={9007199254740991} value={draft.seed} onChange={e => patch({ seed: Number(e.target.value) })} /></label></details>
      <Button className="min-h-11 w-full sm:w-auto" disabled={Boolean(busy) || !draft.prompt.trim() || !draft.references.length} onClick={() => void planShot()}>{busy === 'plan' ? 'Planning…' : 'Plan shot'}</Button>
    </section>
    {plan && <section className="space-y-3 rounded-xl border border-sky-300/20 bg-sky-300/5 p-4">
      <h2 className="font-semibold">3. Review & generate</h2><p className="text-sm">{plan.camera.movement}</p><p className="text-xs text-muted-foreground">{plan.composition}</p>
      <p className="text-xs text-sky-200">Planned by {plan.planner === 'claude-code-opus' ? 'Claude Code Opus' : 'Local Cinematic Director'}</p>
      <ol className="space-y-2 text-sm">{plan.timing.map((beat, i) => <li key={i}><span className="text-sky-300">{beat.start.toFixed(1)}–{beat.end.toFixed(1)}s</span> · {beat.action}</li>)}</ol>
      <p className="text-xs text-muted-foreground">{plan.width} × {plan.height} · {plan.frames} frames at {plan.fps} fps · {(plan.frames / plan.fps).toFixed(2)}s rendered · {plan.request.workflow === 'image' ? 8 : 4}-step H3 draft</p>
      {plan.notes.map(note => <p key={note} className="text-xs text-amber-200/80">{note}</p>)}
      <div className="flex flex-wrap gap-2"><Button className="min-h-11" disabled={Boolean(busy) || !capabilities?.h3.render_ready || !capabilities?.h3.workflows?.[plan.request.workflow]?.available} onClick={() => void generate()}>{busy === 'render' ? 'Submitting…' : 'Generate H3 video'}</Button><Button variant="outline" className="min-h-11" onClick={() => downloadMotionJSON(plan, 'motion-director-shot.json')}>Download shot plan</Button></div>
      {capabilities?.h3.workflows?.[plan.request.workflow]?.reason && <p className="text-xs text-amber-200">{capabilities.h3.workflows[plan.request.workflow].reason}</p>}
    </section>}
    {!capabilities ? <p role="status" className="text-sm text-muted-foreground">Checking local H3 availability…</p> : !capabilities.h3.render_ready && <p role="status" className="rounded-lg border border-amber-400/20 p-3 text-sm text-amber-200">{capabilities.h3.reason || 'H3 is unavailable.'} Planning and drawings remain available.</p>}
    {error && <p role="alert" className="break-words text-sm text-red-300">{error}</p>}
    <section className="space-y-4"><h2 className="font-semibold">Your takes</h2>
      {!jobs.length && <p className="text-sm text-muted-foreground">Completed videos appear here for preview, download and comparison.</p>}
      {comparison.length === 2 && <div className="space-y-3 rounded-xl border border-sky-400/30 p-3"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">Compare takes</h3><Button variant="outline" className="min-h-11" onClick={() => comparison.forEach(j => { const video = videos.current[j.job_id]; if (video) { video.currentTime = 0; void video.play().catch(() => {}) } })}>Play both from start</Button></div><div className="grid gap-3 md:grid-cols-2">{comparison.map((job, i) => <div key={job.job_id}><p className="mb-2 text-sm">Take {i + 1} · {job.shot.request.preset}</p><video ref={node => { videos.current[job.job_id] = node }} className="max-h-[60vh] w-full rounded-lg bg-black" src={job.preview_url!} playsInline controls preload="metadata" /></div>)}</div></div>}
      <div className="grid gap-4 lg:grid-cols-2">{jobs.map(job => <article key={job.job_id} className="min-w-0 space-y-3 rounded-xl border border-white/10 p-3 sm:p-4">
        <div className="flex items-center justify-between gap-2"><span className="text-sm font-medium capitalize">{job.status}</span><span className="text-xs text-muted-foreground">{new Date(job.created_at).toLocaleString()}</span></div>
        <p className="line-clamp-2 text-sm">{job.shot.request.prompt}</p><p role="status" className="text-xs text-sky-200">{job.message}</p>
        {!terminal(job) && <div className="h-1 overflow-hidden rounded bg-white/10"><div className="h-full w-1/3 animate-pulse rounded bg-sky-400" /></div>}
        {job.error && <p className="break-words text-xs text-red-300">{job.error}</p>}
        {job.preview_url && <video className="max-h-[55vh] w-full rounded-lg bg-black" src={job.preview_url} controls playsInline preload="metadata" />}
        <div className="flex flex-wrap gap-2">{job.download_url && <a className="flex min-h-11 items-center rounded-md border border-white/15 px-3 text-sm" href={job.download_url}>Download video</a>}
          {job.status === 'completed' && <label className="flex min-h-11 cursor-pointer items-center gap-2 px-2 text-sm"><input type="checkbox" checked={compare.includes(job.job_id)} disabled={!compare.includes(job.job_id) && compare.length >= 2} onChange={e => setCompare(prev => e.target.checked ? [...prev, job.job_id] : prev.filter(id => id !== job.job_id))} />Compare take</label>}
          <Button variant="ghost" className="min-h-11" onClick={() => { const { reference_ids, ...request } = job.shot.request; setDraft({ ...request, references: reference_ids.map((id, i) => ({ id, name: `Picture ${i + 1}` })) }); setSelected(reference_ids[0]); setPlan(job.shot) }}>Reuse shot</Button>
          {job.status === 'failed' && job.prompt_id && <Button variant="ghost" className="min-h-11" onClick={() => void motionApi.reconcile(job.job_id).catch(e => setError(String(e)))}>Check accepted H3 shot</Button>}
        </div>
      </article>)}</div>
    </section>
  </div>
}
