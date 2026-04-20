'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  Download,
  History,
  Loader2,
  Mic2,
  Square,
  Play,
  RefreshCw,
  Trash2,
  UploadCloud,
  Wand2,
} from 'lucide-react'
import { FileDropzone } from '@/components/file-dropzone'
import { useMediaRecorder } from '@/hooks/use-media-recorder'
import { cn, formatDuration } from '@/lib/utils'
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
  reference_transcript?: string | null
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
  filename?: string
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
  voxMode?: VoxMode
  voxContinuationReferenceSource?: VoxContinuationReferenceSource
  voxPromptText?: string
  voxStyleText?: string
}

interface PersistedActiveVoiceoverJob {
  jobId: string
  modelId: string
  modelLabel: string
  profileId: string
  profileName: string
  createdAt: string
}

interface TrackedVoiceoverJob extends PersistedActiveVoiceoverJob {
  status: VoiceoverJobStatus | null
}

const dateFormatter = new Intl.DateTimeFormat('en-US', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

const MIN_SPEED = 0.8
const MAX_SPEED = 1.25
const SPEED_STEP = 0.05
const VOICEOVER_FORM_STATE_KEY = 'neonforge-voiceover-form-state-v1'
const VOICEOVER_ACTIVE_JOBS_KEY = 'neonforge-voiceover-active-jobs-v1'
const VOICE_PROFILE_INPUT_DEVICE_KEY = 'neonforge-voice-profile-input-device-v1'
const VOICE_PROFILE_CAPTURE_MODE_KEY = 'neonforge-voice-profile-capture-mode-v1'
const VOX_MODEL_ID = 'voxcpm2'
const VOX_MODE_DESIGN = 'design'
const VOX_MODE_CLONE = 'clone'
const VOX_MODE_CONTINUATION = 'continuation'
const VOX_SINGLE_PASS_MAX_CHARS = 1200
const VOX_CONTINUATION_SINGLE_PASS_MAX_CHARS = 1800
const VOX_CHUNK_ESTIMATE_SIZE = 650
const BROWSER_RECORDED_PROFILE_SOURCE = 'browser-recording'

type VoxMode = 'design' | 'clone' | 'continuation'
type VoxContinuationReferenceSource = 'profile' | 'record'
type VoiceProfileReferenceSource = 'upload' | 'record'
type VoiceProfileCaptureMode = 'raw' | 'enhanced'
type VoiceoverWorkspaceTab = 'generate' | 'profiles' | 'outputs'

interface TemporaryReferenceUploadResponse {
  temp_reference_id: string
  transcript: string
}

const VOX_MODE_OPTIONS: Array<{
  value: VoxMode
  label: string
  shortLabel: string
  helper: string
}> = [
  {
    value: VOX_MODE_DESIGN,
    label: 'Voice Design',
    shortLabel: 'Design',
    helper: 'Text-only voice.',
  },
  {
    value: VOX_MODE_CLONE,
    label: 'Clone My Voice',
    shortLabel: 'Clone',
    helper: 'Uses a saved reference.',
  },
  {
    value: VOX_MODE_CONTINUATION,
    label: 'Continue From Reference',
    shortLabel: 'Continue',
    helper: 'Needs the exact transcript.',
  },
]

function clampSpeed(value: number): number {
  if (!Number.isFinite(value)) return 1
  return Math.min(MAX_SPEED, Math.max(MIN_SPEED, value))
}

function isOutputFormat(value: unknown): value is 'wav' | 'mp3' {
  return value === 'wav' || value === 'mp3'
}

function isVoiceProfileCaptureMode(value: unknown): value is VoiceProfileCaptureMode {
  return value === 'raw' || value === 'enhanced'
}

function getAudioInputDeviceLabel(device: MediaDeviceInfo, index: number): string {
  return device.label.trim() || `Microphone ${index + 1}`
}

function isTerminalJobStatus(status: string | null | undefined): boolean {
  return status === 'done' || status === 'failed'
}

function getJobProgressValue(status: VoiceoverJobStatus | null): number {
  if (!status?.total_chunks) return 0
  return (100 * (status.completed_chunks ?? 0)) / status.total_chunks
}

function sortTrackedJobs<T extends { createdAt: string }>(jobs: T[]): T[] {
  return [...jobs].sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt))
}

function upsertTrackedJob(current: TrackedVoiceoverJob[], nextJob: TrackedVoiceoverJob): TrackedVoiceoverJob[] {
  return sortTrackedJobs([nextJob, ...current.filter((job) => job.jobId !== nextJob.jobId)])
}

function readPersistedActiveJobs(): PersistedActiveVoiceoverJob[] {
  if (typeof window === 'undefined') return []

  try {
    const raw = window.localStorage.getItem(VOICEOVER_ACTIVE_JOBS_KEY)
    if (!raw) return []

    const payload = JSON.parse(raw)
    if (!Array.isArray(payload)) return []

    return payload.flatMap((item) => {
      if (typeof item !== 'object' || item === null) return []

      const candidate = item as Partial<PersistedActiveVoiceoverJob>
      if (
        typeof candidate.jobId !== 'string' ||
        typeof candidate.modelId !== 'string' ||
        typeof candidate.modelLabel !== 'string' ||
        typeof candidate.profileId !== 'string' ||
        typeof candidate.profileName !== 'string' ||
        typeof candidate.createdAt !== 'string'
      ) {
        return []
      }

      return [
        {
          jobId: candidate.jobId,
          modelId: candidate.modelId,
          modelLabel: candidate.modelLabel,
          profileId: candidate.profileId,
          profileName: candidate.profileName,
          createdAt: candidate.createdAt,
        },
      ]
    })
  } catch {
    return []
  }
}

function getTrackedJobFilename(job: TrackedVoiceoverJob): string | null {
  if (typeof job.status?.filename === 'string' && job.status.filename.trim()) {
    return job.status.filename
  }

  if (typeof job.status?.output_path === 'string' && job.status.output_path.trim()) {
    const parts = job.status.output_path.split('/')
    return parts[parts.length - 1] || null
  }

  return null
}

function getProfileTranscriptSeed(profile: VoiceProfile | null): string {
  if (!profile) return ''
  return profile.reference_transcript?.trim() || profile.notes?.trim() || ''
}

