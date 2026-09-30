'use client'

import { useEffect, useState } from 'react'
import { Film } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { JobTracker } from '@/components/job-tracker'
import { useJobPoller } from '@/hooks/use-job-poller'
import { fetchComfyUITemplates, submitComfyUIJob } from '@/lib/api'
import type { ComfyUITemplate } from '@/lib/types'

const FRAMES = {
  landscape: { label: 'Landscape', width: 832, height: 480 },
  portrait: { label: 'Portrait', width: 480, height: 832 },
  square: { label: 'Square', width: 640, height: 640 },
} as const
type Settings = { prompt: string; avoid: string; frame: keyof typeof FRAMES; duration: number; quality: string; seed: string }
const initial: Settings = { prompt: '', avoid: '', frame: 'landscape', duration: 5, quality: 'preview', seed: '42' }
const selectClass = 'h-10 rounded-lg border border-input bg-background px-3 text-sm'

export default function VideoPage() {
  const [settings, setSettings] = useState(initial)
  const [workflow, setWorkflow] = useState<ComfyUITemplate | null>(null)
  const [checking, setChecking] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<Record<string, Settings>>({})
  const { jobs, trackJob, dismissJob } = useJobPoller()
  const update = (patch: Partial<Settings>) => setSettings((previous) => ({ ...previous, ...patch }))

  useEffect(() => {
    fetchComfyUITemplates().then(({ items }) => setWorkflow(items.find((item) => item.id === 'hunyuan-video-15-t2v') ?? null))
      .catch(() => setError('Unable to check Video availability. Refresh the page to try again.'))
      .finally(() => setChecking(false))
  }, [])
  const available = Boolean(workflow && workflow.validation.missing.length === 0)
  const busy = submitting || jobs.some((job) => !['completed', 'failed'].includes(job.status))

  const submit = async (values = settings) => {
    if (!workflow || !available || busy || !values.prompt.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      const result = await submitComfyUIJob({ template_id: workflow.id, inputs: {}, params: {
        prompt: values.prompt.trim(), negative_prompt: values.avoid.trim(),
        width: FRAMES[values.frame].width, height: FRAMES[values.frame].height,
        frames: values.duration * 24 + 1, fps: 24, steps: values.quality === 'quality' ? 50 : 20,
        seed: Number(values.seed || 42), cfg: 1, shift: 5,
      } })
      setSaved((previous) => ({ ...previous, [result.job_id]: { ...values } }))
      trackJob(result.job_id, 'Video')
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to start video. Please try again.') }
    finally { setSubmitting(false) }
  }

  return <div className="space-y-8">
    <header><h1 className="text-3xl font-semibold tracking-tight">Video</h1><p className="mt-2 text-muted-foreground">Describe a scene. Bring it to life.</p></header>
    <section className="space-y-5 rounded-2xl bg-card/50 p-5 sm:p-7" aria-label="Video workspace">
      <Label htmlFor="video-prompt">Your scene</Label>
      <Textarea id="video-prompt" className="min-h-52 resize-y border-0 bg-transparent text-base shadow-none focus-visible:ring-1" value={settings.prompt} onChange={(e) => update({ prompt: e.target.value })} placeholder="A red fox crosses a snowy clearing at sunrise. The camera follows slowly through soft golden light…" />
      <div className="flex flex-wrap items-end gap-4">
        <div className="grid gap-2"><Label htmlFor="video-frame">Aspect ratio</Label><select id="video-frame" className={selectClass} value={settings.frame} onChange={(e) => update({ frame: e.target.value as Settings['frame'] })}>{Object.entries(FRAMES).map(([key, frame]) => <option key={key} value={key}>{frame.label}</option>)}</select></div>
        <div className="grid gap-2"><Label htmlFor="video-duration">Duration</Label><select id="video-duration" className={selectClass} value={settings.duration} onChange={(e) => update({ duration: Number(e.target.value) })}><option value={2}>2 seconds</option><option value={3}>3 seconds</option><option value={5}>5 seconds</option></select></div>
        <div className="grid gap-2"><Label htmlFor="video-quality">Quality</Label><select id="video-quality" className={selectClass} value={settings.quality} onChange={(e) => update({ quality: e.target.value })}><option value="preview">Draft</option><option value="quality">Studio</option></select></div>
        <Button size="lg" className="sm:ml-auto" disabled={!available || !settings.prompt.trim() || busy} onClick={() => void submit()}>{submitting ? 'Preparing…' : busy ? 'Generation in progress' : 'Generate video'}</Button>
      </div>
      <p className="text-xs text-muted-foreground">{FRAMES[settings.frame].width} × {FRAMES[settings.frame].height} · Silent video · Studio renders take longer</p>
    </section>
    <details className="text-sm"><summary className="cursor-pointer text-muted-foreground">Advanced settings</summary><div className="mt-4 grid gap-4 sm:grid-cols-[1fr,180px]"><div className="space-y-2"><Label htmlFor="video-avoid">Avoid</Label><Textarea id="video-avoid" value={settings.avoid} onChange={(e) => update({ avoid: e.target.value })} placeholder="Text, watermarks…" rows={2} /></div><div className="space-y-2"><Label htmlFor="video-seed">Seed</Label><Input id="video-seed" type="number" value={settings.seed} onChange={(e) => update({ seed: e.target.value })} /></div></div></details>
    {checking && <p className="text-sm text-muted-foreground">Checking availability…</p>}
    {!checking && !available && <p role="status" className="text-sm text-amber-300">Video needs setup. See System Info for missing files and service details.</p>}
    {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
    <JobTracker canReuse={(id) => Boolean(saved[id])} jobs={jobs} onDismiss={dismissJob} onReuse={(id) => { if (saved[id]) setSettings(saved[id]) }} onRegenerate={(id) => { if (saved[id]) void submit(saved[id]) }} actionsDisabled={busy} />
    {!jobs.length && <div className="py-12 text-center text-muted-foreground"><Film className="mx-auto mb-3 h-9 w-9 opacity-40" /><p>Your video will appear here.</p><p className="mt-1 text-sm">Preview, download, or refine your next take.</p></div>}
  </div>
}
