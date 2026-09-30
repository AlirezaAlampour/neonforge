'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Boxes, RefreshCw, Send, UploadCloud } from 'lucide-react'
import { FileDropzone } from '@/components/file-dropzone'
import { JobTracker } from '@/components/job-tracker'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useJobPoller } from '@/hooks/use-job-poller'
import {
  fetchComfyUIAssets,
  fetchComfyUITemplates,
  submitComfyUIJob,
  uploadComfyUIAsset,
} from '@/lib/api'
import type { ComfyUIAsset, ComfyUITemplate } from '@/lib/types'

type CharacterMode = 'replace' | 'animate'

export default function CharacterPage() {
  const [mode, setMode] = useState<CharacterMode>('replace')
  const [templates, setTemplates] = useState<ComfyUITemplate[]>([])
  const [assets, setAssets] = useState<ComfyUIAsset[]>([])
  const [referenceId, setReferenceId] = useState('')
  const [drivingId, setDrivingId] = useState('')
  const [referenceFile, setReferenceFile] = useState<File | null>(null)
  const [drivingFile, setDrivingFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState<'image' | 'video' | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [seed, setSeed] = useState('42')
  const [steps, setSteps] = useState(4)
  const [frameRate, setFrameRate] = useState(16)
  const { jobs, trackJob, dismissJob } = useJobPoller()

  const refresh = useCallback(async () => {
    setRefreshing(true)
    try {
      const [templateResult, assetResult] = await Promise.all([
        fetchComfyUITemplates(),
        fetchComfyUIAssets(),
      ])
      setTemplates(templateResult.items)
      setAssets(assetResult.items)
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Character backend inventory is unavailable')
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const workflow = useMemo(
    () => templates.find((item) => item.id === 'wan-character-swap') ?? null,
    [templates],
  )
  const images = useMemo(() => assets.filter((item) => item.kind === 'image'), [assets])
  const videos = useMemo(() => assets.filter((item) => item.kind === 'video'), [assets])
  const missing = workflow?.validation.missing ?? []
  const canSubmit = Boolean(
    mode === 'replace' && workflow && referenceId && drivingId && missing.length === 0 && !submitting,
  )

  const upload = async (kind: 'image' | 'video') => {
    const file = kind === 'image' ? referenceFile : drivingFile
    if (!file) return
    setUploading(kind)
    setError(null)
    try {
      const asset = await uploadComfyUIAsset(file, kind)
      if (kind === 'image') {
        setReferenceId(asset.id)
        setReferenceFile(null)
      } else {
        setDrivingId(asset.id)
        setDrivingFile(null)
      }
      await refresh()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(null)
    }
  }

  const submit = async () => {
    if (!workflow || !canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const result = await submitComfyUIJob({
        template_id: workflow.id,
        inputs: { reference_image: referenceId, driving_video: drivingId },
        params: {
          seed: Number(seed || 42),
          steps,
          cfg: 1,
          denoise_strength: 0.9,
          frame_rate: frameRate,
          person_index: 0,
        },
      })
      trackJob(result.job_id, 'Wan 2.2 Character Replace')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Character generation failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
            <Boxes className="h-6 w-6 text-primary" /> Character
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Preserve motion from a driving clip while replacing or animating its character.
          </p>
        </div>
        <Button variant="outline" size="sm" className="gap-2" onClick={() => void refresh()}>
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>

      <div className="flex gap-2" role="tablist" aria-label="Character mode">
        <Button variant={mode === 'replace' ? 'default' : 'outline'} onClick={() => setMode('replace')}>
          Replace
        </Button>
        <Button variant={mode === 'animate' ? 'default' : 'outline'} onClick={() => setMode('animate')}>
          Animate
        </Button>
      </div>

      {mode === 'animate' ? (
        <Card className="border-amber-500/30">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <AlertTriangle className="h-4 w-4 text-amber-400" /> Animate workflow not yet validated
            </CardTitle>
            <CardDescription>
              NeonForge will not route Animate through the Replace graph. This mode remains disabled until its
              distinct Wan 2.2 graph completes a real render on this host.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1fr,360px]">
          <div className="space-y-6">
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <CardTitle className="text-base">Wan 2.2 Character Replace</CardTitle>
                    <CardDescription>Reference character + driving video → replaced-character video.</CardDescription>
                  </div>
                  <Badge variant={missing.length ? 'danger' : 'success'}>
                    {missing.length ? `${missing.length} model files missing` : 'Models found'}
                  </Badge>
                </div>
              </CardHeader>
              {missing.length > 0 && (
                <CardContent>
                  <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">
                    {missing.map((item) => item.filename).join(', ')}
                  </div>
                </CardContent>
              )}
            </Card>

            <div className="grid gap-6 xl:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">1. Reference character</CardTitle>
                  <CardDescription>The appearance to place into the driving clip.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <select value={referenceId} onChange={(e) => setReferenceId(e.target.value)} className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm">
                    <option value="">Select uploaded image</option>
                    {images.map((item) => <option key={item.id} value={item.id}>{item.original_filename}</option>)}
                  </select>
                  <FileDropzone accept="image/*" label="Drop reference image" hint="PNG, JPG, or WebP up to 20 MB" file={referenceFile} onFileChange={setReferenceFile} maxSizeMB={20} icon="image" />
                  <Button variant="outline" className="gap-2" disabled={!referenceFile || uploading === 'image'} onClick={() => void upload('image')}>
                    <UploadCloud className="h-4 w-4" /> {uploading === 'image' ? 'Uploading…' : 'Upload image'}
                  </Button>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">2. Driving video</CardTitle>
                  <CardDescription>The scene, camera, and motion to preserve. This validated preview path uses the first 17 frames.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <select value={drivingId} onChange={(e) => setDrivingId(e.target.value)} className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm">
                    <option value="">Select uploaded video</option>
                    {videos.map((item) => <option key={item.id} value={item.id}>{item.original_filename}</option>)}
                  </select>
                  <FileDropzone accept="video/*" label="Drop driving video" hint="MP4, MOV, or WebM up to 500 MB" file={drivingFile} onFileChange={setDrivingFile} maxSizeMB={500} icon="video" />
                  <Button variant="outline" className="gap-2" disabled={!drivingFile || uploading === 'video'} onClick={() => void upload('video')}>
                    <UploadCloud className="h-4 w-4" /> {uploading === 'video' ? 'Uploading…' : 'Upload video'}
                  </Button>
                </CardContent>
              </Card>
            </div>

            <details className="rounded-xl border border-border/60 bg-card/50 p-5">
              <summary className="cursor-pointer text-sm font-semibold">Advanced settings</summary>
              <div className="mt-4 grid gap-4 sm:grid-cols-3">
                <div className="space-y-2"><Label htmlFor="character-seed">Seed</Label><Input id="character-seed" value={seed} onChange={(e) => setSeed(e.target.value)} /></div>
                <div className="space-y-2"><Label htmlFor="character-steps">Steps</Label><Input id="character-steps" type="number" min={1} max={60} value={steps} onChange={(e) => setSteps(Number(e.target.value))} /></div>
                <div className="space-y-2"><Label htmlFor="character-fps">Frame rate</Label><Input id="character-fps" type="number" min={1} max={60} value={frameRate} onChange={(e) => setFrameRate(Number(e.target.value))} /></div>
              </div>
            </details>

            {error && <div className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>}
            <Button size="lg" className="gap-2" disabled={!canSubmit} onClick={() => void submit()}>
              <Send className="h-4 w-4" /> {submitting ? 'Submitting…' : 'Generate character video'}
            </Button>
            <p className="text-xs text-muted-foreground">GPU memory is reclaimed automatically. The first run may spend several minutes loading model weights.</p>
          </div>

          <div><JobTracker jobs={jobs} onDismiss={dismissJob} /></div>
        </div>
      )}
    </div>
  )
}
