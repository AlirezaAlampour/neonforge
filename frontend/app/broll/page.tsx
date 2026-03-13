'use client'

import { useState } from 'react'
import { Video, Send, ChevronDown, ChevronUp, Sparkles } from 'lucide-react'
import { useJobPoller } from '@/hooks/use-job-poller'
import { submitWan21 } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { Input } from '@/components/ui/input'
import { JobTracker } from '@/components/job-tracker'

export default function BRollStudioPage() {
  const [prompt, setPrompt] = useState('')
  const [negativePrompt, setNegativePrompt] = useState('')
  const [numFrames, setNumFrames] = useState(16)
  const [width, setWidth] = useState(512)
  const [height, setHeight] = useState(512)
  const [steps, setSteps] = useState(25)
  const [guidance, setGuidance] = useState(7.5)
  const [seed, setSeed] = useState(-1)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { jobs, trackJob, dismissJob } = useJobPoller()

  const handleSubmit = async () => {
    if (!prompt.trim()) return
    setError(null)
    setSubmitting(true)

    try {
      const result = await submitWan21({
        prompt: prompt.trim(),
        negative_prompt: negativePrompt.trim(),
        num_frames: numFrames,
        width,
        height,
        num_inference_steps: steps,
        guidance_scale: guidance,
        seed,
      })
      trackJob(result.job_id, 'wan21')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Request failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
          <Video className="h-6 w-6 text-primary" />
          B-Roll Studio
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Generate cinematic B-roll clips with Wan 2.1 text-to-video.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr,380px]">
        {/* Main Form */}
        <div className="space-y-6">
          {/* Prompt */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-primary" />
                Prompt
              </CardTitle>
              <CardDescription>
                Describe the scene you want to generate. Be specific about motion, lighting, and style.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="A drone shot flying over a misty mountain range at golden hour, cinematic 4K, smooth camera movement..."
                rows={4}
                className="min-h-[100px]"
              />
            </CardContent>
          </Card>

          {/* Core Parameters */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Generation Settings</CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              {/* Frames */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label className="text-sm">Frames</Label>
                  <span className="text-sm font-mono text-muted-foreground tabular-nums">
                    {numFrames} ({(numFrames / 8).toFixed(1)}s at 8fps)
                  </span>
                </div>
                <Slider value={numFrames} onChange={(v) => setNumFrames(Math.round(v))} min={8} max={80} step={8} />
              </div>

              {/* Resolution */}
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label className="text-sm">Width</Label>
                  <div className="flex items-center gap-2">
                    <Slider value={width} onChange={(v) => setWidth(Math.round(v))} min={256} max={512} step={64} />
                    <span className="text-xs font-mono text-muted-foreground w-8">{width}</span>
                  </div>
                </div>
                <div className="space-y-2">
                  <Label className="text-sm">Height</Label>
                  <div className="flex items-center gap-2">
                    <Slider value={height} onChange={(v) => setHeight(Math.round(v))} min={256} max={512} step={64} />
                    <span className="text-xs font-mono text-muted-foreground w-8">{height}</span>
                  </div>
                </div>
              </div>

              {/* Guidance Scale */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label className="text-sm">Guidance Scale</Label>
                  <span className="text-sm font-mono text-muted-foreground tabular-nums">{guidance.toFixed(1)}</span>
                </div>
                <Slider value={guidance} onChange={setGuidance} min={1} max={20} step={0.5} />
                <div className="flex justify-between text-[10px] text-muted-foreground/60">
                  <span>Creative</span>
                  <span>Balanced</span>
                  <span>Precise</span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Advanced (collapsible) */}
          <Card>
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="flex w-full items-center justify-between p-5 text-left"
            >
              <span className="text-sm font-semibold">Advanced Settings</span>
              {showAdvanced ? (
                <ChevronUp className="h-4 w-4 text-muted-foreground" />
              ) : (
                <ChevronDown className="h-4 w-4 text-muted-foreground" />
              )}
            </button>
            {showAdvanced && (
              <CardContent className="space-y-5 pt-0 border-t border-border/30">
                {/* Negative Prompt */}
                <div className="space-y-2 pt-4">
                  <Label className="text-sm">Negative Prompt</Label>
                  <Textarea
                    value={negativePrompt}
                    onChange={(e) => setNegativePrompt(e.target.value)}
                    placeholder="blurry, low quality, distorted, watermark..."
                    rows={2}
                  />
                </div>

                {/* Steps */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-sm">Inference Steps</Label>
                    <span className="text-sm font-mono text-muted-foreground tabular-nums">{steps}</span>
                  </div>
                  <Slider value={steps} onChange={(v) => setSteps(Math.round(v))} min={10} max={50} step={5} />
                </div>

                {/* Seed */}
                <div className="space-y-2">
                  <Label className="text-sm">Seed (-1 for random)</Label>
                  <Input
                    type="number"
                    value={seed}
                    onChange={(e) => setSeed(parseInt(e.target.value) || -1)}
                    className="w-40 font-mono"
                  />
                </div>
              </CardContent>
            )}
          </Card>

          {/* Submit */}
          <div className="flex items-center gap-4">
            <Button onClick={handleSubmit} disabled={!prompt.trim() || submitting} className="gap-2 px-6" size="lg">
              <Send className="h-4 w-4" />
              {submitting ? 'Submitting...' : 'Generate B-Roll'}
            </Button>
            {error && <p className="text-sm text-red-400">{error}</p>}
          </div>

          <p className="text-xs text-muted-foreground/60">
            Wan 2.1 is lazy-start: the first generation may take longer while the model loads (~8-40 GB UMA).
          </p>
        </div>

        {/* Right Sidebar: Jobs */}
        <div>
          <JobTracker jobs={jobs} onDismiss={dismissJob} />
          {jobs.length === 0 && (
            <div className="rounded-lg border border-dashed border-border/50 p-8 text-center">
              <Video className="h-8 w-8 mx-auto text-muted-foreground/30 mb-2" />
              <p className="text-sm text-muted-foreground/60">Generated videos will appear here</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
