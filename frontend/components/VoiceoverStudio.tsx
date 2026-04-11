'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronUp,
  Download,
  History,
  Loader2,
  Mic2,
  Play,
  RefreshCw,
  Trash2,
  UploadCloud,
  Wand2,
} from 'lucide-react'
import { FileDropzone } from '@/components/file-dropzone'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Slider } from '@/components/ui/slider'
import { Textarea } from '@/components/ui/textarea'

interface VoiceProfile {
  id: string
  name: string
  reference_audio_path: string
  created_at: string
  notes?: string | null
}

interface VoiceoverModelSummary {
  model_id: string
  display_name: string
  supports_reference_audio: boolean
  available: boolean
}

interface VoiceoverJobStatus {
  status: string
  total_chunks?: number
  completed_chunks?: number
  error?: string
  output_path?: string
  output_url?: string
  created_at?: string
}

interface RecentVoiceover {
  job_id: string
  filename: string
  created_at: string
  output_path: string
  output_url: string
}

interface PersistedVoiceoverFormState {
  selectedProfileId?: string
  selectedModelId?: string
  script?: string
  outputFormat?: 'wav' | 'mp3'
  speed?: number
}

const dateFormatter = new Intl.DateTimeFormat('en-US', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

const MIN_SPEED = 0.8
const MAX_SPEED = 1.25
const SPEED_STEP = 0.05
const VOICEOVER_FORM_STATE_KEY = 'neonforge-voiceover-form-state-v1'

function clampSpeed(value: number): number {
  if (!Number.isFinite(value)) return 1
  return Math.min(MAX_SPEED, Math.max(MIN_SPEED, value))
}

function isOutputFormat(value: unknown): value is 'wav' | 'mp3' {
  return value === 'wav' || value === 'mp3'
}

function extractErrorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === 'string' && payload.trim()) {
    return payload
  }

  if (typeof payload === 'object' && payload !== null) {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) {
      return detail
    }
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0]
      if (typeof first === 'string' && first.trim()) {
        return first
      }
      if (typeof first === 'object' && first !== null && typeof (first as { msg?: unknown }).msg === 'string') {
        return (first as { msg: string }).msg
      }
    }
  }

  return fallback
}

async function readErrorMessage(response: Response): Promise<string> {
  const text = await response.text().catch(() => '')
  if (!text) {
    return `HTTP ${response.status}`
  }

  try {
    return extractErrorMessage(JSON.parse(text), text)
  } catch {
    return text
  }
}

async function apiRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    throw new Error(await readErrorMessage(response))
  }
  return response.json()
}

function uploadVoiceProfile(formData: FormData, onProgress: (value: number) => void): Promise<VoiceProfile> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/v1/voiceover/profiles')

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress(Math.round((event.loaded / event.total) * 100))
      }
    }

    xhr.onerror = () => reject(new Error('Profile upload failed'))
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as VoiceProfile)
        } catch {
          reject(new Error('Profile upload returned invalid JSON'))
        }
        return
      }
      try {
        reject(new Error(extractErrorMessage(JSON.parse(xhr.responseText), xhr.responseText || `HTTP ${xhr.status}`)))
      } catch {
        reject(new Error(xhr.responseText || `HTTP ${xhr.status}`))
      }
    }

    xhr.send(formData)
  })
}

