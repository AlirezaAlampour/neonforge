'use client'

import { useState } from 'react'
import { Mic, Send, Info } from 'lucide-react'
import { useJobPoller } from '@/hooks/use-job-poller'
import { submitTTS } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { AudioRecorder } from '@/components/audio-recorder'
import { FileDropzone } from '@/components/file-dropzone'
import { JobTracker } from '@/components/job-tracker'

export default function VoiceStudioPage() {
  const [text, setText] = useState('')
  const [refText, setRefText] = useState('')
  const [speed, setSpeed] = useState(1.0)
  const [audioSource, setAudioSource] = useState<'record' | 'upload'>('record')
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null)
  const [recordedUrl, setRecordedUrl] = useState<string | null>(null)
  const [uploadedFile, setUploadedFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { jobs, trackJob, dismissJob } = useJobPoller()

  const handleRecorded = (blob: Blob) => {
    setRecordedBlob(blob)
    setRecordedUrl(URL.createObjectURL(blob))
  }

  const handleSubmit = async () => {
    if (!text.trim()) return
    setError(null)
    setSubmitting(true)

    try {
      const formData = new FormData()
      formData.append('text', text)
      formData.append('speed', speed.toString())
      if (refText.trim()) formData.append('ref_text', refText)

      if (audioSource === 'record' && recordedBlob) {
        formData.append('ref_audio', recordedBlob, 'recording.webm')
      } else if (audioSource === 'upload' && uploadedFile) {
        formData.append('ref_audio', uploadedFile)
      }

      const result = await submitTTS(formData)
      trackJob(result.job_id, 'f5tts')
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
          <Mic className="h-6 w-6 text-primary" />
          Voice Studio
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Generate speech with F5-TTS. Optionally clone a voice with reference audio.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr,380px]">
        {/* Main Form */}
        <div className="space-y-6">
          {/* Text Input */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Text to Speak</CardTitle>
              <CardDescription>Enter the text you want to synthesize into speech.</CardDescription>
            </CardHeader>
            <CardContent>
              <Textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Type or paste the script you want to generate..."
                rows={5}
                className="min-h-[120px]"
              />
              <p className="text-xs text-muted-foreground mt-2 text-right">
                {text.length} characters
              </p>
            </CardContent>
          </Card>

          {/* Reference Audio */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Reference Audio (Optional)</CardTitle>
              <CardDescription>
                Provide a voice sample to clone. Record your mic or upload a file.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Source Toggle */}
              <div className="flex rounded-lg border border-border/50 p-1 w-fit">
                <button
                  type="button"
                  onClick={() => setAudioSource('record')}
                  className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                    audioSource === 'record'
                      ? 'bg-primary/10 text-primary'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  Record Mic
                </button>
                <button
                  type="button"
                  onClick={() => setAudioSource('upload')}
                  className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                    audioSource === 'upload'
                      ? 'bg-primary/10 text-primary'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  Upload File
                </button>
              </div>

              {audioSource === 'record' ? (
                <AudioRecorder
                  onRecorded={handleRecorded}
                  audioBlob={recordedBlob}
                  audioUrl={recordedUrl}
                />
              ) : (
                <FileDropzone
                  accept="audio/*"
                  label="Drop reference audio here"
                  hint="WAV, MP3, WebM up to 50 MB"
                  file={uploadedFile}
                  onFileChange={setUploadedFile}
                  maxSizeMB={50}
                  icon="audio"
                />
              )}

              {/* Reference text */}
              <div className="space-y-2 pt-2 border-t border-border/30">
                <Label htmlFor="ref-text" className="text-xs text-muted-foreground">
                  Reference transcript (what the audio says — helps accuracy)
                </Label>
                <Input
                  id="ref-text"
                  value={refText}
                  onChange={(e) => setRefText(e.target.value)}
                  placeholder="Optional: transcript of the reference audio..."
                />
              </div>
            </CardContent>
          </Card>

          {/* Parameters */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Parameters</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-sm">Speed</Label>
                    <span className="text-sm font-mono text-muted-foreground tabular-nums">
                      {speed.toFixed(1)}x
                    </span>
                  </div>
                  <Slider value={speed} onChange={setSpeed} min={0.5} max={2.0} step={0.1} />
                  <div className="flex justify-between text-[10px] text-muted-foreground/60">
                    <span>Slow</span>
                    <span>Normal</span>
                    <span>Fast</span>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Submit */}
          <div className="flex items-center gap-4">
            <Button
              onClick={handleSubmit}
              disabled={!text.trim() || submitting}
              className="gap-2 px-6"
              size="lg"
            >
              <Send className="h-4 w-4" />
              {submitting ? 'Submitting...' : 'Generate Voice'}
            </Button>
            {error && <p className="text-sm text-red-400">{error}</p>}
          </div>

          {/* HTTPS hint */}
          <div className="flex items-start gap-2 rounded-lg bg-secondary/30 p-3 text-xs text-muted-foreground">
            <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            <p>
              Mic recording requires HTTPS. Use <code className="text-primary/80">npm run dev:https</code> or
              add your DGX IP to Chrome&apos;s{' '}
              <code className="text-primary/80">chrome://flags/#unsafely-treat-insecure-origin-as-secure</code>.
            </p>
          </div>
        </div>

        {/* Right Sidebar: Jobs */}
        <div>
          <JobTracker jobs={jobs} onDismiss={dismissJob} />
          {jobs.length === 0 && (
            <div className="rounded-lg border border-dashed border-border/50 p-8 text-center">
              <Mic className="h-8 w-8 mx-auto text-muted-foreground/30 mb-2" />
              <p className="text-sm text-muted-foreground/60">
                Generated audio will appear here
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
