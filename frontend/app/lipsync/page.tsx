'use client'

import { useState } from 'react'
import { Clapperboard, Send } from 'lucide-react'
import { useJobPoller } from '@/hooks/use-job-poller'
import { submitLipSync } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { FileDropzone } from '@/components/file-dropzone'
import { JobTracker } from '@/components/job-tracker'

export default function LipSyncStudioPage() {
  const [videoFile, setVideoFile] = useState<File | null>(null)
  const [audioFile, setAudioFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { jobs, trackJob, dismissJob } = useJobPoller()

  const handleSubmit = async () => {
    if (!videoFile || !audioFile) return
    setError(null)
    setSubmitting(true)

    try {
      const formData = new FormData()
      formData.append('video', videoFile)
      formData.append('audio', audioFile)

      const result = await submitLipSync(formData)
      trackJob(result.job_id, 'lipsync')
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
          <Clapperboard className="h-6 w-6 text-primary" />
          Lip Sync Studio
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Sync lip movements to audio using Video-Retalking or SadTalker.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr,380px]">
        {/* Main Form */}
        <div className="space-y-6">
          {/* Source Video */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Source Video</CardTitle>
              <CardDescription>
                Upload the video with the face you want to lip-sync.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <FileDropzone
                accept="video/*"
                label="Drop source video here"
                hint="MP4, WebM, MOV up to 500 MB"
                file={videoFile}
                onFileChange={setVideoFile}
                maxSizeMB={500}
                icon="video"
              />
              {videoFile && (
                <div className="mt-3">
                  <video
                    controls
                    className="w-full rounded-lg border border-border/30 max-h-56 bg-black"
                    src={URL.createObjectURL(videoFile)}
                  />
                </div>
              )}
            </CardContent>
          </Card>

          {/* Driving Audio */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Driving Audio</CardTitle>
              <CardDescription>
                The audio track to sync the lips to. This can be a TTS output from Voice Studio.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <FileDropzone
                accept="audio/*"
                label="Drop driving audio here"
                hint="WAV, MP3, WebM up to 50 MB"
                file={audioFile}
                onFileChange={setAudioFile}
                maxSizeMB={50}
                icon="audio"
              />
              {audioFile && (
                <div className="mt-3">
                  <audio controls className="w-full h-8 [&::-webkit-media-controls-panel]:bg-secondary rounded">
                    <source src={URL.createObjectURL(audioFile)} />
                  </audio>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Submit */}
          <div className="flex items-center gap-4">
            <Button
              onClick={handleSubmit}
              disabled={!videoFile || !audioFile || submitting}
              className="gap-2 px-6"
              size="lg"
            >
              <Send className="h-4 w-4" />
              {submitting ? 'Submitting...' : 'Generate Lip Sync'}
            </Button>
            {error && <p className="text-sm text-red-400">{error}</p>}
          </div>

          <p className="text-xs text-muted-foreground/60">
            Processing time depends on video length. Expect 1-5 minutes for a typical clip.
          </p>
        </div>

        {/* Right Sidebar: Jobs */}
        <div>
          <JobTracker jobs={jobs} onDismiss={dismissJob} />
          {jobs.length === 0 && (
            <div className="rounded-lg border border-dashed border-border/50 p-8 text-center">
              <Clapperboard className="h-8 w-8 mx-auto text-muted-foreground/30 mb-2" />
              <p className="text-sm text-muted-foreground/60">
                Synced videos will appear here
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
