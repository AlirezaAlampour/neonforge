'use client'

import { useEffect, useState } from 'react'
import { FileDropzone } from '@/components/file-dropzone'
import { InputPreview } from '@/components/input-preview'
import { JobTracker } from '@/components/job-tracker'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useJobPoller } from '@/hooks/use-job-poller'
import { fetchComfyUITemplates, submitComfyUIJob, uploadComfyUIAsset } from '@/lib/api'
import type { ComfyUITemplate } from '@/lib/types'

type Take = { image: File; video: File; seed: string }
export default function CharacterPage() {
  const [mode, setMode] = useState<'replace' | 'animate'>('replace')
  const [workflow, setWorkflow] = useState<ComfyUITemplate | null>(null)
  const [checking, setChecking] = useState(true)
  const [image, setImage] = useState<File | null>(null)
  const [video, setVideo] = useState<File | null>(null)
  const [seed, setSeed] = useState('42')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [takes, setTakes] = useState<Record<string, Take>>({})
  const { jobs, trackJob, dismissJob } = useJobPoller()
  useEffect(() => {
    fetchComfyUITemplates().then(({ items }) => setWorkflow(items.find((item) => item.id === 'wan-character-swap') ?? null))
      .catch(() => setError('Unable to check Character availability. Refresh to try again.'))
      .finally(() => setChecking(false))
  }, [])
  const ready = Boolean(workflow && !workflow.validation.missing.length)
  const busy = submitting || jobs.some((job) => !['completed', 'failed'].includes(job.status))
  const submit = async (take?: Take) => {
    const values = take ?? (image && video ? { image, video, seed } : null)
    if (!values || !workflow || !ready || busy || mode !== 'replace') return
    setSubmitting(true)
    setError(null)
    try {
      const [reference, driving] = await Promise.all([uploadComfyUIAsset(values.image, 'image'), uploadComfyUIAsset(values.video, 'video')])
      const result = await submitComfyUIJob({ template_id: workflow.id, inputs: { reference_image: reference.id, driving_video: driving.id }, params: {
        seed: Number(values.seed || 42), steps: 4, cfg: 1, denoise_strength: 0.9, frame_rate: 16, person_index: 0, max_frames: 17,
      } })
      setTakes((previous) => ({ ...previous, [result.job_id]: values }))
      trackJob(result.job_id, 'Character')
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to start Character. Please try again.') }
    finally { setSubmitting(false) }
  }
  return <div className="space-y-8">
    <header><h1 className="text-3xl font-semibold tracking-tight">Character</h1><p className="mt-2 text-muted-foreground">A new character. The same performance.</p></header>
    <div className="inline-flex gap-1 rounded-xl bg-secondary/50 p-1" role="tablist" aria-label="Character mode">
      <Button role="tab" aria-selected={mode === 'replace'} variant={mode === 'replace' ? 'default' : 'ghost'} onClick={() => setMode('replace')}>Replace</Button>
      <Button role="tab" aria-selected={mode === 'animate'} variant={mode === 'animate' ? 'default' : 'ghost'} onClick={() => setMode('animate')}>Animate</Button>
    </div>
    {mode === 'animate' ? <div className="rounded-2xl bg-card/50 p-10"><h2 className="text-xl font-medium">Animate is coming soon</h2><p className="mt-3 max-w-xl text-muted-foreground">Bring a character to life using a driving performance. This mode will become available after its own generation workflow passes local testing.</p></div> : <>
      <div className="grid gap-8 md:grid-cols-2">
        <section className="space-y-4"><div><h2 className="font-medium">Reference character</h2><p className="mt-1 text-sm text-muted-foreground">The appearance you want in the result.</p></div><FileDropzone accept="image/*" label="Drop a character image" hint="PNG, JPG or WebP · up to 20 MB" file={image} onFileChange={setImage} maxSizeMB={20} icon="image" /><InputPreview file={image} kind="image" /></section>
        <section className="space-y-4"><div><h2 className="font-medium">Driving video</h2><p className="mt-1 text-sm text-muted-foreground">One visible performer, with clear motion.</p></div><FileDropzone accept="video/*" label="Drop a driving video" hint="MP4, MOV or WebM · up to 500 MB" file={video} onFileChange={setVideo} maxSizeMB={500} icon="video" /><InputPreview file={video} kind="video" /></section>
      </div>
      <div className="flex flex-wrap items-center gap-5"><Button size="lg" disabled={!ready || !image || !video || busy} onClick={() => void submit()}>{submitting ? 'Uploading…' : busy ? 'Generation in progress' : 'Generate character'}</Button><p className="max-w-lg text-sm text-muted-foreground">Preview: first 17 frames, about 1 second. Longer clips exceed the currently tested local memory limit.</p></div>
      <details className="text-sm"><summary className="cursor-pointer text-muted-foreground">Advanced settings</summary><div className="mt-4 max-w-48 space-y-2"><Label htmlFor="character-seed">Seed</Label><Input id="character-seed" type="number" value={seed} onChange={(e) => setSeed(e.target.value)} /></div></details>
      {checking && <p className="text-sm text-muted-foreground">Checking availability…</p>}
      {!checking && !ready && <p role="status" className="text-sm text-amber-300">Character needs setup. See System Info for details.</p>}
      {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
    </>}
    <JobTracker canReuse={(id) => Boolean(takes[id])} jobs={jobs} onDismiss={(id) => { dismissJob(id); setTakes((previous) => { const next = { ...previous }; delete next[id]; return next }) }} onReuse={(id) => { const take = takes[id]; if (take) { setMode('replace'); setImage(take.image); setVideo(take.video); setSeed(take.seed) } }} onRegenerate={(id) => { if (takes[id]) void submit(takes[id]) }} actionsDisabled={busy || mode !== 'replace'} />
    {!jobs.length && mode === 'replace' && <p className="py-12 text-center text-sm text-muted-foreground">Your character preview will appear here.</p>}
  </div>
}