export function VoiceoverStudio() {
  const restoredFormStateRef = useRef<PersistedVoiceoverFormState | null>(null)
  const [profiles, setProfiles] = useState<VoiceProfile[]>([])
  const [models, setModels] = useState<VoiceoverModelSummary[]>([])
  const [recentVoiceovers, setRecentVoiceovers] = useState<RecentVoiceover[]>([])
  const [profilesLoading, setProfilesLoading] = useState(true)
  const [modelsLoading, setModelsLoading] = useState(true)
  const [recentLoading, setRecentLoading] = useState(true)
  const [profileError, setProfileError] = useState<string | null>(null)
  const [generationError, setGenerationError] = useState<string | null>(null)
  const [recentError, setRecentError] = useState<string | null>(null)
  const [profilesOpen, setProfilesOpen] = useState(true)
  const [generateOpen, setGenerateOpen] = useState(true)
  const [recentOpen, setRecentOpen] = useState(true)
  const [showProfileForm, setShowProfileForm] = useState(false)
  const [profileName, setProfileName] = useState('')
  const [profileNotes, setProfileNotes] = useState('')
  const [profileFile, setProfileFile] = useState<File | null>(null)
  const [uploadingProfile, setUploadingProfile] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [selectedProfileId, setSelectedProfileId] = useState('')
  const [selectedModelId, setSelectedModelId] = useState('')
  const [script, setScript] = useState('')
  const [outputFormat, setOutputFormat] = useState<'wav' | 'mp3'>('wav')
  const [speed, setSpeed] = useState(1)
  const [speedInput, setSpeedInput] = useState('1.00')
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<VoiceoverJobStatus | null>(null)
  const [submittingJob, setSubmittingJob] = useState(false)
  const [deletingOutputId, setDeletingOutputId] = useState<string | null>(null)
  const [previewProfileId, setPreviewProfileId] = useState<string | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [formStateRestored, setFormStateRestored] = useState(false)

  const refreshProfiles = useCallback(async () => {
    setProfilesLoading(true)
    try {
      const data = await apiRequest<VoiceProfile[]>('/api/v1/voiceover/profiles')
      setProfiles(data)
      setProfileError(null)
      setSelectedProfileId((current) => {
        const preferredId = current || restoredFormStateRef.current?.selectedProfileId || ''
        if (data.length === 0) return ''
        if (preferredId && data.some((profile) => profile.id === preferredId)) return preferredId
        return data[0].id
      })
    } catch (error: unknown) {
      setProfileError(error instanceof Error ? error.message : 'Failed to load voice profiles')
    } finally {
      setProfilesLoading(false)
    }
  }, [])

  const refreshModels = useCallback(async () => {
    setModelsLoading(true)
    try {
      const data = await apiRequest<VoiceoverModelSummary[]>('/api/v1/voiceover/models')
      setModels(data)
      setGenerationError(null)
      setSelectedModelId((current) => {
        const preferredId = current || restoredFormStateRef.current?.selectedModelId || ''
        const preferredModel = preferredId
          ? data.find((model) => model.model_id === preferredId && model.available)
          : null
        if (preferredModel) return preferredModel.model_id
        return data.find((model) => model.available)?.model_id ?? ''
      })
    } catch (error: unknown) {
      setGenerationError(error instanceof Error ? error.message : 'Failed to load voiceover models')
    } finally {
      setModelsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (typeof window !== 'undefined') {
      try {
        const raw = window.localStorage.getItem(VOICEOVER_FORM_STATE_KEY)
        if (raw) {
          const payload = JSON.parse(raw) as PersistedVoiceoverFormState
          restoredFormStateRef.current = payload

          if (typeof payload.selectedProfileId === 'string') {
            setSelectedProfileId(payload.selectedProfileId)
          }
          if (typeof payload.selectedModelId === 'string') {
            setSelectedModelId(payload.selectedModelId)
          }
          if (typeof payload.script === 'string') {
            setScript(payload.script)
          }
          if (isOutputFormat(payload.outputFormat)) {
            setOutputFormat(payload.outputFormat)
          }
          if (typeof payload.speed === 'number') {
            const normalizedSpeed = Number(clampSpeed(payload.speed).toFixed(2))
            setSpeed(normalizedSpeed)
            setSpeedInput(normalizedSpeed.toFixed(2))
          }
        }
      } catch {
        restoredFormStateRef.current = null
      }
    }

    setFormStateRestored(true)
  }, [])

  const refreshRecentVoiceovers = useCallback(async () => {
    setRecentLoading(true)
    try {
      const data = await apiRequest<RecentVoiceover[]>('/api/v1/voiceover/outputs?limit=25')
      setRecentVoiceovers(data)
      setRecentError(null)
    } catch (error: unknown) {
      setRecentError(error instanceof Error ? error.message : 'Failed to load recent voiceovers')
    } finally {
      setRecentLoading(false)
    }
  }, [])

  useEffect(() => {
    void refreshProfiles()
    void refreshModels()
    void refreshRecentVoiceovers()
  }, [refreshModels, refreshProfiles, refreshRecentVoiceovers])

  useEffect(() => {
    if (!formStateRestored || typeof window === 'undefined') return

    const payload: PersistedVoiceoverFormState = {
      selectedProfileId,
      selectedModelId,
      script,
      outputFormat,
      speed,
    }

    window.localStorage.setItem(VOICEOVER_FORM_STATE_KEY, JSON.stringify(payload))
  }, [formStateRestored, outputFormat, script, selectedModelId, selectedProfileId, speed])

  useEffect(() => {
    if (!jobId) return

    let cancelled = false
    let timerId: number | null = null

    const poll = async () => {
      try {
        const nextStatus = await apiRequest<VoiceoverJobStatus>(`/api/v1/voiceover/jobs/${jobId}`)
        if (cancelled) return
        setJobStatus(nextStatus)
        if (nextStatus.status === 'done' || nextStatus.status === 'failed') {
          if (timerId !== null) window.clearInterval(timerId)
        }
      } catch (error: unknown) {
        if (cancelled) return
        setGenerationError(error instanceof Error ? error.message : 'Failed to poll voiceover job')
        if (timerId !== null) window.clearInterval(timerId)
      }
    }

    void poll()
    timerId = window.setInterval(() => {
      void poll()
    }, 2000)

    return () => {
      cancelled = true
      if (timerId !== null) window.clearInterval(timerId)
    }
  }, [jobId])

  useEffect(() => {
    if (jobStatus?.status !== 'done') return
    void refreshRecentVoiceovers()
  }, [jobStatus?.status, refreshRecentVoiceovers])

  const selectedModel = useMemo(
    () => models.find((model) => model.model_id === selectedModelId) ?? null,
    [models, selectedModelId],
  )
  const availableModels = useMemo(() => models.filter((model) => model.available), [models])

  const roughChunkEstimate = useMemo(() => (script.length === 0 ? 0 : Math.ceil(script.length / 150)), [script.length])
  const canGenerate =
    !!selectedProfileId &&
    !!script.trim() &&
    !!selectedModel?.available &&
    !submittingJob

  const progressValue = useMemo(() => {
    if (!jobStatus?.total_chunks) return 0
    return (100 * (jobStatus.completed_chunks ?? 0)) / jobStatus.total_chunks
  }, [jobStatus])

  const updateSpeed = (nextValue: number) => {
    const normalized = Number(clampSpeed(nextValue).toFixed(2))
    setSpeed(normalized)
    setSpeedInput(normalized.toFixed(2))
  }

  const handleCreateProfile = async () => {
    if (!profileName.trim() || !profileFile) return

    setUploadingProfile(true)
    setUploadProgress(0)
    setProfileError(null)

    try {
      const formData = new FormData()
      formData.append('name', profileName.trim())
      formData.append('notes', profileNotes.trim())
      formData.append('audio_file', profileFile)

      await uploadVoiceProfile(formData, setUploadProgress)
      setProfileName('')
      setProfileNotes('')
      setProfileFile(null)
      setShowProfileForm(false)
      await refreshProfiles()
    } catch (error: unknown) {
      setProfileError(error instanceof Error ? error.message : 'Failed to save voice profile')
    } finally {
      setUploadingProfile(false)
    }
  }

  const handleDeleteProfile = async (profile: VoiceProfile) => {
    const confirmed = window.confirm(`Delete voice profile "${profile.name}"?`)
    if (!confirmed) return

    setProfileError(null)
    try {
      await apiRequest<{ deleted: boolean }>(`/api/v1/voiceover/profiles/${profile.id}`, {
        method: 'DELETE',
      })
      if (previewProfileId === profile.id) {
        setPreviewProfileId(null)
        setPreviewUrl(null)
      }
      await refreshProfiles()
    } catch (error: unknown) {
      setProfileError(error instanceof Error ? error.message : 'Failed to delete voice profile')
    }
  }

  const handlePlayProfile = (profile: VoiceProfile) => {
    setPreviewProfileId(profile.id)
    setPreviewUrl(`/api/v1/voiceover/profiles/${profile.id}/sample?v=${Date.now()}`)
  }

  const handleGenerate = async () => {
    if (!canGenerate || !selectedModel?.available) return

    const requestedSpeed = Number(clampSpeed(parseFloat(speedInput)).toFixed(2))

    setSubmittingJob(true)
    setGenerationError(null)
    setJobStatus(null)
    setSpeed(requestedSpeed)
    setSpeedInput(requestedSpeed.toFixed(2))

    try {
      const response = await apiRequest<{ job_id: string; status: string }>('/api/v1/voiceover/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          voice_profile_id: selectedProfileId,
          script: script.trim(),
          model_id: selectedModel.model_id,
          output_format: outputFormat,
          speed: requestedSpeed,
        }),
      })

      setJobId(response.job_id)
      setJobStatus({ status: response.status, completed_chunks: 0, total_chunks: 0 })
    } catch (error: unknown) {
      setGenerationError(error instanceof Error ? error.message : 'Failed to start voiceover job')
    } finally {
      setSubmittingJob(false)
    }
  }

  const handleDeleteRecentVoiceover = async (item: RecentVoiceover) => {
    const confirmed = window.confirm(`Delete recent voiceover "${item.filename}"?`)
    if (!confirmed) return

    setDeletingOutputId(item.job_id)
    setRecentError(null)
    try {
      await apiRequest<{ deleted: boolean }>(`/api/v1/voiceover/output/${item.job_id}`, {
        method: 'DELETE',
      })
      setRecentVoiceovers((current) => current.filter((voiceover) => voiceover.job_id !== item.job_id))
      if (jobId === item.job_id) {
        setJobId(null)
        setJobStatus(null)
      }
    } catch (error: unknown) {
      setRecentError(error instanceof Error ? error.message : 'Failed to delete voiceover output')
    } finally {
      setDeletingOutputId(null)
    }
  }

  return (
    <div className="space-y-9">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <Mic2 className="h-6 w-6 text-primary" />
          Voiceover Studio
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Save reusable voice profiles, pick a local TTS runtime, and render long-form narration with stitched output.
        </p>
      </div>

      <Card className="overflow-hidden border-border/60 bg-card/80 shadow-sm">
        <button
          type="button"
          onClick={() => setProfilesOpen((current) => !current)}
          className="flex w-full items-start justify-between gap-4 border-b border-border/35 bg-muted/10 px-6 py-5 text-left transition-colors hover:bg-muted/15"
        >
          <div className="flex items-start gap-3">
            <div className="rounded-xl bg-primary/10 p-2.5 text-primary">
              <Mic2 className="h-5 w-5" />
            </div>
            <div>
              <p className="text-lg font-semibold tracking-tight">Voice Profiles</p>
              <p className="mt-1 text-[11px] font-medium uppercase tracking-[0.18em] text-primary/75">Reusable References</p>
              <p className="mt-2 text-sm text-muted-foreground">
                Upload a short reference clip once, then reuse it for future voiceover jobs.
              </p>
            </div>
          </div>
          {profilesOpen ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
        </button>

        {profilesOpen && (
          <CardContent className="space-y-5 pt-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm text-muted-foreground">
                {profilesLoading ? 'Loading profiles...' : `${profiles.length} saved voice profile${profiles.length === 1 ? '' : 's'}`}
              </div>
              <Button type="button" variant={showProfileForm ? 'secondary' : 'outline'} onClick={() => setShowProfileForm((current) => !current)}>
                {showProfileForm ? 'Close Form' : 'Add Voice Profile'}
              </Button>
            </div>

            {showProfileForm && (
              <div className="space-y-4 rounded-xl bg-background/35 p-4 ring-1 ring-border/40">
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="voice-profile-name">Profile Name</Label>
                    <Input
                      id="voice-profile-name"
                      value={profileName}
                      onChange={(event) => setProfileName(event.target.value)}
                      placeholder="Narrator A"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>Reference Clip</Label>
                    <FileDropzone
                      accept=".wav,.mp3,audio/wav,audio/mpeg"
                      label="Drop a WAV or MP3 sample"
                      hint={profileFile?.name || '3-30 seconds recommended'}
                      file={profileFile}
                      onFileChange={setProfileFile}
                      maxSizeMB={25}
                      icon="audio"
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="voice-profile-notes">Notes</Label>
                  <Textarea
                    id="voice-profile-notes"
                    value={profileNotes}
                    onChange={(event) => setProfileNotes(event.target.value)}
                    placeholder="Tone, pacing, pronunciation notes, or source details..."
                    rows={3}
                  />
                </div>

                {uploadingProfile && (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                      <span>Uploading profile...</span>
                      <span>{uploadProgress}%</span>
                    </div>
                    <Progress value={uploadProgress} />
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-3">
                  <Button
                    type="button"
                    className="gap-2"
                    onClick={handleCreateProfile}
                    disabled={!profileName.trim() || !profileFile || uploadingProfile}
                  >
                    {uploadingProfile ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
                    {uploadingProfile ? 'Uploading...' : 'Save Voice Profile'}
                  </Button>
                  <p className="text-xs text-muted-foreground">WAV and MP3 are accepted. Clips longer than 30 seconds are rejected when duration tools are available.</p>
                </div>
              </div>
            )}

            {profileError && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                {profileError}
              </div>
            )}

            <div className="space-y-3">
              {profiles.map((profile) => {
                const isPreviewing = previewProfileId === profile.id && previewUrl
                return (
                  <div key={profile.id} className="rounded-xl bg-background/35 p-4 ring-1 ring-border/40">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-semibold">{profile.name}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          Saved {dateFormatter.format(new Date(profile.created_at))}
                        </p>
                        {profile.notes && <p className="mt-2 text-sm text-muted-foreground">{profile.notes}</p>}
                      </div>
                      <div className="flex gap-2">
                        <Button type="button" variant="outline" size="sm" className="gap-2" onClick={() => handlePlayProfile(profile)}>
                          <Play className="h-3.5 w-3.5" />
                          Play
                        </Button>
                        <Button type="button" variant="outline" size="sm" className="gap-2" onClick={() => void handleDeleteProfile(profile)}>
                          <Trash2 className="h-3.5 w-3.5" />
                          Delete
                        </Button>
                      </div>
                    </div>
                    {isPreviewing && (
                      <audio key={previewUrl} controls autoPlay className="mt-3 w-full" src={previewUrl}>
                        Your browser does not support audio playback.
                      </audio>
                    )}
                  </div>
                )
              })}

              {!profilesLoading && profiles.length === 0 && (
                <div className="rounded-xl border border-dashed border-border/50 p-6 text-center text-sm text-muted-foreground">
                  Save your first voice profile to unlock generation.
                </div>
              )}
            </div>
          </CardContent>
        )}
      </Card>

      <Card className="overflow-hidden border-border/60 bg-card/80 shadow-sm">
        <button
          type="button"
          onClick={() => setGenerateOpen((current) => !current)}
          className="flex w-full items-start justify-between gap-4 border-b border-border/35 bg-muted/10 px-6 py-5 text-left transition-colors hover:bg-muted/15"
        >
          <div className="flex items-start gap-3">
            <div className="rounded-xl bg-primary/10 p-2.5 text-primary">
              <Wand2 className="h-5 w-5" />
            </div>
            <div>
              <p className="text-lg font-semibold tracking-tight">Generate Voiceover</p>
              <p className="mt-1 text-[11px] font-medium uppercase tracking-[0.18em] text-primary/75">Render And Stitch</p>
              <p className="mt-2 text-sm text-muted-foreground">
                Pick a saved voice, choose a model, and render a stitched narration file.
              </p>
            </div>
          </div>
          {generateOpen ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
        </button>

        {generateOpen && (
          <CardContent className="space-y-6 pt-6">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="voice-profile-select">Voice Profile</Label>
                <select
                  id="voice-profile-select"
                  value={selectedProfileId}
                  onChange={(event) => setSelectedProfileId(event.target.value)}
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                >
                  <option value="">Select a saved profile</option>
                  {profiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="voice-model-select">TTS Model</Label>
                <select
                  id="voice-model-select"
                  value={selectedModelId}
                  onChange={(event) => setSelectedModelId(event.target.value)}
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                >
                  <option value="">Select a model</option>
                  {models.map((model) => (
                    <option key={model.model_id} value={model.model_id} disabled={!model.available}>
                      {model.display_name}{model.available ? '' : ' (unavailable)'}
                    </option>
                  ))}
                </select>
                <p className="text-xs text-muted-foreground">
                  {modelsLoading
                    ? 'Loading model list...'
                    : availableModels.length > 0
                      ? 'Unavailable runtimes stay listed but cannot be selected.'
                      : 'No runnable voice models are currently available.'}
                </p>
              </div>
            </div>

            <div className="space-y-2 rounded-xl bg-background/30 p-4 ring-1 ring-border/35">
              <Label htmlFor="voiceover-script">Script</Label>
              <Textarea
                id="voiceover-script"
                value={script}
                onChange={(event) => setScript(event.target.value)}
                placeholder="Paste the voiceover script here. Longer scripts are chunked automatically and stitched into one final file."
                rows={10}
                className="min-h-[220px]"
              />
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>{script.length} characters</span>
                <span>Estimated chunks: {roughChunkEstimate}</span>
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-[minmax(0,1fr),220px,1fr]">
              <div className="space-y-3 rounded-xl bg-background/30 p-4 ring-1 ring-border/35">
                <div className="flex items-center justify-between gap-3">
                  <Label htmlFor="voiceover-speed">Speed</Label>
                  <Input
                    id="voiceover-speed"
                    type="number"
                    min={MIN_SPEED}
                    max={MAX_SPEED}
                    step={SPEED_STEP}
                    value={speedInput}
                    onChange={(event) => setSpeedInput(event.target.value)}
                    onBlur={() => updateSpeed(parseFloat(speedInput))}
                    className="w-24 font-mono text-sm"
                  />
                </div>
                <Slider
                  value={speed}
                  onChange={(value) => updateSpeed(value)}
                  min={MIN_SPEED}
                  max={MAX_SPEED}
                  step={SPEED_STEP}
                />
                <div className="flex justify-between text-[10px] text-muted-foreground/70">
                  <span>0.80x</span>
                  <span>{speed.toFixed(2)}x</span>
                  <span>1.25x</span>
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="voiceover-output-format">Output Format</Label>
                <select
                  id="voiceover-output-format"
                  value={outputFormat}
                  onChange={(event) => setOutputFormat(event.target.value === 'mp3' ? 'mp3' : 'wav')}
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                >
                  <option value="wav">wav</option>
                  <option value="mp3">mp3</option>
                </select>
              </div>
              <div className="flex items-end">
                <Button type="button" size="lg" className="gap-2 px-6" disabled={!canGenerate} onClick={handleGenerate}>
                  {submittingJob ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
                  {submittingJob ? 'Submitting...' : 'Generate'}
                </Button>
              </div>
            </div>

            {generationError && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                {generationError}
              </div>
            )}

            {jobStatus && (
              <div className="space-y-4 rounded-xl bg-background/35 p-4 ring-1 ring-border/40">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold">Current Job</p>
                    <p className="mt-1 text-xs text-muted-foreground">Status: {jobStatus.status}</p>
                  </div>
                  {jobId && <p className="text-xs font-mono text-muted-foreground">{jobId}</p>}
                </div>

                <div className="space-y-2">
                  <Progress value={progressValue} indeterminate={jobStatus.status === 'pending' || jobStatus.status === 'stitching'} />
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                    <span>
                      {jobStatus.completed_chunks ?? 0} / {jobStatus.total_chunks ?? 0} chunks complete
                    </span>
                    {jobStatus.status === 'stitching' && <span>Finalizing audio...</span>}
                  </div>
                </div>

                {jobStatus.status === 'failed' && jobStatus.error && (
                  <p className="text-sm text-red-300">{jobStatus.error}</p>
                )}

                {jobStatus.status === 'done' && jobStatus.output_url && (
                  <div className="space-y-3">
                    <audio controls className="w-full" src={jobStatus.output_url}>
                      Your browser does not support audio playback.
                    </audio>
                    <a href={jobStatus.output_url} download className="inline-flex">
                      <Button type="button" variant="outline">
                        Download Audio
                      </Button>
                    </a>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        )}
      </Card>

      <Card className="overflow-hidden border-border/60 bg-card/80 shadow-sm">
        <button
          type="button"
          onClick={() => setRecentOpen((current) => !current)}
          className="flex w-full items-start justify-between gap-4 border-b border-border/35 bg-muted/10 px-6 py-5 text-left transition-colors hover:bg-muted/15"
        >
          <div className="flex items-start gap-3">
            <div className="rounded-xl bg-primary/10 p-2.5 text-primary">
              <History className="h-5 w-5" />
            </div>
            <div>
              <p className="text-lg font-semibold tracking-tight">Recent Voiceovers</p>
              <p className="mt-1 text-[11px] font-medium uppercase tracking-[0.18em] text-primary/75">Completed Outputs</p>
              <p className="mt-2 text-sm text-muted-foreground">
                Revisit finished voiceovers from this feature only, then play, download, or clear them.
              </p>
            </div>
          </div>
          {recentOpen ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
        </button>

        {recentOpen && (
          <CardContent className="space-y-5 pt-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm text-muted-foreground">
                {recentLoading ? 'Loading recent voiceovers...' : `${recentVoiceovers.length} saved voiceover${recentVoiceovers.length === 1 ? '' : 's'}`}
              </div>
              <Button type="button" variant="outline" size="sm" className="gap-2" onClick={() => void refreshRecentVoiceovers()}>
                <RefreshCw className="h-3.5 w-3.5" />
                Refresh
              </Button>
            </div>

            {recentError && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                {recentError}
              </div>
            )}

            <div className="space-y-3">
              {recentVoiceovers.map((item) => (
                <div key={item.job_id} className="rounded-xl bg-background/35 p-4 ring-1 ring-border/40">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold">{item.filename}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Saved {dateFormatter.format(new Date(item.created_at))}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <a href={item.output_url} download className="inline-flex">
                        <Button type="button" variant="outline" size="sm" className="gap-2">
                          <Download className="h-3.5 w-3.5" />
                          Download
                        </Button>
                      </a>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="gap-2"
                        onClick={() => void handleDeleteRecentVoiceover(item)}
                        disabled={deletingOutputId === item.job_id}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        {deletingOutputId === item.job_id ? 'Deleting...' : 'Delete'}
                      </Button>
                    </div>
                  </div>
                  <audio controls className="mt-3 w-full" src={`${item.output_url}?v=${encodeURIComponent(item.created_at)}`}>
                    Your browser does not support audio playback.
                  </audio>
                </div>
              ))}

              {!recentLoading && recentVoiceovers.length === 0 && (
                <div className="rounded-xl border border-dashed border-border/50 p-6 text-center text-sm text-muted-foreground">
                  Completed voiceovers will appear here after a render finishes.
                </div>
              )}
            </div>
          </CardContent>
        )}
      </Card>
    </div>
  )
}