function truncateText(value: string | null | undefined, maxLength: number): string {
  const trimmedValue = value?.trim()
  if (!trimmedValue) return ''
  if (trimmedValue.length <= maxLength) return trimmedValue
  return `${trimmedValue.slice(0, maxLength - 1).trimEnd()}...`
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

interface VoiceoverJobsPanelProps {
  activeJobsCount: number
  trackedJobs: TrackedVoiceoverJob[]
  latestRecentVoiceover: RecentVoiceover | null
  onOpenOutputs: () => void
  recentVoiceoversCount: number
  className?: string
}

function VoiceoverJobsPanel({
  activeJobsCount,
  trackedJobs,
  latestRecentVoiceover,
  onOpenOutputs,
  recentVoiceoversCount,
  className,
}: VoiceoverJobsPanelProps) {
  const hasTrackedJobs = trackedJobs.length > 0

  return (
    <Card className={cn('overflow-hidden border-border/60 bg-card/80 shadow-sm', className)}>
      <div className={cn('border-b border-border/35 bg-muted/10', hasTrackedJobs ? 'px-6 py-5' : 'px-5 py-4')}>
        <div className="flex items-start gap-3">
          <div className="rounded-xl bg-primary/10 p-2.5 text-primary">
            <History className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-lg font-semibold tracking-tight">Job Activity</p>
                <p className="mt-1 text-[11px] font-medium uppercase tracking-[0.18em] text-primary/75">
                  {hasTrackedJobs ? 'Live Render Progress' : 'Ready Queue'}
                </p>
                <p className="mt-2 text-sm text-muted-foreground">
                  {hasTrackedJobs
                    ? 'Progress stays visible beside Generate.'
                    : recentVoiceoversCount > 0
                      ? 'Idle for now. Latest outputs stay one click away.'
                      : 'Start a render and track it here.'}
                </p>
              </div>
              <p className="text-xs text-muted-foreground">
                {activeJobsCount} active, {trackedJobs.length} tracked
                {recentVoiceoversCount > 0 ? `, ${recentVoiceoversCount} recent` : ''}
              </p>
            </div>
          </div>
        </div>
      </div>

      <CardContent className={cn('space-y-4 xl:max-h-[calc(100vh-12rem)] xl:overflow-y-auto', hasTrackedJobs ? 'pt-6' : 'pt-4')}>
        {hasTrackedJobs ? (
          <div className="space-y-3">
            {trackedJobs.map((job) => {
              const currentStatus = job.status
              const progressValue = getJobProgressValue(currentStatus)
              const jobFilename = getTrackedJobFilename(job)
              const isIndeterminate =
                !currentStatus || currentStatus.status === 'pending' || currentStatus.status === 'stitching'

              return (
                <div key={job.jobId} className="rounded-xl bg-background/45 p-4 ring-1 ring-border/35">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold">{job.modelLabel}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {job.profileName} • Started {dateFormatter.format(new Date(job.createdAt))}
                      </p>
                      <p className="mt-1 text-xs font-mono text-muted-foreground">{job.jobId}</p>
                      {jobFilename && <p className="mt-2 text-xs text-muted-foreground">Output: {jobFilename}</p>}
                    </div>
                    <div className="rounded-full border border-border/50 px-2.5 py-1 text-xs font-medium text-muted-foreground">
                      {currentStatus?.status ?? 'restoring'}
                    </div>
                  </div>

                  <div className="mt-4 space-y-2">
                    <Progress value={progressValue} indeterminate={isIndeterminate} />
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                      <span>
                        {currentStatus?.completed_chunks ?? 0} / {currentStatus?.total_chunks ?? 0} chunks complete
                      </span>
                      {currentStatus?.status === 'stitching' && <span>Finalizing audio...</span>}
                      {!currentStatus && <span>Restoring job state...</span>}
                    </div>
                  </div>

                  {currentStatus?.status === 'failed' && currentStatus.error && (
                    <p className="mt-3 text-sm text-red-300">{currentStatus.error}</p>
                  )}

                  {currentStatus?.status === 'done' && currentStatus.output_url && (
                    <div className="mt-4 space-y-3">
                      <audio
                        controls
                        className="w-full"
                        src={`${currentStatus.output_url}?v=${encodeURIComponent(currentStatus.created_at ?? job.createdAt)}`}
                      >
                        Your browser does not support audio playback.
                      </audio>
                      <a href={currentStatus.output_url} download className="inline-flex">
                        <Button type="button" variant="outline" size="sm" className="gap-2">
                          <Download className="h-3.5 w-3.5" />
                          Download Audio
                        </Button>
                      </a>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          <div className="space-y-4 rounded-xl border border-dashed border-border/50 p-6 text-center">
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">No live jobs right now</p>
              <p className="text-sm text-muted-foreground">
                Start a voiceover and this rail will keep tracking it, even if you refresh the page.
              </p>
            </div>

            {latestRecentVoiceover ? (
              <div className="rounded-xl bg-background/45 p-4 text-left ring-1 ring-border/35">
                <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-primary/75">Latest Output</p>
                <p className="mt-2 truncate text-sm font-semibold">{latestRecentVoiceover.filename}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Saved {dateFormatter.format(new Date(latestRecentVoiceover.created_at))}
                </p>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                Choose a model, set up the reference flow, and submit a render to populate this panel.
              </p>
            )}

            {recentVoiceoversCount > 0 && (
              <Button type="button" variant="outline" size="sm" onClick={onOpenOutputs}>
                Open Outputs
              </Button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function VoiceoverStudio() {
  const restoredFormStateRef = useRef<PersistedVoiceoverFormState | null>(null)
  const activeJobsRef = useRef<TrackedVoiceoverJob[]>([])
  const storedProfileInputDeviceIdRef = useRef('')
  const voxRecordedReferenceTokenRef = useRef(0)
  const voxProcessedRecordingRef = useRef<Blob | null>(null)
  const lastAutoSeededVoxPromptRef = useRef('')
  const [profiles, setProfiles] = useState<VoiceProfile[]>([])
  const [models, setModels] = useState<VoiceoverModelSummary[]>([])
  const [recentVoiceovers, setRecentVoiceovers] = useState<RecentVoiceover[]>([])
  const [profilesLoading, setProfilesLoading] = useState(true)
  const [modelsLoading, setModelsLoading] = useState(true)
  const [recentLoading, setRecentLoading] = useState(true)
  const [profileError, setProfileError] = useState<string | null>(null)
  const [generationError, setGenerationError] = useState<string | null>(null)
  const [recentError, setRecentError] = useState<string | null>(null)
  const [activeWorkspaceTab, setActiveWorkspaceTab] = useState<VoiceoverWorkspaceTab>('generate')
  const [showProfileForm, setShowProfileForm] = useState(false)
  const [profileName, setProfileName] = useState('')
  const [profileNotes, setProfileNotes] = useState('')
  const [profileReferenceSource, setProfileReferenceSource] = useState<VoiceProfileReferenceSource>('upload')
  const [profileCaptureMode, setProfileCaptureMode] = useState<VoiceProfileCaptureMode>('raw')
  const [profileInputDeviceId, setProfileInputDeviceId] = useState('')
  const [profileInputDevices, setProfileInputDevices] = useState<MediaDeviceInfo[]>([])
  const [profileFile, setProfileFile] = useState<File | null>(null)
  const [uploadingProfile, setUploadingProfile] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [selectedProfileId, setSelectedProfileId] = useState('')
  const [selectedModelId, setSelectedModelId] = useState('')
  const [script, setScript] = useState('')
  const [outputFormat, setOutputFormat] = useState<'wav' | 'mp3'>('wav')
  const [speed, setSpeed] = useState(1)
  const [speedInput, setSpeedInput] = useState('1.00')
  const [voxMode, setVoxMode] = useState<VoxMode>(VOX_MODE_CLONE)
  const [voxContinuationReferenceSource, setVoxContinuationReferenceSource] = useState<VoxContinuationReferenceSource>('profile')
  const [voxRecordedReferenceId, setVoxRecordedReferenceId] = useState('')
  const [voxRecordedReferencePending, setVoxRecordedReferencePending] = useState(false)
  const [voxRecordedReferenceError, setVoxRecordedReferenceError] = useState<string | null>(null)
  const [voxPromptText, setVoxPromptText] = useState('')
  const [voxStyleText, setVoxStyleText] = useState('')
  const [trackedJobs, setTrackedJobs] = useState<TrackedVoiceoverJob[]>([])
  const [submittingJob, setSubmittingJob] = useState(false)
  const [deletingOutputId, setDeletingOutputId] = useState<string | null>(null)
  const [selectedOutputIds, setSelectedOutputIds] = useState<string[]>([])
  const [bulkDeletingOutputs, setBulkDeletingOutputs] = useState(false)
  const [bulkDownloadingOutputs, setBulkDownloadingOutputs] = useState(false)
  const [previewProfileId, setPreviewProfileId] = useState<string | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [formStateRestored, setFormStateRestored] = useState(false)
  const [profileRecordingPrefsRestored, setProfileRecordingPrefsRestored] = useState(false)
  const profileRecorderAudioConstraints = useMemo<MediaTrackConstraints>(() => {
    const enhancedCapture = profileCaptureMode === 'enhanced'

    return {
      autoGainControl: enhancedCapture,
      echoCancellation: enhancedCapture,
      noiseSuppression: enhancedCapture,
      ...(profileInputDeviceId ? { deviceId: { exact: profileInputDeviceId } } : {}),
    }
  }, [profileCaptureMode, profileInputDeviceId])
  const profileRecorder = useMediaRecorder({
    audioConstraints: profileRecorderAudioConstraints,
  })
  const voxRecorder = useMediaRecorder()

  const refreshProfiles = useCallback(async (preferredSelectedId?: string) => {
    setProfilesLoading(true)
    try {
      const data = await apiRequest<VoiceProfile[]>('/api/v1/voiceover/profiles')
      setProfiles(data)
      setProfileError(null)
      setSelectedProfileId((current) => {
        const preferredId = preferredSelectedId || current || restoredFormStateRef.current?.selectedProfileId || ''
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
      const data = await apiRequest<VoiceoverModelSummary[]>('/api/v1/voiceover/models', {
        cache: 'no-store',
      })
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

  const refreshProfileInputDevices = useCallback(async () => {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.enumerateDevices) {
      return
    }

    try {
      const devices = await navigator.mediaDevices.enumerateDevices()
      const inputs = devices.filter((device) => device.kind === 'audioinput')
      setProfileInputDevices(inputs)
      setProfileInputDeviceId((current) => {
        const preferredId = current || storedProfileInputDeviceIdRef.current
        if (!preferredId) return ''
        return inputs.some((device) => device.deviceId === preferredId) ? preferredId : ''
      })
    } catch {
      setProfileInputDevices([])
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
          if (
            payload.voxMode === VOX_MODE_DESIGN ||
            payload.voxMode === VOX_MODE_CLONE ||
            payload.voxMode === VOX_MODE_CONTINUATION
          ) {
            setVoxMode(payload.voxMode)
          }
          if (
            payload.voxContinuationReferenceSource === 'profile' ||
            payload.voxContinuationReferenceSource === 'record'
          ) {
            setVoxContinuationReferenceSource(payload.voxContinuationReferenceSource)
          }
          if (typeof payload.voxPromptText === 'string') {
            setVoxPromptText(payload.voxPromptText)
          }
          if (typeof payload.voxStyleText === 'string') {
            setVoxStyleText(payload.voxStyleText)
          }
        }
      } catch {
        restoredFormStateRef.current = null
      }

      setTrackedJobs(readPersistedActiveJobs().map((job) => ({ ...job, status: null })))
    }

    setFormStateRestored(true)
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return

    const savedProfileInputDeviceId = window.localStorage.getItem(VOICE_PROFILE_INPUT_DEVICE_KEY) ?? ''
    const savedProfileCaptureMode = window.localStorage.getItem(VOICE_PROFILE_CAPTURE_MODE_KEY)

    storedProfileInputDeviceIdRef.current = savedProfileInputDeviceId
    if (savedProfileInputDeviceId) {
      setProfileInputDeviceId(savedProfileInputDeviceId)
    }
    if (isVoiceProfileCaptureMode(savedProfileCaptureMode)) {
      setProfileCaptureMode(savedProfileCaptureMode)
    }
    setProfileRecordingPrefsRestored(true)
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
    void refreshProfileInputDevices()

    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.addEventListener) {
      return
    }

    const handleDeviceChange = () => {
      void refreshProfileInputDevices()
    }

    navigator.mediaDevices.addEventListener('devicechange', handleDeviceChange)
    return () => {
      navigator.mediaDevices.removeEventListener('devicechange', handleDeviceChange)
    }
  }, [refreshProfileInputDevices])

  useEffect(() => {
    const handleWindowFocus = () => {
      void refreshModels()
    }

    window.addEventListener('focus', handleWindowFocus)
    return () => {
      window.removeEventListener('focus', handleWindowFocus)
    }
  }, [refreshModels])

  useEffect(() => {
    if (!formStateRestored || typeof window === 'undefined') return

    const payload: PersistedVoiceoverFormState = {
      selectedProfileId,
      selectedModelId,
      script,
      outputFormat,
      speed,
      voxMode,
      voxContinuationReferenceSource,
      voxPromptText,
      voxStyleText,
    }

    window.localStorage.setItem(VOICEOVER_FORM_STATE_KEY, JSON.stringify(payload))
  }, [formStateRestored, outputFormat, script, selectedModelId, selectedProfileId, speed, voxMode, voxContinuationReferenceSource, voxPromptText, voxStyleText])

  useEffect(() => {
    if (!profileRecordingPrefsRestored || typeof window === 'undefined') return

    storedProfileInputDeviceIdRef.current = profileInputDeviceId
    if (profileInputDeviceId) {
      window.localStorage.setItem(VOICE_PROFILE_INPUT_DEVICE_KEY, profileInputDeviceId)
      return
    }

    window.localStorage.removeItem(VOICE_PROFILE_INPUT_DEVICE_KEY)
  }, [profileInputDeviceId, profileRecordingPrefsRestored])

  useEffect(() => {
    if (!profileRecordingPrefsRestored || typeof window === 'undefined') return
    window.localStorage.setItem(VOICE_PROFILE_CAPTURE_MODE_KEY, profileCaptureMode)
  }, [profileCaptureMode, profileRecordingPrefsRestored])

  useEffect(() => {
    if (typeof window === 'undefined') return

    const payload = trackedJobs
      .filter((job) => !isTerminalJobStatus(job.status?.status))
      .map(({ status: _status, ...job }) => job)

    if (payload.length === 0) {
      window.localStorage.removeItem(VOICEOVER_ACTIVE_JOBS_KEY)
      return
    }

    window.localStorage.setItem(VOICEOVER_ACTIVE_JOBS_KEY, JSON.stringify(payload))
  }, [trackedJobs])

  const selectedModel = useMemo(
    () => models.find((model) => model.model_id === selectedModelId) ?? null,
    [models, selectedModelId],
  )
  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedProfileId) ?? null,
    [profiles, selectedProfileId],
  )
  const isVoxModel = selectedModel?.model_id === VOX_MODEL_ID
  const isVoxDesignMode = isVoxModel && voxMode === VOX_MODE_DESIGN
  const isVoxContinuationMode = isVoxModel && voxMode === VOX_MODE_CONTINUATION
  const voxContinuationUsesRecordedReference = isVoxContinuationMode && voxContinuationReferenceSource === 'record'
  const requiresSavedVoiceProfile = !isVoxDesignMode && !voxContinuationUsesRecordedReference
  const availableModels = useMemo(() => models.filter((model) => model.available), [models])
  const activeJobs = useMemo(
    () => trackedJobs.filter((job) => !isTerminalJobStatus(job.status?.status)),
    [trackedJobs],
  )
  const selectedRecentVoiceovers = useMemo(
    () => recentVoiceovers.filter((voiceover) => selectedOutputIds.includes(voiceover.job_id)),
    [recentVoiceovers, selectedOutputIds],
  )
  const allRecentVoiceoversSelected =
    recentVoiceovers.length > 0 && selectedOutputIds.length === recentVoiceovers.length
  const activeJobsKey = useMemo(
    () => activeJobs.map((job) => job.jobId).sort().join(','),
    [activeJobs],
  )

  useEffect(() => {
    setSelectedOutputIds((current) =>
      current.filter((jobId) => recentVoiceovers.some((voiceover) => voiceover.job_id === jobId)),
    )
  }, [recentVoiceovers])

  useEffect(() => {
    activeJobsRef.current = activeJobs
  }, [activeJobs])

  useEffect(() => {
    if (!activeJobsKey) return

    let cancelled = false
    let timerId: number | null = null

    const poll = async () => {
      const jobsToPoll = activeJobsRef.current
      if (jobsToPoll.length === 0) return

      const results = await Promise.allSettled(
        jobsToPoll.map(async (job) => ({
          jobId: job.jobId,
          status: await apiRequest<VoiceoverJobStatus>(`/api/v1/voiceover/jobs/${job.jobId}`),
        })),
      )

      if (cancelled) return

      let shouldRefreshRecent = false
      let pollingError: string | null = null
      const resultsByJobId = new Map(results.map((result, index) => [jobsToPoll[index].jobId, result]))

      setTrackedJobs((current) =>
        current.flatMap((job) => {
          const result = resultsByJobId.get(job.jobId)
          if (!result) return [job]

          if (result.status === 'fulfilled') {
            if (result.value.status.status === 'done' && job.status?.status !== 'done') {
              shouldRefreshRecent = true
            }
            return [{ ...job, status: result.value.status }]
          }

          const message = result.reason instanceof Error ? result.reason.message : 'Failed to poll voiceover job'
          if (message === 'Voiceover job not found') {
            return []
          }

          if (!pollingError) {
            pollingError = message
          }
          return [job]
        }),
      )

      if (pollingError) {
        setGenerationError(pollingError)
      }
      if (shouldRefreshRecent) {
        void refreshRecentVoiceovers()
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
  }, [activeJobsKey, refreshRecentVoiceovers])

  const deleteTempReference = useCallback(async (referenceId: string) => {
    try {
      await apiRequest<{ deleted: boolean }>(`/api/v1/voiceover/temp-reference/${referenceId}`, {
        method: 'DELETE',
      })
    } catch {
      return
    }
  }, [])

  const clearRecordedReference = useCallback((options?: { clearTranscript?: boolean }) => {
    voxRecordedReferenceTokenRef.current += 1
    voxProcessedRecordingRef.current = null
    if (voxRecordedReferenceId) {
      void deleteTempReference(voxRecordedReferenceId)
    }
    setVoxRecordedReferenceId('')
    setVoxRecordedReferencePending(false)
    setVoxRecordedReferenceError(null)
    if (options?.clearTranscript !== false) {
      setVoxPromptText('')
    }
    voxRecorder.clearRecording()
  }, [deleteTempReference, voxRecordedReferenceId, voxRecorder.clearRecording])

  const uploadRecordedReference = useCallback(async (recordedBlob: Blob) => {
    const requestToken = ++voxRecordedReferenceTokenRef.current
    setVoxRecordedReferencePending(true)
    setVoxRecordedReferenceError(null)

    try {
      const formData = new FormData()
      formData.append(
        'audio_file',
        recordedBlob,
        `vox-continuation-reference-${Date.now()}.${voxRecorder.fileExtension}`,
      )

      const response = await apiRequest<TemporaryReferenceUploadResponse>('/api/v1/voiceover/temp-reference', {
        method: 'POST',
        body: formData,
      })

      if (requestToken !== voxRecordedReferenceTokenRef.current) {
        void deleteTempReference(response.temp_reference_id)
        return
      }

      setVoxRecordedReferenceId(response.temp_reference_id)
      setVoxPromptText(response.transcript)
    } catch (error: unknown) {
      if (requestToken !== voxRecordedReferenceTokenRef.current) {
        return
      }
      setVoxRecordedReferenceId('')
      setVoxRecordedReferenceError(error instanceof Error ? error.message : 'Failed to prepare the recorded reference clip')
    } finally {
      if (requestToken === voxRecordedReferenceTokenRef.current) {
        setVoxRecordedReferencePending(false)
      }
    }
  }, [deleteTempReference, voxRecorder.fileExtension])

  useEffect(() => {
    if (!voxContinuationUsesRecordedReference || !voxRecorder.audioBlob) return
    if (voxRecorder.audioBlob === voxProcessedRecordingRef.current) return

    voxProcessedRecordingRef.current = voxRecorder.audioBlob
    void uploadRecordedReference(voxRecorder.audioBlob)
  }, [uploadRecordedReference, voxContinuationUsesRecordedReference, voxRecorder.audioBlob])

  useEffect(() => {
    if (!isVoxContinuationMode || voxContinuationReferenceSource !== 'profile') return

    const transcriptSeed = getProfileTranscriptSeed(selectedProfile)
    if (!transcriptSeed) return

    setVoxPromptText((current) => {
      if (current.trim() && current !== lastAutoSeededVoxPromptRef.current) {
        return current
      }

      lastAutoSeededVoxPromptRef.current = transcriptSeed
      return transcriptSeed
    })
  }, [
    isVoxContinuationMode,
    selectedProfile,
    voxContinuationReferenceSource,
  ])

  const roughChunkEstimate = useMemo(() => {
    const trimmedScript = script.trim()
    if (trimmedScript.length === 0) return 0

    if (selectedModelId === VOX_MODEL_ID) {
      const singlePassLimit = voxMode === VOX_MODE_CONTINUATION ? VOX_CONTINUATION_SINGLE_PASS_MAX_CHARS : VOX_SINGLE_PASS_MAX_CHARS
      if (trimmedScript.length <= singlePassLimit) {
        return 1
      }
      return Math.ceil(trimmedScript.length / VOX_CHUNK_ESTIMATE_SIZE)
    }

    return Math.ceil(trimmedScript.length / 150)
  }, [script, selectedModelId, voxMode])
  const chunkEstimateLabel =
    roughChunkEstimate === 1 && selectedModelId === VOX_MODEL_ID ? 'Estimated chunks: 1 (single pass)' : `Estimated chunks: ${roughChunkEstimate}`
  const hasRequiredReference = isVoxDesignMode ? true : voxContinuationUsesRecordedReference ? !!voxRecordedReferenceId : !!selectedProfileId
  const profileInputLevelPercent = Math.round(profileRecorder.inputLevel * 100)
  const profileInputMeterState = useMemo(() => {
    if (!profileRecorder.isRecording) {
      return {
        barClassName: 'bg-muted-foreground/30',
        helper: 'The meter activates while recording so you can spot a quiet or clipping mic.',
        label: 'Stand by',
        toneClassName: 'text-muted-foreground',
      }
    }

    if (profileRecorder.inputLevel >= 0.85) {
      return {
        barClassName: 'bg-amber-500',
        helper: 'Back off the mic slightly or lower the input if peaks keep slamming the end of the bar.',
        label: 'Clipping risk',
        toneClassName: 'text-amber-300',
      }
    }

    if (profileRecorder.inputLevel >= 0.18) {
      return {
        barClassName: 'bg-emerald-500',
        helper: 'This looks healthy for a clean reference take.',
        label: 'Healthy',
        toneClassName: 'text-emerald-300',
      }
    }

    return {
      barClassName: 'bg-sky-500',
      helper: 'Move closer to the mic or pick a different input if the voice still sounds distant.',
      label: 'Too quiet',
      toneClassName: 'text-sky-300',
    }
  }, [profileRecorder.inputLevel, profileRecorder.isRecording])
  const canSaveProfile =
    !!profileName.trim() &&
    (profileReferenceSource === 'upload' ? !!profileFile : !!profileRecorder.audioBlob) &&
    !uploadingProfile &&
    !profileRecorder.isRecording
  const canGenerate =
    hasRequiredReference &&
    !!script.trim() &&
    (!isVoxContinuationMode || !!voxPromptText.trim()) &&
    !!selectedModel?.available &&
    (!voxContinuationUsesRecordedReference || !voxRecordedReferencePending) &&
    !submittingJob

  const updateSpeed = (nextValue: number) => {
    const normalized = Number(clampSpeed(nextValue).toFixed(2))
    setSpeed(normalized)
    setSpeedInput(normalized.toFixed(2))
  }

  const handleStartProfileRecording = async () => {
    setProfileError(null)
    profileRecorder.clearRecording()
    await profileRecorder.startRecording()
    void refreshProfileInputDevices()
  }

  const handleCreateProfile = async () => {
    const trimmedProfileName = profileName.trim()
    const trimmedProfileNotes = profileNotes.trim()
    const useRecordedReference = profileReferenceSource === 'record'
    const selectedReference = useRecordedReference ? profileRecorder.audioBlob : profileFile

    if (!trimmedProfileName || !selectedReference) return

    setUploadingProfile(true)
    setUploadProgress(0)
    setProfileError(null)

    try {
      const formData = new FormData()
      formData.append('name', trimmedProfileName)
      formData.append('notes', trimmedProfileNotes)
      if (useRecordedReference) {
        formData.append('recording_source', BROWSER_RECORDED_PROFILE_SOURCE)
        formData.append(
          'audio_file',
          selectedReference,
          `voice-profile-reference-${Date.now()}.${profileRecorder.fileExtension}`,
        )
      } else {
        formData.append('audio_file', selectedReference)
      }

      const createdProfile = await uploadVoiceProfile(formData, setUploadProgress)
      setProfileName('')
      setProfileNotes('')
      setProfileFile(null)
      profileRecorder.clearRecording()
      setUploadProgress(0)
      setShowProfileForm(false)
      await refreshProfiles(createdProfile.id)
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

  const handleUseSavedVoiceProfile = () => {
    if (voxContinuationReferenceSource === 'record') {
      clearRecordedReference()
    }
    setVoxContinuationReferenceSource('profile')
  }

  const handleRecordReferenceNow = () => {
    setVoxContinuationReferenceSource('record')
    setVoxRecordedReferenceError(null)
  }

  const handleStartRecordedReference = async () => {
    clearRecordedReference()
    await voxRecorder.startRecording()
  }

  const handleGenerate = async () => {
    if (!canGenerate || !selectedModel?.available) return

    const requestedSpeed = Number(clampSpeed(parseFloat(speedInput)).toFixed(2))
    const trimmedScript = script.trim()
    const trimmedVoxPromptText = voxPromptText.trim()
    const trimmedVoxStyleText = voxStyleText.trim()

    setSubmittingJob(true)
    setGenerationError(null)
    setSpeed(requestedSpeed)
    setSpeedInput(requestedSpeed.toFixed(2))

    try {
      const payload: Record<string, unknown> = {
        script: trimmedScript,
        model_id: selectedModel.model_id,
        output_format: outputFormat,
        speed: requestedSpeed,
      }

      if (requiresSavedVoiceProfile && selectedProfileId) {
        payload.voice_profile_id = selectedProfileId
      }
      if (voxContinuationUsesRecordedReference && voxRecordedReferenceId) {
        payload.temp_reference_id = voxRecordedReferenceId
      }

      if (isVoxModel) {
        payload.vox_mode = voxMode
        if (isVoxContinuationMode && trimmedVoxPromptText) {
          payload.prompt_text = trimmedVoxPromptText
        }
        if (!isVoxContinuationMode && trimmedVoxStyleText) {
          payload.style_text = trimmedVoxStyleText
        }
      }

      const response = await apiRequest<{ job_id: string; status: string }>('/api/v1/voiceover/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      setTrackedJobs((current) =>
        upsertTrackedJob(current, {
          jobId: response.job_id,
          modelId: selectedModel.model_id,
          modelLabel: selectedModel.display_name,
          profileId: voxContinuationUsesRecordedReference ? voxRecordedReferenceId || 'recorded-reference' : selectedProfileId || 'voice-design',
          profileName: isVoxDesignMode ? 'Voice Design' : voxContinuationUsesRecordedReference ? 'Recorded Reference' : selectedProfile?.name ?? 'Voice',
          createdAt: new Date().toISOString(),
          status: { status: response.status, completed_chunks: 0, total_chunks: 0 },
        }),
      )
    } catch (error: unknown) {
      setGenerationError(error instanceof Error ? error.message : 'Failed to start voiceover job')
    } finally {
      setSubmittingJob(false)
    }
  }

  const deleteRecentVoiceoverById = async (jobId: string) => {
    await apiRequest<{ deleted: boolean }>(`/api/v1/voiceover/output/${jobId}`, {
      method: 'DELETE',
    })
    setTrackedJobs((current) => current.filter((job) => job.jobId !== jobId))
    setRecentVoiceovers((current) => current.filter((voiceover) => voiceover.job_id !== jobId))
  }

  const handleDeleteRecentVoiceover = async (item: RecentVoiceover) => {
    const confirmed = window.confirm(`Delete recent voiceover "${item.filename}"?`)
    if (!confirmed) return

    setDeletingOutputId(item.job_id)
    setRecentError(null)
    try {
      await deleteRecentVoiceoverById(item.job_id)
    } catch (error: unknown) {
      setRecentError(error instanceof Error ? error.message : 'Failed to delete voiceover output')
    } finally {
      setDeletingOutputId(null)
    }
  }

  const toggleRecentVoiceoverSelection = (jobId: string) => {
    setSelectedOutputIds((current) =>
      current.includes(jobId) ? current.filter((candidate) => candidate !== jobId) : [...current, jobId],
    )
  }

  const toggleSelectAllRecentVoiceovers = () => {
    setSelectedOutputIds((current) => (current.length === recentVoiceovers.length ? [] : recentVoiceovers.map((item) => item.job_id)))
  }

  const triggerRecentVoiceoverDownload = async (item: RecentVoiceover) => {
    if (typeof document === 'undefined') return

    const anchor = document.createElement('a')
    anchor.href = item.output_url
    anchor.download = item.filename
    anchor.style.display = 'none'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()

    await new Promise((resolve) => window.setTimeout(resolve, 180))
  }

  const handleBulkDownloadRecentVoiceovers = async () => {
    if (selectedRecentVoiceovers.length === 0) return

    setBulkDownloadingOutputs(true)
    try {
      for (const item of selectedRecentVoiceovers) {
        await triggerRecentVoiceoverDownload(item)
      }
    } finally {
      setBulkDownloadingOutputs(false)
    }
  }

  const handleBulkDeleteRecentVoiceovers = async () => {
    if (selectedRecentVoiceovers.length === 0) return

    const confirmed = window.confirm(`Delete ${selectedRecentVoiceovers.length} selected voiceover output(s)?`)
    if (!confirmed) return

    setBulkDeletingOutputs(true)
    setRecentError(null)

    const results = await Promise.allSettled(
      selectedRecentVoiceovers.map((item) => deleteRecentVoiceoverById(item.job_id)),
    )

    const failedCount = results.filter((result) => result.status === 'rejected').length
    if (failedCount > 0) {
      setRecentError(
        failedCount === 1 ? '1 selected output failed to delete' : `${failedCount} selected outputs failed to delete`,
      )
    }

    setSelectedOutputIds((current) =>
      current.filter((jobId) => results.some((result, index) => result.status === 'rejected' && selectedRecentVoiceovers[index]?.job_id === jobId)),
    )
    setBulkDeletingOutputs(false)
  }

  const workspaceTabs: Array<{
    id: VoiceoverWorkspaceTab
    label: string
    icon: typeof Wand2
    helper: string
    badge: string
  }> = [
    {
      id: 'generate',
      label: 'Generate',
      icon: Wand2,
      helper: 'Main workspace',
      badge: activeJobs.length > 0 ? `${activeJobs.length} live` : selectedModel?.display_name ?? 'Ready',
    },
    {
      id: 'profiles',
      label: 'Profiles',
      icon: Mic2,
      helper: 'Reusable voices',
      badge: profilesLoading ? 'Loading...' : `${profiles.length}`,
    },
    {
      id: 'outputs',
      label: 'Outputs',
      icon: History,
      helper: 'Recent renders',
      badge: recentLoading ? 'Loading...' : `${recentVoiceovers.length}`,
    },
  ]

  const renderProfilesPanel = () => (
    <Card className="overflow-hidden border-border/70 bg-card shadow-sm">
      <div className="border-b border-border/50 bg-muted/15 px-6 py-5">
        <div className="flex items-start gap-3">
          <div className="rounded-xl bg-primary/12 p-2.5 text-primary">
            <Mic2 className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-lg font-semibold tracking-tight">Voice Profiles</p>
                <p className="mt-2 text-sm text-muted-foreground">Save a reusable reference once, then use it everywhere.</p>
              </div>
              <p className="text-xs text-muted-foreground">{profilesLoading ? 'Loading...' : `${profiles.length} saved`}</p>
            </div>
          </div>
        </div>
      </div>

      <CardContent className="space-y-5 pt-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-sm text-muted-foreground">
            {profilesLoading ? 'Loading profiles...' : `${profiles.length} saved voice profile${profiles.length === 1 ? '' : 's'}`}
          </div>
          <Button
            type="button"
            variant={showProfileForm ? 'secondary' : 'outline'}
            onClick={() => setShowProfileForm((current) => !current)}
          >
            {showProfileForm ? 'Close' : 'Add Profile'}
          </Button>
        </div>

        {showProfileForm && (
          <div className="space-y-4 rounded-xl bg-background/55 p-4 ring-1 ring-border/60">
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
                <Label>Reference Source</Label>
                <div className="flex flex-wrap rounded-lg border border-border/70 bg-background/60 p-1">
                  <button
                    type="button"
                    onClick={() => {
                      setProfileReferenceSource('upload')
                      setProfileError(null)
                    }}
                    className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                      profileReferenceSource === 'upload'
                        ? 'bg-primary text-primary-foreground'
                        : 'text-muted-foreground hover:bg-background/80 hover:text-foreground'
                    }`}
                  >
                    Upload Reference Clip
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setProfileReferenceSource('record')
                      setProfileError(null)
                    }}
                    className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                      profileReferenceSource === 'record'
                        ? 'bg-primary text-primary-foreground'
                        : 'text-muted-foreground hover:bg-background/80 hover:text-foreground'
                    }`}
                  >
                    Record Reference Now
                  </button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Use a quiet room and close mic placement. NeonForge stores a high-quality WAV master.
                </p>
              </div>
            </div>

            <div className="space-y-2">
              <Label>Reference Clip</Label>
              {profileReferenceSource === 'upload' ? (
                <FileDropzone
                  accept=".wav,.mp3,.m4a,audio/wav,audio/mpeg,audio/mp4,audio/x-m4a"
                  label="Drop a WAV, MP3, or M4A sample"
                  hint={profileFile?.name || '3-30 seconds recommended'}
                  file={profileFile}
                  onFileChange={(file) => {
                    setProfileFile(file)
                    setProfileError(null)
                  }}
                  maxSizeMB={25}
                  icon="audio"
                />
              ) : (
                <div className="space-y-3 rounded-xl bg-background/60 p-4 ring-1 ring-border/55">
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="voice-profile-input-device">Microphone Input</Label>
                      <select
                        id="voice-profile-input-device"
                        value={profileInputDeviceId}
                        onChange={(event) => {
                          setProfileInputDeviceId(event.target.value)
                          setProfileError(null)
                        }}
                        className="h-10 w-full rounded-md border border-input bg-background/80 px-3 text-sm"
                      >
                        <option value="">Browser default microphone</option>
                        {profileInputDevices.map((device, index) => (
                          <option key={device.deviceId} value={device.deviceId}>
                            {getAudioInputDeviceLabel(device, index)}
                          </option>
                        ))}
                      </select>
                      <p className="text-xs text-muted-foreground">
                        Choose the exact mic if the browser default is not the one you want.
                      </p>
                    </div>

                    <div className="space-y-2">
                      <Label>Recording Mode</Label>
                      <div className="grid gap-2 sm:grid-cols-2">
                        <button
                          type="button"
                          aria-pressed={profileCaptureMode === 'raw'}
                          onClick={() => setProfileCaptureMode('raw')}
                          className={`rounded-xl border px-3 py-3 text-left transition-colors ${
                            profileCaptureMode === 'raw'
                              ? 'border-primary bg-primary/12 text-foreground shadow-sm'
                              : 'border-border/60 bg-background/60 text-muted-foreground hover:border-border hover:text-foreground'
                          }`}
                        >
                          <p className="text-sm font-semibold">Raw Reference Capture</p>
                          <p className="mt-1 text-xs leading-5">Better for accurate voice capture and cloning quality.</p>
                        </button>
                        <button
                          type="button"
                          aria-pressed={profileCaptureMode === 'enhanced'}
                          onClick={() => setProfileCaptureMode('enhanced')}
                          className={`rounded-xl border px-3 py-3 text-left transition-colors ${
                            profileCaptureMode === 'enhanced'
                              ? 'border-primary bg-primary/12 text-foreground shadow-sm'
                              : 'border-border/60 bg-background/60 text-muted-foreground hover:border-border hover:text-foreground'
                          }`}
                        >
                          <p className="text-sm font-semibold">Enhanced Reference Capture</p>
                          <p className="mt-1 text-xs leading-5">Can sound closer and more polished in untreated rooms.</p>
                        </button>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Raw = better for accurate voice capture / cloning. Enhanced = can sound closer and more polished in untreated rooms.
                      </p>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-3">
                    {!profileRecorder.isRecording ? (
                      <Button
                        type="button"
                        variant="outline"
                        className="gap-2"
                        onClick={() => void handleStartProfileRecording()}
                      >
                        <Mic2 className="h-4 w-4" />
                        Start Recording
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        variant="destructive"
                        className="gap-2"
                        onClick={profileRecorder.stopRecording}
                      >
                        <Square className="h-3.5 w-3.5" />
                        Stop Recording
                      </Button>
                    )}

                    {profileRecorder.isRecording && (
                      <div className="flex items-center gap-2 text-sm text-foreground/85">
                        <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-red-500" />
                        <span className="font-mono tabular-nums">{formatDuration(profileRecorder.duration)}</span>
                      </div>
                    )}

                    {profileRecorder.audioUrl && !profileRecorder.isRecording && (
                      <Button
                        type="button"
                        variant="ghost"
                        className="gap-2"
                        onClick={() => {
                          profileRecorder.clearRecording()
                          setProfileError(null)
                        }}
                      >
                        <Trash2 className="h-4 w-4" />
                        Discard / Re-record
                      </Button>
                    )}
                  </div>

                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-3 text-xs">
                      <span className="text-muted-foreground">Live Input Level</span>
                      <span className={cn('font-medium', profileInputMeterState.toneClassName)}>
                        {profileInputMeterState.label}
                        {profileRecorder.isRecording ? ` · ${profileInputLevelPercent}%` : ''}
                      </span>
                    </div>
                    <div className="h-2.5 overflow-hidden rounded-full bg-secondary/90">
                      <div
                        className={cn(
                          'h-full rounded-full transition-[width] duration-100 ease-out',
                          profileInputMeterState.barClassName,
                        )}
                        style={{
                          width: `${Math.max(
                            profileRecorder.isRecording && profileInputLevelPercent > 0 ? 2 : 0,
                            profileInputLevelPercent,
                          )}%`,
                        }}
                      />
                    </div>
                    <p className="text-[11px] text-muted-foreground">{profileInputMeterState.helper}</p>
                  </div>

                  {profileRecorder.audioUrl && !profileRecorder.isRecording && (
                    <audio controls className="w-full" src={profileRecorder.audioUrl}>
                      Your browser does not support audio playback.
                    </audio>
                  )}

                  {profileRecorder.audioUrl && !profileRecorder.isRecording && (
                    <div className="rounded-lg border border-emerald-500/25 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-100">
                      Recording ready to save as a reusable voice profile.
                      {profileRecorder.mimeType ? ` Captured as ${profileRecorder.mimeType}.` : ''}
                    </div>
                  )}

                  {profileRecorder.error && (
                    <div className="flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
                      <span>{profileRecorder.error}</span>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="voice-profile-notes">Notes</Label>
              <Textarea
                id="voice-profile-notes"
                value={profileNotes}
                onChange={(event) => setProfileNotes(event.target.value)}
                placeholder="Optional context, tone, or pronunciation notes"
                rows={3}
                className="bg-background/80"
              />
              <p className="text-xs text-muted-foreground">Transcript is captured automatically after save.</p>
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
              <Button type="button" className="gap-2" onClick={handleCreateProfile} disabled={!canSaveProfile}>
                {uploadingProfile ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
                {uploadingProfile ? 'Uploading...' : 'Save Profile'}
              </Button>
              <p className="text-xs text-muted-foreground">
                {profileReferenceSource === 'upload' ? 'WAV, MP3, and M4A are accepted.' : 'Record a short clip, preview it, then save it.'}
              </p>
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
            const transcriptPreview = truncateText(profile.reference_transcript, 180)
            const notesPreview =
              profile.notes && profile.notes !== profile.reference_transcript ? truncateText(profile.notes, 140) : ''

            return (
              <div key={profile.id} className="rounded-xl bg-background/55 p-4 ring-1 ring-border/55">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-semibold">{profile.name}</p>
                      {profile.reference_transcript && (
                        <span className="rounded-full border border-emerald-500/25 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-200">
                          Transcript ready
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">Saved {dateFormatter.format(new Date(profile.created_at))}</p>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="gap-2"
                      onClick={() => handlePlayProfile(profile)}
                    >
                      <Play className="h-3.5 w-3.5" />
                      Play
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="gap-2"
                      onClick={() => void handleDeleteProfile(profile)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      Delete
                    </Button>
                  </div>
                </div>

                {transcriptPreview && (
                  <div className="mt-3 rounded-lg bg-background/70 px-3 py-2 ring-1 ring-border/45">
                    <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-primary/75">Reference Transcript</p>
                    <p className="mt-1 text-sm text-foreground/90">{transcriptPreview}</p>
                  </div>
                )}

                {notesPreview && <p className="mt-3 text-sm text-muted-foreground">Notes: {notesPreview}</p>}

                {isPreviewing && (
                  <audio key={previewUrl} controls autoPlay className="mt-3 w-full" src={previewUrl}>
                    Your browser does not support audio playback.
                  </audio>
                )}
              </div>
            )
          })}

          {!profilesLoading && profiles.length === 0 && (
            <div className="rounded-xl border border-dashed border-border/60 p-6 text-center text-sm text-muted-foreground">
              Save your first voice profile to reuse it later.
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )

  const renderRecentVoiceoversPanel = () => (
    <Card className="overflow-hidden border-border/70 bg-card shadow-sm">
      <div className="border-b border-border/50 bg-muted/15 px-6 py-5">
        <div className="flex items-start gap-3">
          <div className="rounded-xl bg-primary/12 p-2.5 text-primary">
            <History className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-lg font-semibold tracking-tight">Recent Voiceovers</p>
                <p className="mt-2 text-sm text-muted-foreground">Select, download, or clear finished renders.</p>
              </div>
              <p className="text-xs text-muted-foreground">{recentLoading ? 'Loading...' : `${recentVoiceovers.length} saved`}</p>
            </div>
          </div>
        </div>
      </div>

      <CardContent className="space-y-5 pt-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            {!recentLoading && recentVoiceovers.length > 0 && (
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-input bg-background accent-primary"
                  checked={allRecentVoiceoversSelected}
                  onChange={toggleSelectAllRecentVoiceovers}
                  disabled={bulkDeletingOutputs}
                />
                Select all
              </label>
            )}
            {selectedRecentVoiceovers.length > 0 && (
              <span className="rounded-full border border-border/60 bg-background/60 px-3 py-1 text-xs font-medium text-foreground/90">
                {selectedRecentVoiceovers.length} selected
              </span>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            {selectedRecentVoiceovers.length > 0 && (
              <>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="gap-2"
                  onClick={() => void handleBulkDownloadRecentVoiceovers()}
                  disabled={bulkDownloadingOutputs || bulkDeletingOutputs}
                >
                  <Download className="h-3.5 w-3.5" />
                  {bulkDownloadingOutputs ? 'Downloading...' : 'Download Selected'}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="gap-2"
                  onClick={() => void handleBulkDeleteRecentVoiceovers()}
                  disabled={bulkDeletingOutputs || bulkDownloadingOutputs}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  {bulkDeletingOutputs ? 'Deleting...' : 'Delete Selected'}
                </Button>
              </>
            )}
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="gap-2"
              onClick={() => void refreshRecentVoiceovers()}
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </Button>
          </div>
        </div>

        {selectedRecentVoiceovers.length > 1 && (
          <p className="text-xs text-muted-foreground">
            Browsers may ask you to allow multiple downloads when you bulk-download files.
          </p>
        )}

        {recentError && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            {recentError}
          </div>
        )}

        <div className="space-y-3">
          {recentVoiceovers.map((item) => {
            const isSelected = selectedOutputIds.includes(item.job_id)

            return (
              <div
                key={item.job_id}
                className={cn(
                  'rounded-xl bg-background/55 p-4 ring-1 ring-border/55 transition-colors',
                  isSelected && 'bg-background/75 ring-2 ring-primary/35',
                )}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex min-w-0 items-start gap-3">
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 rounded border-input bg-background accent-primary"
                      checked={isSelected}
                      onChange={() => toggleRecentVoiceoverSelection(item.job_id)}
                      disabled={bulkDeletingOutputs}
                    />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold">{item.filename}</p>
                      <p className="mt-1 text-xs text-muted-foreground">Saved {dateFormatter.format(new Date(item.created_at))}</p>
                    </div>
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
                      disabled={deletingOutputId === item.job_id || bulkDeletingOutputs}
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
            )
          })}

          {!recentLoading && recentVoiceovers.length === 0 && (
            <div className="rounded-xl border border-dashed border-border/60 p-6 text-center text-sm text-muted-foreground">
              Completed voiceovers will appear here after a render finishes.
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )

  const renderGeneratePanel = () => {
    const latestRecentVoiceover = recentVoiceovers[0] ?? null
    const activeVoxModeOption = VOX_MODE_OPTIONS.find((option) => option.value === voxMode) ?? null
    const hasSelectedModel = !!selectedModel
    const showVoiceProfileSelection = hasSelectedModel && !isVoxContinuationMode && requiresSavedVoiceProfile
    const selectedProfileTranscript = getProfileTranscriptSeed(selectedProfile)
    const referenceSummary = isVoxDesignMode
      ? 'No reference required'
      : voxContinuationUsesRecordedReference
        ? voxRecordedReferenceId
          ? 'Recorded temp clip ready'
          : 'Record a temp clip'
        : selectedProfile?.name ?? 'Choose a saved profile'
    const activitySummary =
      activeJobs.length > 0
        ? `${activeJobs.length} active job${activeJobs.length === 1 ? '' : 's'}`
        : trackedJobs.length > 0
          ? `${trackedJobs.length} tracked job${trackedJobs.length === 1 ? '' : 's'}`
          : 'Ready for the next render'
    const workspaceStatusNote =
      activeJobs.length > 0
        ? 'Job Activity updates live on the right.'
        : latestRecentVoiceover
          ? `Latest output: ${latestRecentVoiceover.filename}`
          : hasSelectedModel
            ? 'Choose the remaining fields, then render.'
            : 'Choose a model to start.'
    const scriptLabel = isVoxContinuationMode ? 'New Script' : 'Script'
    const scriptHelper = isVoxContinuationMode
      ? 'Write the narration that should follow the reference clip.'
      : 'Paste the narration you want to render.'
    const scriptPlaceholder = isVoxContinuationMode
      ? 'Write the next narration that should continue after the reference clip.'
      : 'Paste the voiceover script here.'

    const renderScriptPanel = () => (
      <div className="space-y-2 rounded-xl bg-background/55 p-4 ring-1 ring-border/55">
        <Label htmlFor="voiceover-script">{scriptLabel}</Label>
        <p className="text-xs text-muted-foreground">{scriptHelper}</p>
        <Textarea
          id="voiceover-script"
          value={script}
          onChange={(event) => setScript(event.target.value)}
          placeholder={scriptPlaceholder}
          rows={10}
          className="min-h-[220px] bg-background/85"
        />
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>{script.length} characters</span>
          <span>{chunkEstimateLabel}</span>
        </div>
      </div>
    )

    return (
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.7fr),320px] 2xl:grid-cols-[minmax(0,1.85fr),340px]">
        <div className="space-y-6">
          <div className="rounded-2xl border border-border/70 bg-card p-4 shadow-sm">
            <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                <div className="rounded-xl bg-background/65 px-3 py-2 ring-1 ring-border/55">
                  <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-primary/75">Model</p>
                  <p className="mt-1 text-sm font-semibold">{selectedModel?.display_name ?? 'Select a model'}</p>
                </div>
                <div className="rounded-xl bg-background/65 px-3 py-2 ring-1 ring-border/55">
                  <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-primary/75">
                    {isVoxModel ? 'Mode' : 'Reference'}
                  </p>
                  <p className="mt-1 text-sm font-semibold">
                    {isVoxModel ? activeVoxModeOption?.shortLabel ?? 'Choose a mode' : referenceSummary}
                  </p>
                </div>
                <div className="rounded-xl bg-background/65 px-3 py-2 ring-1 ring-border/55">
                  <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-primary/75">Activity</p>
                  <p className="mt-1 text-sm font-semibold">{activitySummary}</p>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2 xl:justify-end">
                <p className="text-xs text-muted-foreground">{workspaceStatusNote}</p>
                {recentVoiceovers.length > 0 && (
                  <Button type="button" variant="outline" size="sm" onClick={() => setActiveWorkspaceTab('outputs')}>
                    Open Outputs
                  </Button>
                )}
              </div>
            </div>
          </div>

          <Card className="overflow-hidden border-border/70 bg-card shadow-sm">
            <div className="border-b border-border/50 bg-muted/15 px-6 py-5">
              <div className="flex items-start gap-3">
                <div className="rounded-xl bg-primary/12 p-2.5 text-primary">
                  <Wand2 className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-lg font-semibold tracking-tight">Generate Voiceover</p>
                  <p className="mt-2 text-sm text-muted-foreground">Choose a path, fill the relevant fields, then render.</p>
                </div>
              </div>
            </div>

            <CardContent className="space-y-6 pt-6">
              <div className="space-y-2 rounded-xl bg-background/55 p-4 ring-1 ring-border/55">
                <Label htmlFor="voice-model-select">1. TTS Model</Label>
                <select
                  id="voice-model-select"
                  value={selectedModelId}
                  onChange={(event) => setSelectedModelId(event.target.value)}
                  className="h-10 w-full rounded-md border border-input bg-background/85 px-3 text-sm"
                >
                  <option value="">Select a model</option>
                  {models.map((model) => (
                    <option key={model.model_id} value={model.model_id} disabled={!model.available}>
                      {model.display_name}
                      {model.available ? '' : ' (unavailable)'}
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

              {!hasSelectedModel && (
                <div className="rounded-xl border border-dashed border-border/60 bg-background/45 p-6 text-center text-sm text-muted-foreground">
                  Choose a TTS model to reveal the next step.
                </div>
              )}

              {hasSelectedModel && isVoxModel && (
                <div className="space-y-3 rounded-xl bg-background/55 p-4 ring-1 ring-border/55">
                  <div>
                    <p className="text-sm font-semibold">2. Voice Mode</p>
                    <p className="mt-1 text-xs text-muted-foreground">Pick the Vox path that matches this render.</p>
                  </div>
                  <div className="grid gap-2 md:grid-cols-3">
                    {VOX_MODE_OPTIONS.map((option) => {
                      const isActive = voxMode === option.value
                      return (
                        <button
                          key={option.value}
                          type="button"
                          aria-pressed={isActive}
                          onClick={() => setVoxMode(option.value)}
                          className={`rounded-xl border px-4 py-3 text-left transition-colors ${
                            isActive
                              ? 'border-primary bg-primary text-primary-foreground shadow-md shadow-primary/10'
                              : 'border-border/60 bg-background/60 text-foreground hover:border-border hover:bg-background/80'
                          }`}
                        >
                          <p className="text-sm font-semibold">{option.shortLabel}</p>
                          <p className={cn('mt-1 text-xs', isActive ? 'text-primary-foreground/85' : 'text-muted-foreground')}>
                            {option.helper}
                          </p>
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}

              {showVoiceProfileSelection && (
                <div className="space-y-2 rounded-xl bg-background/55 p-4 ring-1 ring-border/55">
                  <Label htmlFor="voice-profile-select">{isVoxModel ? '3. Voice Profile' : '2. Voice Profile'}</Label>
                  <select
                    id="voice-profile-select"
                    value={selectedProfileId}
                    onChange={(event) => setSelectedProfileId(event.target.value)}
                    className="h-10 w-full rounded-md border border-input bg-background/85 px-3 text-sm"
                  >
                    <option value="">Select a saved profile</option>
                    {profiles.map((profile) => (
                      <option key={profile.id} value={profile.id}>
                        {profile.name}
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-muted-foreground">
                    {isVoxModel ? 'Clone uses a saved reference clip.' : 'Choose the saved reference clip to use.'}
                  </p>
                </div>
              )}

              {hasSelectedModel && isVoxDesignMode && (
                <div className="rounded-xl bg-background/55 px-4 py-3 ring-1 ring-border/55">
                  <p className="text-sm font-medium">Voice Design skips saved reference audio.</p>
                </div>
              )}

              {showVoiceProfileSelection && !profilesLoading && profiles.length === 0 && (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-amber-500/30 bg-amber-500/10 p-4">
                  <div>
                    <p className="text-sm font-semibold">Saved voice profile required</p>
                    <p className="mt-1 text-xs text-amber-100/80">Add a reusable profile before you render.</p>
                  </div>
                  <Button type="button" variant="outline" size="sm" onClick={() => setActiveWorkspaceTab('profiles')}>
                    Open Profiles
                  </Button>
                </div>
              )}

              {hasSelectedModel && isVoxContinuationMode && (
                <div className="space-y-4 rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4 sm:p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold">Vox Continuation Workspace</p>
                      <p className="mt-1 text-xs text-amber-100/80">Source, reference, transcript, then new script.</p>
                    </div>
                    <div className="flex flex-wrap gap-2 text-[11px] font-medium uppercase tracking-[0.16em] text-amber-100/70">
                      <span className="rounded-full border border-amber-500/20 px-2.5 py-1">1 Source</span>
                      <span className="rounded-full border border-amber-500/20 px-2.5 py-1">2 Reference</span>
                      <span className="rounded-full border border-amber-500/20 px-2.5 py-1">3 Transcript</span>
                    </div>
                  </div>

                  <div className="space-y-4">
                    <div className="space-y-4 rounded-xl bg-background/25 p-4 ring-1 ring-amber-500/15">
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-100/70">Step 1</p>
                        <p className="mt-1 text-sm font-semibold">Reference source</p>
                        <p className="mt-1 text-xs text-amber-100/80">Choose a saved profile or record a temporary clip.</p>
                      </div>
                      <div className="grid gap-3 md:grid-cols-2">
                        <button
                          type="button"
                          aria-pressed={voxContinuationReferenceSource === 'profile'}
                          onClick={handleUseSavedVoiceProfile}
                          className={`rounded-xl border px-4 py-4 text-left transition-colors ${
                            voxContinuationReferenceSource === 'profile'
                              ? 'border-amber-400/40 bg-amber-500/15 text-foreground'
                              : 'border-amber-500/20 bg-background/25 text-amber-100/75 hover:text-foreground'
                          }`}
                        >
                          <p className="text-sm font-semibold">Use Saved Voice Profile</p>
                          <p className="mt-1 text-xs leading-5">Stable reusable source.</p>
                        </button>
                        <button
                          type="button"
                          aria-pressed={voxContinuationReferenceSource === 'record'}
                          onClick={handleRecordReferenceNow}
                          className={`rounded-xl border px-4 py-4 text-left transition-colors ${
                            voxContinuationReferenceSource === 'record'
                              ? 'border-amber-400/40 bg-amber-500/15 text-foreground'
                              : 'border-amber-500/20 bg-background/25 text-amber-100/75 hover:text-foreground'
                          }`}
                        >
                          <p className="text-sm font-semibold">Record Reference Now</p>
                          <p className="mt-1 text-xs leading-5">Temporary clip for this run.</p>
                        </button>
                      </div>

                      {voxContinuationReferenceSource === 'profile' ? (
                        <div className="grid gap-4 lg:grid-cols-[minmax(0,360px),minmax(0,1fr)]">
                          <div className="space-y-2">
                            <Label htmlFor="voice-profile-select">Saved Voice Profile</Label>
                            <select
                              id="voice-profile-select"
                              value={selectedProfileId}
                              onChange={(event) => setSelectedProfileId(event.target.value)}
                              className="h-10 w-full rounded-md border border-input bg-background/85 px-3 text-sm"
                            >
                              <option value="">Select a saved profile</option>
                              {profiles.map((profile) => (
                                <option key={profile.id} value={profile.id}>
                                  {profile.name}
                                </option>
                              ))}
                            </select>
                            <p className="text-xs text-amber-100/80">
                              Step 3 uses the saved transcript when available.
                            </p>
                          </div>
                          <div className="rounded-xl border border-amber-500/20 bg-background/30 p-4">
                            <p className="text-sm font-medium">
                              {selectedProfile ? `Selected saved profile: ${selectedProfile.name}` : 'Choose a saved voice profile to continue from.'}
                            </p>
                            {selectedProfileTranscript ? (
                              <p className="mt-2 text-xs text-amber-100/80">
                                Stored transcript: {truncateText(selectedProfileTranscript, 120)}
                              </p>
                            ) : (
                              <p className="mt-2 text-xs text-amber-100/80">No stored transcript yet. You can paste it in Step 3.</p>
                            )}
                          </div>
                        </div>
                      ) : (
                        <div className="rounded-xl border border-amber-500/20 bg-background/30 px-4 py-3 text-sm text-amber-50/90">
                          Temporary reference mode keeps the clip only for this run.
                        </div>
                      )}
                    </div>

                    <div className="space-y-4 rounded-xl bg-background/25 p-4 ring-1 ring-amber-500/15">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-100/70">Step 2</p>
                          <p className="mt-1 text-sm font-semibold">
                            {voxContinuationUsesRecordedReference ? 'Capture the continuation reference' : 'Confirm the saved reference'}
                          </p>
                          <p className="mt-1 text-xs text-amber-100/80">
                            {voxContinuationUsesRecordedReference
                              ? 'Record, preview, then let NeonForge transcribe it.'
                              : 'Review the saved profile before moving on.'}
                          </p>
                        </div>
                        {voxContinuationUsesRecordedReference && (
                          <div className="flex flex-wrap items-center gap-3">
                            {!voxRecorder.isRecording ? (
                              <Button
                                type="button"
                                variant="outline"
                                className="gap-2"
                                onClick={() => void handleStartRecordedReference()}
                              >
                                <Mic2 className="h-4 w-4" />
                                Start Recording
                              </Button>
                            ) : (
                              <Button
                                type="button"
                                variant="destructive"
                                className="gap-2"
                                onClick={voxRecorder.stopRecording}
                              >
                                <Square className="h-3.5 w-3.5" />
                                Stop Recording
                              </Button>
                            )}

                            {voxRecorder.isRecording && (
                              <div className="flex items-center gap-2 text-sm text-amber-50/90">
                                <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-red-500" />
                                <span className="font-mono tabular-nums">{formatDuration(voxRecorder.duration)}</span>
                              </div>
                            )}

                            {(voxRecorder.audioUrl || voxRecordedReferenceId || voxRecordedReferencePending) &&
                              !voxRecorder.isRecording && (
                                <Button
                                  type="button"
                                  variant="ghost"
                                  className="gap-2"
                                  onClick={() => clearRecordedReference()}
                                >
                                  <Trash2 className="h-4 w-4" />
                                  Discard / Re-record
                                </Button>
                              )}
                          </div>
                        )}
                      </div>

                      {voxContinuationUsesRecordedReference ? (
                        <>
                          {voxRecorder.audioUrl && !voxRecorder.isRecording && (
                            <audio controls className="w-full" src={voxRecorder.audioUrl}>
                              Your browser does not support audio playback.
                            </audio>
                          )}

                          {voxRecordedReferencePending && (
                            <div className="flex items-center gap-2 rounded-lg border border-amber-500/20 bg-background/35 px-3 py-2 text-sm text-amber-50/90">
                              <Loader2 className="h-4 w-4 animate-spin" />
                              Uploading and transcribing your recorded reference clip...
                            </div>
                          )}

                          {voxRecordedReferenceId && !voxRecordedReferencePending && (
                            <div className="rounded-lg border border-emerald-500/25 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-100">
                              Recorded reference ready. Step 3 is editable.
                            </div>
                          )}

                          {voxRecordedReferenceError && (
                            <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
                              {voxRecordedReferenceError}
                            </div>
                          )}

                          {voxRecorder.error && (
                            <div className="flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
                              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
                              <span>{voxRecorder.error}</span>
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="rounded-xl border border-amber-500/20 bg-background/30 p-4">
                          <div className="flex flex-wrap items-center justify-between gap-3">
                            <div>
                              <p className="text-sm font-medium">
                                {selectedProfile ? `Using saved profile: ${selectedProfile.name}` : 'Pick a saved voice profile in Step 1.'}
                              </p>
                              <p className="mt-2 text-xs text-amber-100/80">
                                Vox will use that saved clip as the continuation anchor for this run.
                              </p>
                            </div>
                            {selectedProfile && (
                              <span className="rounded-full border border-amber-500/20 px-3 py-1 text-xs font-medium text-amber-50/90">
                                Saved reference ready
                              </span>
                            )}
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="space-y-3 rounded-xl bg-background/25 p-4 ring-1 ring-amber-500/15">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-100/70">Step 3</p>
                          <Label htmlFor="vox-prompt-text" className="mt-1 block">
                            Reference Transcript
                          </Label>
                          <p className="mt-1 text-xs text-amber-100/80">
                            {voxContinuationUsesRecordedReference
                              ? 'ASR auto-fills this after recording.'
                              : selectedProfileTranscript
                                ? 'Seeded from the saved profile transcript.'
                                : 'Paste the exact transcript of the saved reference clip.'}
                          </p>
                        </div>
                        {(voxContinuationUsesRecordedReference && voxRecordedReferenceId) ||
                        (!voxContinuationUsesRecordedReference && selectedProfileTranscript) ? (
                          <span className="rounded-full border border-amber-500/20 px-3 py-1 text-xs font-medium text-amber-50/90">
                            {voxContinuationUsesRecordedReference ? 'Auto-filled from ASR' : 'Seeded from profile'}
                          </span>
                        ) : null}
                      </div>
                      <Textarea
                        id="vox-prompt-text"
                        value={voxPromptText}
                        onChange={(event) => setVoxPromptText(event.target.value)}
                        placeholder={
                          voxContinuationUsesRecordedReference
                            ? 'ASR will fill this after you stop recording.'
                            : 'Paste the exact transcript of the saved reference clip.'
                        }
                        rows={9}
                        className="min-h-[240px] bg-background/85"
                      />
                    </div>
                  </div>
                </div>
              )}

              {hasSelectedModel && !isVoxContinuationMode && renderScriptPanel()}

              {hasSelectedModel && isVoxModel && !isVoxContinuationMode && (
                <details className="rounded-xl bg-background/55 p-4 ring-1 ring-border/55" open={voxStyleText.trim().length > 0}>
                  <summary className="cursor-pointer list-none text-sm font-semibold">
                    Style / Control
                  </summary>
                  <p className="mt-2 text-xs text-muted-foreground">Optional voice guidance.</p>
                  <Textarea
                    id="vox-style-text"
                    value={voxStyleText}
                    onChange={(event) => setVoxStyleText(event.target.value)}
                    placeholder="Warm, confident, slightly slower"
                    rows={3}
                    className="mt-3 bg-background/85"
                  />
                </details>
              )}

              {hasSelectedModel && isVoxContinuationMode && renderScriptPanel()}

              {hasSelectedModel && (
                <div className="space-y-4 rounded-xl bg-background/55 p-4 ring-1 ring-border/55">
                <div>
                  <p className="text-sm font-semibold">{isVoxContinuationMode ? '4. Render' : isVoxModel ? '4. Render' : '3. Render'}</p>
                  <p className="mt-1 text-xs text-muted-foreground">Output controls come last.</p>
                </div>
                <div className="grid gap-4 md:grid-cols-[minmax(0,1fr),220px,auto] md:items-end">
                  <div className="space-y-3 rounded-xl bg-background/70 p-4 ring-1 ring-border/45">
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
                        className="w-24 bg-background/85 font-mono text-sm"
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
                      className="h-10 w-full rounded-md border border-input bg-background/85 px-3 text-sm"
                    >
                      <option value="wav">wav</option>
                      <option value="mp3">mp3</option>
                    </select>
                  </div>

                  <div className="flex items-end">
                    <Button
                      type="button"
                      size="lg"
                      className="w-full gap-2 px-8 font-semibold md:min-w-[180px]"
                      disabled={!canGenerate}
                      onClick={handleGenerate}
                    >
                      {submittingJob ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
                      {submittingJob ? 'Submitting...' : 'Render'}
                    </Button>
                  </div>
                </div>
                </div>
              )}

              {generationError && (
                <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                  {generationError}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        <VoiceoverJobsPanel
          activeJobsCount={activeJobs.length}
          latestRecentVoiceover={latestRecentVoiceover}
          onOpenOutputs={() => setActiveWorkspaceTab('outputs')}
          recentVoiceoversCount={recentVoiceovers.length}
          trackedJobs={trackedJobs}
          className="xl:sticky xl:top-6 xl:self-start"
        />
      </div>
    )
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <Mic2 className="h-6 w-6 text-primary" />
          Voiceover Studio
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">Profiles, guided generation, and recent renders in one place.</p>
      </div>

      <Card className="border-border/70 bg-card shadow-sm">
        <CardContent className="p-4">
          <div className="flex flex-wrap gap-2">
            {workspaceTabs.map((tab) => {
              const Icon = tab.icon
              const active = activeWorkspaceTab === tab.id
              return (
                <Button
                  key={tab.id}
                  type="button"
                  variant={active ? 'default' : 'outline'}
                  className={cn(
                    'gap-2',
                    active ? 'shadow-sm' : 'border-border/70 bg-background/60 hover:bg-background/80',
                  )}
                  onClick={() => setActiveWorkspaceTab(tab.id)}
                >
                  <Icon className="h-4 w-4" />
                  {tab.label}
                  <span
                    className={cn(
                      'rounded-full px-2 py-0.5 text-[10px] font-medium',
                      active ? 'bg-background/15 text-current' : 'bg-muted text-muted-foreground',
                    )}
                  >
                    {tab.badge}
                  </span>
                </Button>
              )
            })}
          </div>
        </CardContent>
      </Card>

      {activeWorkspaceTab === 'generate' && renderGeneratePanel()}
      {activeWorkspaceTab === 'profiles' && renderProfilesPanel()}
      {activeWorkspaceTab === 'outputs' && renderRecentVoiceoversPanel()}
    </div>
  )
}
