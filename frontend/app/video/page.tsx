'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Film, Send, Sparkles } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { JobTracker } from '@/components/job-tracker'
import { useJobPoller } from '@/hooks/use-job-poller'
import { fetchComfyUITemplates, submitComfyUIJob } from '@/lib/api'
import type { ComfyUITemplate } from '@/lib/types'

const RESOLUTIONS = {
  landscape: { label: 'Landscape · 832 × 480', width: 832, height: 480 },
  portrait: { label: 'Portrait · 480 × 832', width: 480, height: 832 },
  square: { label: 'Square · 640 × 640', width: 640, height: 640 },
} as const

type ResolutionKey = keyof typeof RESOLUTIONS

export default function VideoGenerationPage() {
  const [templates, setTemplates] = useState<ComfyUITemplate[]>([])
  const [prompt, setPrompt] = useState('')
  const [negativePrompt, setNegativePrompt] = useState('')
  const [resolution, setResolution] = useState<ResolutionKey>('landscape')
  const [duration, setDuration] = useState(2)
  const [quality, setQuality] = useState<'preview' | 'quality'>('preview')
  const [seed, setSeed] = useState('42')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { jobs, trackJob, dismissJob } = useJobPoller()

  const refresh = useCallback(async () => {
    try {
      const result = await fetchComfyUITemplates()
      setTemplates(result.items)
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Video backend inventory is unavailable')
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const workflow = useMemo(
    () => templates.find((item) => item.id === 'hunyuan-video-15-t2v') ?? null,
    [templates],
  )
  const missing = workflow?.validation.missing ?? []
  const canSubmit = Boolean(workflow && prompt.trim() && missing.length === 0 && !submitting)

  const submit = async () => {
    if (!workflow || !canSubmit) return
    setSubmitting(true)
    setError(null)
    const dimensions = RESOLUTIONS[resolution]
    try {
      const result = await submitComfyUIJob({
        template_id: workflow.id,
        inputs: {},
        params: {
          prompt: prompt.trim(),
          negative_prompt: negativePrompt.trim(),
          width: dimensions.width,
          height: dimensions.height,
          frames: duration * 24 + 1,
          fps: 24,
          steps: quality === 'quality' ? 50 : 20,
          seed: Number(seed || 42),
          cfg: 1,
          shift: 5,
        },
      })
      trackJob(result.job_id, 'HunyuanVideo 1.5')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Video generation failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <Film className="h-6 w-6 text-primary" /> Video Generation
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Turn a written scene into a short, locally generated video.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr,380px]">
        <div className="space-y-6">
          <Card className={missing.length ? 'border-amber-500/30' : 'border-emerald-500/20'}>
            <CardContent className="flex items-start gap-3 p-4">
              {missing.length ? <AlertTriangle className="mt-0.5 h-5 w-5 text-amber-400" /> : <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-400" />}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-semibold">Local video engine</p>
                  <Badge variant={missing.length ? 'warning' : 'success'}>
                    {!workflow ? 'Checking' : missing.length ? 'Setup required' : 'Ready on demand'}
                  </Badge>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  {missing.length
                    ? `Missing setup files: ${missing.map((item) => item.filename).join(', ')}`
                    : 'HunyuanVideo 1.5 loads only when a job starts; NeonForge prepares UMA automatically.'}
                </p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base"><Sparkles className="h-4 w-4 text-primary" /> 1. Describe the video</CardTitle>
              <CardDescription>Include the subject, action, camera movement, lighting, and visual style.</CardDescription>
            </CardHeader>
            <CardContent>
              <Textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={6} placeholder="A red fox crossing a snowy clearing at sunrise, slow tracking shot, soft golden light, realistic detail…" />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">2. Creative settings</CardTitle>
              <CardDescription>Choose the framing, length, and render quality.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-5 sm:grid-cols-3">
              <div className="space-y-2">
                <Label htmlFor="video-resolution">Frame</Label>
                <select id="video-resolution" value={resolution} onChange={(event) => setResolution(event.target.value as ResolutionKey)} className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm">
                  {Object.entries(RESOLUTIONS).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}
                </select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="video-duration">Duration</Label>
                <select id="video-duration" value={duration} onChange={(event) => setDuration(Number(event.target.value))} className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm">
                  <option value={2}>2 seconds</option><option value={3}>3 seconds</option><option value={5}>5 seconds</option>
                </select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="video-quality">Quality</Label>
                <select id="video-quality" value={quality} onChange={(event) => setQuality(event.target.value as 'preview' | 'quality')} className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm">
                  <option value="preview">Preview · 20 steps</option><option value="quality">Quality · 50 steps</option>
                </select>
              </div>
            </CardContent>
          </Card>

          <details className="rounded-xl border border-border/60 bg-card/50 p-5">
            <summary className="cursor-pointer text-sm font-semibold">Advanced settings</summary>
            <div className="mt-4 grid gap-4 sm:grid-cols-[1fr,180px]">
              <div className="space-y-2"><Label htmlFor="video-negative">Avoid</Label><Textarea id="video-negative" value={negativePrompt} onChange={(event) => setNegativePrompt(event.target.value)} rows={2} placeholder="Artifacts, text, watermark…" /></div>
              <div className="space-y-2"><Label htmlFor="video-seed">Seed</Label><Input id="video-seed" type="number" value={seed} onChange={(event) => setSeed(event.target.value)} /></div>
            </div>
          </details>

          {error && <div className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>}
          <Button size="lg" className="gap-2" disabled={!canSubmit} onClick={() => void submit()}><Send className="h-4 w-4" /> {submitting ? 'Submitting…' : 'Generate video'}</Button>
          <p className="text-xs text-muted-foreground">A cold start includes automatic memory preparation and model loading. Progress and the finished video appear here.</p>
        </div>

        <div>
          <JobTracker jobs={jobs} onDismiss={dismissJob} />
          {jobs.length === 0 && <div className="rounded-lg border border-dashed border-border/50 p-8 text-center"><Film className="mx-auto mb-2 h-8 w-8 text-muted-foreground/30" /><p className="text-sm text-muted-foreground/60">Generated videos will appear here</p></div>}
        </div>
      </div>
    </div>
  )
}
