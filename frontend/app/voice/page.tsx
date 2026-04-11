'use client'

import { useEffect, useMemo, useState } from 'react'
import { Info, Mic, Send } from 'lucide-react'
import { AudioRecorder } from '@/components/audio-recorder'
import { FileDropzone } from '@/components/file-dropzone'
import { JobTracker } from '@/components/job-tracker'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { Textarea } from '@/components/ui/textarea'
import { useJobPoller } from '@/hooks/use-job-poller'
import { fetchTTSProviders, fetchVoiceAssets, submitTTSJob } from '@/lib/api'
import type { AssetItem, TTSProvider } from '@/lib/types'

type VoiceMode = 'none' | 'saved' | 'record' | 'upload'

function normalizeOptionValue(provider: TTSProvider | null, optionId: string, value: unknown): unknown {
  const field = provider?.option_fields.find((item) => item.id === optionId)
  if (!field) return value
  if (field.type === 'number' || field.type === 'integer') {
    const numeric = Number(value)
    return Number.isFinite(numeric) ? numeric : field.default
  }
  if (field.type === 'boolean') return Boolean(value)
  return value
}

export default function VoiceStudioPage() {
  const [providers, setProviders] = useState<TTSProvider[]>([])
  const [voiceAssets, setVoiceAssets] = useState<AssetItem[]>([])
  const [providerId, setProviderId] = useState('f5tts')
  const [text, setText] = useState('')
  const [speakerName, setSpeakerName] = useState('')
  const [referenceText, setReferenceText] = useState('')
  const [transcript, setTranscript] = useState('')
  const [stylePrompt, setStylePrompt] = useState('')
  const [outputFormat, setOutputFormat] = useState('')
  const [targetSampleRate, setTargetSampleRate] = useState('')
  const [optionValues, setOptionValues] = useState<Record<string, unknown>>({})
  const [voiceMode, setVoiceMode] = useState<VoiceMode>('none')
  const [savedVoicePath, setSavedVoicePath] = useState('')
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null)
  const [recordedUrl, setRecordedUrl] = useState<string | null>(null)
  const [uploadedFile, setUploadedFile] = useState<File | null>(null)
  const [continuationFile, setContinuationFile] = useState<File | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { jobs, trackJob, dismissJob } = useJobPoller()

  const enabledProviders = useMemo(() => providers.filter((provider) => provider.enabled), [providers])
  const selectedProvider = useMemo(
    () => enabledProviders.find((provider) => provider.provider_id === providerId) ?? enabledProviders[0] ?? null,
    [enabledProviders, providerId],
  )

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const [providerResponse, voicesResponse] = await Promise.all([
          fetchTTSProviders(),
          fetchVoiceAssets(),
        ])
        setProviders(providerResponse.items)
        setVoiceAssets(voicesResponse.items)
        const firstEnabled = providerResponse.items.find((provider) => provider.enabled)
        if (firstEnabled) {
          setProviderId((current) =>
            providerResponse.items.some((provider) => provider.enabled && provider.provider_id === current)
              ? current
              : firstEnabled.provider_id,
          )
        }
        setError(null)
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to load TTS providers')
      } finally {
        setLoading(false)
      }
    }

    void load()
  }, [])

  useEffect(() => {
    if (!selectedProvider) return

    setOutputFormat((current) => {
      const next =
        current && selectedProvider.supported_output_formats.includes(current)
          ? current
          : selectedProvider.default_output_format || selectedProvider.supported_output_formats[0] || ''
      return next
    })

    setTargetSampleRate((current) => {
      if (
        current &&
        selectedProvider.supported_target_sample_rates.includes(Number(current))
      ) {
        return current
      }
      return selectedProvider.default_target_sample_rate
        ? String(selectedProvider.default_target_sample_rate)
        : ''
    })

    setOptionValues((current) => {
      const next: Record<string, unknown> = {}
      for (const field of selectedProvider.option_fields) {
        next[field.id] = current[field.id] ?? field.default ?? ''
      }
      return next
    })

    if (!selectedProvider.capabilities.supports_reference_audio) {
      setVoiceMode('none')
      setSavedVoicePath('')
      setRecordedBlob(null)
      setRecordedUrl(null)
      setUploadedFile(null)
      setReferenceText('')
    }
    if (!selectedProvider.capabilities.continuation_edit) {
      setContinuationFile(null)
    }
    if (!selectedProvider.capabilities.transcript_guided_continuation) {
      setTranscript('')
    }
    if (!selectedProvider.capabilities.style_prompt) {
      setStylePrompt('')
    }
  }, [selectedProvider?.provider_id])

  const handleRecorded = (blob: Blob) => {
    setRecordedBlob(blob)
    setRecordedUrl(URL.createObjectURL(blob))
  }

  const handleSubmit = async () => {
    if (!selectedProvider || !text.trim()) return

    setError(null)
    setSubmitting(true)

    try {
      const formData = new FormData()
      formData.append('provider', selectedProvider.provider_id)
      formData.append('text', text.trim())
      if (speakerName.trim()) formData.append('speaker_name', speakerName.trim())
      if (referenceText.trim()) formData.append('reference_text', referenceText.trim())
      if (transcript.trim()) formData.append('transcript', transcript.trim())
      if (stylePrompt.trim()) formData.append('style_prompt', stylePrompt.trim())
      if (outputFormat) formData.append('output_format', outputFormat)
      if (targetSampleRate) formData.append('target_sample_rate', targetSampleRate)

      const normalizedOptions: Record<string, unknown> = {}
      for (const field of selectedProvider.option_fields) {
        const value = optionValues[field.id]
        if (value === '' || value === null || value === undefined) continue
        normalizedOptions[field.id] = normalizeOptionValue(selectedProvider, field.id, value)
      }
      if (Object.keys(normalizedOptions).length > 0) {
        formData.append('options', JSON.stringify(normalizedOptions))
      }

      if (selectedProvider.capabilities.supports_reference_audio) {
        if (voiceMode === 'saved' && savedVoicePath) {
          formData.append('reference_audio_path', savedVoicePath)
        } else if (voiceMode === 'record') {
          if (!recordedBlob) {
            setError('Record a reference clip or switch the voice source mode.')
            return
          }
          formData.append('reference_audio', recordedBlob, 'recording.webm')
        } else if (voiceMode === 'upload') {
          if (!uploadedFile) {
            setError('Upload a reference clip or switch the voice source mode.')
            return
          }
          formData.append('reference_audio', uploadedFile)
        }
      }

      if (selectedProvider.capabilities.continuation_edit && continuationFile) {
        formData.append('continuation_audio', continuationFile)
      }

      const result = await submitTTSJob(formData)
      trackJob(result.job_id, result.provider ?? selectedProvider.provider_id)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Request failed')
    } finally {
      setSubmitting(false)
    }
  }

  const hasReferenceAudio = selectedProvider?.capabilities.supports_reference_audio ?? false
  const hasTranscript = selectedProvider?.capabilities.transcript_guided_continuation ?? false
  const hasContinuation = selectedProvider?.capabilities.continuation_edit ?? false
  const hasStylePrompt = selectedProvider?.capabilities.style_prompt ?? false
  const showSampleRate = (selectedProvider?.supported_target_sample_rates.length ?? 0) > 1
  const showOutputFormat = (selectedProvider?.supported_output_formats.length ?? 0) > 1

  return (
    <div className="space-y-8">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <Mic className="h-6 w-6 text-primary" />
          Voice Studio
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Select a TTS provider and the form will adapt to the capabilities it reports.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr,380px]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Provider</CardTitle>
              <CardDescription>NeonForge discovers available TTS providers from the gateway registry.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <select
                value={selectedProvider?.provider_id ?? ''}
                onChange={(e) => setProviderId(e.target.value)}
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                disabled={loading || enabledProviders.length === 0}
              >
                {enabledProviders.map((provider) => (
                  <option key={provider.provider_id} value={provider.provider_id}>
                    {provider.display_name}
                  </option>
                ))}
              </select>
              {selectedProvider && (
                <p className="text-xs text-muted-foreground">
                  {selectedProvider.description || `${selectedProvider.display_name} is ready for NeonForge jobs.`}
                </p>
              )}
              {!loading && enabledProviders.length === 0 && (
                <p className="text-sm text-red-400">No enabled TTS providers were returned by the gateway.</p>
              )}
            </CardContent>
          </Card>

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
              <p className="mt-2 text-right text-xs text-muted-foreground">{text.length} characters</p>
            </CardContent>
          </Card>

          {hasReferenceAudio && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Reference Audio</CardTitle>
                <CardDescription>
                  Use a saved voice, record a quick sample, or upload a reference clip.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex flex-wrap gap-2">
                  <Button type="button" variant={voiceMode === 'none' ? 'default' : 'outline'} onClick={() => setVoiceMode('none')}>
                    None
                  </Button>
                  <Button type="button" variant={voiceMode === 'saved' ? 'default' : 'outline'} onClick={() => setVoiceMode('saved')}>
                    Saved Voice
                  </Button>
                  <Button type="button" variant={voiceMode === 'record' ? 'default' : 'outline'} onClick={() => setVoiceMode('record')}>
                    Record Mic
                  </Button>
                  <Button type="button" variant={voiceMode === 'upload' ? 'default' : 'outline'} onClick={() => setVoiceMode('upload')}>
                    Upload File
                  </Button>
                </div>

                {voiceMode === 'saved' && (
                  <select
                    value={savedVoicePath}
                    onChange={(e) => setSavedVoicePath(e.target.value)}
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                  >
                    <option value="">Select saved voice</option>
                    {voiceAssets.map((asset) => (
                      <option key={asset.path} value={asset.path}>
                        {asset.name}
                      </option>
                    ))}
                  </select>
                )}

                {voiceMode === 'record' && (
                  <AudioRecorder onRecorded={handleRecorded} audioBlob={recordedBlob} audioUrl={recordedUrl} />
                )}

                {voiceMode === 'upload' && (
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

                <div className="space-y-2 border-t border-border/30 pt-2">
                  <Label htmlFor="reference-text" className="text-xs text-muted-foreground">
                    Reference transcript
                  </Label>
                  <Input
                    id="reference-text"
                    value={referenceText}
                    onChange={(e) => setReferenceText(e.target.value)}
                    placeholder="Optional transcript of the reference clip..."
                  />
                </div>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Provider Controls</CardTitle>
              <CardDescription>Only controls reported by the selected provider are shown.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="speaker-name">Speaker / Reference Name</Label>
                <Input
                  id="speaker-name"
                  value={speakerName}
                  onChange={(e) => setSpeakerName(e.target.value)}
                  placeholder="Optional provider-specific speaker or reference identifier"
                />
              </div>

              {hasStylePrompt && (
                <div className="space-y-2">
                  <Label htmlFor="style-prompt">Style Prompt</Label>
                  <Textarea
                    id="style-prompt"
                    value={stylePrompt}
                    onChange={(e) => setStylePrompt(e.target.value)}
                    rows={3}
                    placeholder="Describe the intended delivery, pacing, or tone..."
                  />
                </div>
              )}

              {hasContinuation && (
                <FileDropzone
                  accept="audio/*"
                  label="Drop continuation seed audio here"
                  hint="Optional seed clip for continuation/edit capable providers"
                  file={continuationFile}
                  onFileChange={setContinuationFile}
                  maxSizeMB={50}
                  icon="audio"
                />
              )}

              {hasTranscript && (
                <div className="space-y-2">
                  <Label htmlFor="continuation-transcript">Transcript Guidance</Label>
                  <Textarea
                    id="continuation-transcript"
                    value={transcript}
                    onChange={(e) => setTranscript(e.target.value)}
                    rows={3}
                    placeholder="Optional transcript or continuation guidance..."
                  />
                </div>
              )}

              {showOutputFormat && (
                <div className="space-y-2">
                  <Label htmlFor="output-format">Output Format</Label>
                  <select
                    id="output-format"
                    value={outputFormat}
                    onChange={(e) => setOutputFormat(e.target.value)}
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                  >
                    {selectedProvider?.supported_output_formats.map((format) => (
                      <option key={format} value={format}>
                        {format.toUpperCase()}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {showSampleRate && (
                <div className="space-y-2">
                  <Label htmlFor="target-sample-rate">Target Sample Rate</Label>
                  <select
                    id="target-sample-rate"
                    value={targetSampleRate}
                    onChange={(e) => setTargetSampleRate(e.target.value)}
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                  >
                    {selectedProvider?.supported_target_sample_rates.map((sampleRate) => (
                      <option key={sampleRate} value={sampleRate}>
                        {sampleRate.toLocaleString()} Hz
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {selectedProvider?.option_fields.map((field) => {
                const value = optionValues[field.id]
                const numericValue = typeof value === 'number' ? value : Number(value ?? field.default ?? 0)
                if ((field.type === 'number' || field.type === 'integer') && field.min != null && field.max != null) {
                  return (
                    <div key={field.id} className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label>{field.label}</Label>
                        <span className="text-sm font-mono text-muted-foreground">{numericValue.toFixed(2)}</span>
                      </div>
                      <Slider
                        value={numericValue}
                        onChange={(next) => setOptionValues((current) => ({ ...current, [field.id]: next }))}
                        min={field.min}
                        max={field.max}
                        step={field.step ?? 0.1}
                      />
                      {field.description && <p className="text-xs text-muted-foreground">{field.description}</p>}
                    </div>
                  )
                }
                return (
                  <div key={field.id} className="space-y-2">
                    <Label htmlFor={`option-${field.id}`}>{field.label}</Label>
                    <Input
                      id={`option-${field.id}`}
                      type={field.type === 'number' || field.type === 'integer' ? 'number' : 'text'}
                      value={String(value ?? field.default ?? '')}
                      onChange={(e) => setOptionValues((current) => ({ ...current, [field.id]: e.target.value }))}
                    />
                    {field.description && <p className="text-xs text-muted-foreground">{field.description}</p>}
                  </div>
                )
              })}
            </CardContent>
          </Card>

          <div className="flex items-center gap-4">
            <Button onClick={handleSubmit} disabled={!text.trim() || submitting || !selectedProvider} className="gap-2 px-6" size="lg">
              <Send className="h-4 w-4" />
              {submitting ? 'Submitting...' : 'Generate Voice'}
            </Button>
            {error && <p className="text-sm text-red-400">{error}</p>}
          </div>

          <div className="flex items-start gap-2 rounded-lg bg-secondary/30 p-3 text-xs text-muted-foreground">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <p>
              Mic recording requires HTTPS. Use <code className="text-primary/80">npm run dev:https</code> or
              add your DGX IP to Chrome&apos;s{' '}
              <code className="text-primary/80">chrome://flags/#unsafely-treat-insecure-origin-as-secure</code>.
            </p>
          </div>
        </div>

        <div>
          <JobTracker jobs={jobs} onDismiss={dismissJob} />
          {jobs.length === 0 && (
            <div className="rounded-lg border border-dashed border-border/50 p-8 text-center">
              <Mic className="mx-auto mb-2 h-8 w-8 text-muted-foreground/30" />
              <p className="text-sm text-muted-foreground/60">
                Jobs will appear here as soon as you submit a synthesis request.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
