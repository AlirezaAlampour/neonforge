'use client'

import { useState } from 'react'
import { useJobPoller } from '@/hooks/use-job-poller'
import { useSystemStatus } from '@/hooks/use-system-status'
import { submitLipSync } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { FileDropzone } from '@/components/file-dropzone'
import { InputPreview } from '@/components/input-preview'
import { JobTracker } from '@/components/job-tracker'

type Take = { video: File; audio: File }
export default function LipSyncPage() {
  const [video, setVideo] = useState<File | null>(null)
  const [audio, setAudio] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [takes, setTakes] = useState<Record<string, Take>>({})
  const { jobs, trackJob, dismissJob } = useJobPoller()
  const { services, loading } = useSystemStatus(10000)
  const ready = Boolean(services?.lipsync?.ready)
  const busy = submitting || jobs.some((job) => !['completed', 'failed'].includes(job.status))
  const submit = async (take?: Take) => {
    const values = take ?? (video && audio ? { video, audio } : null)
    if (!values || !ready || busy) return
    setError(null)
    setSubmitting(true)
    try {
      const data = new FormData()
      data.append('video', values.video)
      data.append('audio', values.audio)
      const result = await submitLipSync(data)
      setTakes((previous) => ({ ...previous, [result.job_id]: values }))
      trackJob(result.job_id, 'Lip Sync')
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to start Lip Sync. Please try again.') }
    finally { setSubmitting(false) }
  }
  return <div className="space-y-8">
    <header><h1 className="text-3xl font-semibold tracking-tight">Lip Sync</h1><p className="mt-2 text-muted-foreground">Match a performance to your audio.</p></header>
    <div className="grid gap-8 md:grid-cols-2">
      <section className="space-y-4"><div><h2 className="font-medium">Source video</h2><p className="mt-1 text-sm text-muted-foreground">Choose a clip with a clearly visible face.</p></div><FileDropzone accept="video/*" label="Drop your video" hint="MP4, MOV or WebM · up to 500 MB" file={video} onFileChange={setVideo} maxSizeMB={500} icon="video" /><InputPreview file={video} kind="video" /></section>
      <section className="space-y-4"><div><h2 className="font-medium">Audio</h2><p className="mt-1 text-sm text-muted-foreground">Upload speech or a take from Voiceover.</p></div><FileDropzone accept="audio/*" label="Drop your audio" hint="WAV or MP3 · up to 50 MB" file={audio} onFileChange={setAudio} maxSizeMB={50} icon="audio" /><InputPreview file={audio} kind="audio" /></section>
    </div>
    <Button size="lg" disabled={!video || !audio || !ready || busy} onClick={() => void submit()}>{submitting ? 'Uploading…' : busy ? 'Generation in progress' : 'Generate lip sync'}</Button>
    {loading && <p className="text-sm text-muted-foreground">Checking availability…</p>}
    {!loading && !ready && <p role="status" className="text-sm text-amber-300">Lip Sync needs setup. See System Info for details.</p>}
    {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
    <JobTracker canReuse={(id) => Boolean(takes[id])} jobs={jobs} onDismiss={(id) => { dismissJob(id); setTakes((previous) => { const next = { ...previous }; delete next[id]; return next }) }} onReuse={(id) => { if (takes[id]) { setVideo(takes[id].video); setAudio(takes[id].audio) } }} onRegenerate={(id) => { if (takes[id]) void submit(takes[id]) }} actionsDisabled={busy} />
    {!jobs.length && <p className="py-12 text-center text-sm text-muted-foreground">Your synced video will appear here, ready to preview and download.</p>}
  </div>
}
