'use client'

import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  Download,
  FileText,
  History,
  Loader2,
  Mic2,
  MoreHorizontal,
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
  has_script_text?: boolean
  has_metadata?: boolean
  script_text_url?: string | null
  metadata_url?: string | null
  duration_seconds?: number | null
  reference_source_type?: string | null
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

const compactDateFormatter = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
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
    label: 'Design',
    shortLabel: 'Design',
    helper: 'No reference.',
  },
  {
    value: VOX_MODE_CLONE,
    label: 'Clone',
    shortLabel: 'Clone',
    helper: 'Saved profile.',
  },
  {
    value: VOX_MODE_CONTINUATION,
    label: 'Continue',
    shortLabel: 'Continue',
    helper: 'Reference transcript.',
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
  return profile.reference_transcript?.trim() || ''
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

function getJobStatusLabel(status: string | null | undefined): string {
  if (status === 'processing' || status === 'stitching') return 'running'
  if (status === 'pending') return 'queued'
  return status ?? 'restoring'
}

function getReferenceSourceLabel(source: string | null | undefined): string {
  if (source === 'saved_profile') return 'saved profile'
  if (source === 'temp_recording') return 'temp recording'
  if (source === 'upload') return 'upload'
  if (source === 'none') return 'no reference'
  return ''
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

function FlowStep({
  number,
  title,
  aside,
  children,
}: {
  number: number
  title: string
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="grid gap-4 border-t border-white/[0.06] pt-6 first:border-t-0 first:pt-0 sm:grid-cols-[44px,minmax(0,1fr)]">
      <div className="pt-0.5 font-mono text-[12px] font-medium tabular-nums text-muted-foreground">
        {String(number).padStart(2, '0')}
      </div>
      <div className="min-w-0 space-y-3">
        <div className="flex min-h-7 flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold tracking-tight text-foreground">{title}</h3>
          {aside}
        </div>
        {children}
      </div>
    </section>
  )
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
    <Card className={cn('overflow-hidden border-white/[0.06] bg-[#0f1218] shadow-sm', className)}>
      <div className="border-b border-white/[0.05] px-4 py-3">
        <div className="flex items-start gap-2.5">
          <div className="rounded-md bg-primary/10 p-1.5 text-primary">
            <History className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="text-sm font-semibold tracking-tight">Job Activity</p>
              </div>
              <p className="font-mono text-[11px] text-muted-foreground">
                {activeJobsCount} active
                {recentVoiceoversCount > 0 ? `, ${recentVoiceoversCount} outputs` : ''}
              </p>
            </div>
          </div>
        </div>
      </div>

      <CardContent className={cn('space-y-3 p-3 xl:max-h-[calc(100vh-10rem)] xl:overflow-y-auto')}>
        {hasTrackedJobs ? (
          <div className="space-y-3">
            <div className="space-y-2">
              {trackedJobs.map((job) => {
              const currentStatus = job.status
              const progressValue = getJobProgressValue(currentStatus)
              const jobFilename = getTrackedJobFilename(job)
              const isIndeterminate =
                !currentStatus || currentStatus.status === 'queued' || currentStatus.status === 'pending' || currentStatus.status === 'stitching'

              return (
                <div key={job.jobId} className="rounded-lg border border-white/[0.06] bg-primary/[0.06] p-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold">{job.modelLabel}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {job.profileName} · {dateFormatter.format(new Date(job.createdAt))}
                      </p>
                      <p className="mt-1 text-xs font-mono text-muted-foreground">{job.jobId}</p>
                      {jobFilename && <p className="mt-2 truncate text-xs text-muted-foreground">{jobFilename}</p>}
                    </div>
                    <div className="rounded-full border border-primary/20 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                      {getJobStatusLabel(currentStatus?.status)}
                    </div>
                  </div>

                  <div className="mt-3 space-y-2">
                    <Progress value={progressValue} indeterminate={isIndeterminate} className="h-1" />
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                      <span>
                        {currentStatus?.completed_chunks ?? 0} / {currentStatus?.total_chunks ?? 0} chunks complete
                      </span>
                      {currentStatus?.status === 'stitching' && <span>Finalizing</span>}
                      {!currentStatus && <span>Restoring job state...</span>}
                    </div>
                  </div>

                  {currentStatus?.status === 'failed' && currentStatus.error && (
                    <p className="mt-3 text-sm text-red-300">{currentStatus.error}</p>
                  )}

                  {currentStatus?.status === 'done' && currentStatus.output_url && (
                    <div className="mt-3 space-y-3">
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
            {latestRecentVoiceover && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">Recent</p>
                  <button
                    type="button"
                    className="text-[11px] text-muted-foreground transition-colors hover:text-foreground"
                    onClick={onOpenOutputs}
                  >
                    All
                  </button>
                </div>
                <div className="rounded-md border border-white/[0.06] bg-[#151823] p-3">
                  <p className="truncate text-sm font-medium">{latestRecentVoiceover.filename}</p>
                  <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                    {typeof latestRecentVoiceover.duration_seconds === 'number'
                      ? `${formatDuration(Math.round(latestRecentVoiceover.duration_seconds))} · `
                      : ''}
                    {dateFormatter.format(new Date(latestRecentVoiceover.created_at))}
                  </p>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-3 rounded-lg border border-dashed border-white/[0.08] p-4 text-center">
            <p className="text-sm font-medium text-foreground">No live jobs</p>

            {latestRecentVoiceover ? (
              <div className="rounded-md border border-white/[0.06] bg-[#151823] p-3 text-left">
                <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-primary/75">Latest</p>
                <p className="mt-2 truncate text-sm font-semibold">{latestRecentVoiceover.filename}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Saved {dateFormatter.format(new Date(latestRecentVoiceover.created_at))}
                </p>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">Renders will appear here.</p>
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
  const supportsSpeedControl = selectedModel?.model_id === 'f5tts'
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
      setVoxPromptText('')
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
    clearRecordedReference()
    setVoxContinuationReferenceSource('record')
    setVoxRecordedReferenceError(null)
  }

  const handleStartRecordedReference = async () => {
    clearRecordedReference()
    await voxRecorder.startRecording()
  }

  const handleGenerate = async () => {
    if (!canGenerate || !selectedModel?.available) return

    const requestedSpeed = supportsSpeedControl ? Number(clampSpeed(parseFloat(speedInput)).toFixed(2)) : 1
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

  const deleteRecentVoiceoverById = async (jobId: string, options?: { refresh?: boolean }) => {
    await apiRequest<{ deleted: boolean }>(`/api/v1/voiceover/output/${jobId}`, {
      method: 'DELETE',
    })
    setTrackedJobs((current) => current.filter((job) => job.jobId !== jobId))
    setRecentVoiceovers((current) => current.filter((voiceover) => voiceover.job_id !== jobId))
    if (options?.refresh !== false) {
      await refreshRecentVoiceovers()
    }
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
      selectedRecentVoiceovers.map((item) => deleteRecentVoiceoverById(item.job_id, { refresh: false })),
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
    await refreshRecentVoiceovers()
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
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold tracking-tight">Voice Profiles</p>
          <p className="mt-1 font-mono text-[11px] text-muted-foreground">
            {profilesLoading ? 'Loading...' : `${profiles.length} saved`}
          </p>
        </div>
        <Button
          type="button"
          variant={showProfileForm ? 'secondary' : 'outline'}
          size="sm"
          onClick={() => setShowProfileForm((current) => !current)}
        >
          {showProfileForm ? 'Close' : 'Add Profile'}
        </Button>
      </div>

        {showProfileForm && (
          <div className="space-y-4 rounded-lg border border-white/[0.06] bg-[#151823] p-4">
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
                <div className="flex w-fit flex-wrap rounded-lg border border-white/[0.06] bg-[#0f1218] p-1">
                  <button
                    type="button"
                    onClick={() => {
                      setProfileReferenceSource('upload')
                      setProfileError(null)
                    }}
                    className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                      profileReferenceSource === 'upload'
                        ? 'bg-[#1c1f2c] text-foreground'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    Upload
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setProfileReferenceSource('record')
                      setProfileError(null)
                    }}
                    className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                      profileReferenceSource === 'record'
                        ? 'bg-[#1c1f2c] text-foreground'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    Record
                  </button>
                </div>
                <p className="text-xs text-muted-foreground">Short, clean reference clips work best.</p>
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
                <div className="space-y-3 rounded-lg bg-background/60 p-4 ring-1 ring-border/55">
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
                      <p className="text-xs text-muted-foreground">Pick a mic when the default is wrong.</p>
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
                          <p className="text-sm font-semibold">Raw</p>
                          <p className="mt-1 text-xs leading-5">Best clone source.</p>
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
                          <p className="text-sm font-semibold">Enhanced</p>
                          <p className="mt-1 text-xs leading-5">Cleaner room sound.</p>
                        </button>
                      </div>
                      <p className="text-xs text-muted-foreground">Raw is most faithful; enhanced is cleaner.</p>
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
                        Re-record
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
                      Recording ready.
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
                className="border-white/[0.08] bg-[#0f1218]"
              />
              <p className="text-xs text-muted-foreground">Transcript is added when STT is available.</p>
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
                {uploadingProfile ? 'Uploading...' : 'Save'}
              </Button>
              <p className="text-xs text-muted-foreground">{profileReferenceSource === 'upload' ? 'WAV, MP3, M4A.' : 'Preview before saving.'}</p>
            </div>
          </div>
        )}

        {profileError && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            {profileError}
          </div>
        )}

        <div className="nf-divider-list">
          {profiles.map((profile) => {
            const isPreviewing = previewProfileId === profile.id && previewUrl
            const transcriptPreview = truncateText(profile.reference_transcript, 180)
            const notesPreview =
              profile.notes && profile.notes !== profile.reference_transcript ? truncateText(profile.notes, 140) : ''
            const rowDetail = notesPreview || transcriptPreview || 'No notes'

            return (
              <div key={profile.id} className="border-b border-white/10 last:border-b-0">
                <div className="grid grid-cols-[auto,minmax(0,max-content),auto,minmax(0,1fr),auto,auto] items-center gap-3 px-3 py-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-8 w-8 shrink-0 px-0"
                    title="Play"
                    onClick={() => handlePlayProfile(profile)}
                  >
                    <Play className="h-3.5 w-3.5" />
                  </Button>
                  <p className="min-w-0 truncate text-sm font-semibold">{profile.name}</p>
                  <span aria-hidden="true" className="block w-0 shrink-0" />
                  <p className="min-w-0 truncate text-sm text-muted-foreground">{rowDetail}</p>
                  <p className="shrink-0 text-[11px] text-muted-foreground">
                    {compactDateFormatter.format(new Date(profile.created_at))}
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-8 w-8 shrink-0 px-0"
                    title="Delete"
                    onClick={() => void handleDeleteProfile(profile)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>

                {isPreviewing && (
                  <div className="px-3 pb-2">
                    <audio key={previewUrl} controls autoPlay className="h-8 w-full" src={previewUrl}>
                      Your browser does not support audio playback.
                    </audio>
                  </div>
                )}
              </div>
            )
          })}

          {!profilesLoading && profiles.length === 0 && (
            <div className="rounded-xl border border-dashed border-white/[0.08] p-6 text-center text-sm text-muted-foreground">
              Save your first voice profile to reuse it later.
            </div>
          )}
        </div>
    </div>
  )

  const renderRecentVoiceoversPanel = () => (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold tracking-tight">Outputs</p>
          <p className="mt-1 font-mono text-[11px] text-muted-foreground">
            {recentLoading ? 'Loading...' : `${recentVoiceovers.length} saved`}
          </p>
        </div>
      </div>
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
          <p className="text-xs text-muted-foreground">Your browser may ask to allow multiple downloads.</p>
        )}

        {recentError && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            {recentError}
          </div>
        )}

        <div className="nf-divider-list">
          {recentVoiceovers.map((item) => {
            const isSelected = selectedOutputIds.includes(item.job_id)
            const durationLabel =
              typeof item.duration_seconds === 'number' ? formatDuration(Math.round(item.duration_seconds)) : null
            const detailParts = [
              getReferenceSourceLabel(item.reference_source_type),
              item.has_script_text ? 'script' : null,
              item.has_metadata ? 'meta' : null,
            ].filter(Boolean)
            const detailLabel = detailParts.length > 0 ? detailParts.join(' · ') : 'Local render'

            return (
              <div
                key={item.job_id}
                className={cn(
                  'border-b border-white/10 px-3 py-2 transition-colors last:border-b-0',
                  isSelected && 'bg-primary/[0.06]',
                )}
              >
                <div className="grid grid-cols-[auto,minmax(0,max-content),auto,minmax(0,1fr),auto,minmax(0,176px),auto] items-center gap-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center">
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded border-input bg-background accent-primary"
                      checked={isSelected}
                      onChange={() => toggleRecentVoiceoverSelection(item.job_id)}
                      disabled={bulkDeletingOutputs}
                    />
                  </div>
                  <p className="min-w-0 truncate text-sm font-semibold">{item.filename}</p>
                  {durationLabel ? (
                    <p className="shrink-0 text-[11px] text-muted-foreground">{durationLabel}</p>
                  ) : (
                    <span aria-hidden="true" className="block w-0 shrink-0" />
                  )}
                  <p className="min-w-0 truncate text-sm text-muted-foreground">{detailLabel}</p>
                  <p className="shrink-0 text-[11px] text-muted-foreground">
                    {compactDateFormatter.format(new Date(item.created_at))}
                  </p>
                  <audio
                    controls
                    className="h-8 min-w-0 w-full"
                    src={`${item.output_url}?v=${encodeURIComponent(item.created_at)}`}
                  >
                    Your browser does not support audio playback.
                  </audio>
                  <details className="relative shrink-0">
                    <summary
                      className="flex h-8 w-8 cursor-pointer list-none items-center justify-center rounded-md border border-input text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
                      title="Output actions"
                    >
                      <MoreHorizontal className="h-4 w-4" />
                    </summary>
                    <div className="absolute right-0 z-20 mt-2 w-44 overflow-hidden rounded-lg border border-white/[0.08] bg-[#151823] p-1 shadow-xl">
                      <a
                        href={item.output_url}
                        download
                        className="flex items-center gap-2 rounded-md px-3 py-2 text-xs text-foreground hover:bg-accent"
                      >
                        <Download className="h-3.5 w-3.5" />
                        Download Audio
                      </a>
                      {item.has_script_text && item.script_text_url && (
                        <a
                          href={item.script_text_url}
                          download
                          className="flex items-center gap-2 rounded-md px-3 py-2 text-xs text-foreground hover:bg-accent"
                        >
                          <FileText className="h-3.5 w-3.5" />
                          Download Text
                        </a>
                      )}
                      {item.has_metadata && item.metadata_url && (
                        <a
                          href={item.metadata_url}
                          download
                          className="flex items-center gap-2 rounded-md px-3 py-2 text-xs text-foreground hover:bg-accent"
                        >
                          <FileText className="h-3.5 w-3.5" />
                          Download Metadata
                        </a>
                      )}
                      <button
                        type="button"
                        className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-xs text-red-300 hover:bg-red-500/10"
                        onClick={() => void handleDeleteRecentVoiceover(item)}
                        disabled={deletingOutputId === item.job_id || bulkDeletingOutputs}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        {deletingOutputId === item.job_id ? 'Deleting...' : 'Delete'}
                      </button>
                    </div>
                  </details>
                </div>
              </div>
            )
          })}

          {!recentLoading && recentVoiceovers.length === 0 && (
            <div className="rounded-xl border border-dashed border-white/[0.08] p-6 text-center text-sm text-muted-foreground">
              Completed voiceovers will appear here after a render finishes.
            </div>
          )}
        </div>
    </div>
  )

  const renderGeneratePanel = () => {
    const latestRecentVoiceover = recentVoiceovers[0] ?? null
    const hasSelectedModel = !!selectedModel
    const showVoiceProfileSelection = hasSelectedModel && !isVoxContinuationMode && requiresSavedVoiceProfile
    const selectedProfileTranscript = getProfileTranscriptSeed(selectedProfile)
    const scriptLabel = isVoxContinuationMode ? 'New Script' : 'Script'
    const scriptPlaceholder = isVoxContinuationMode
      ? 'Write the next narration that should continue after the reference clip.'
      : 'Paste the voiceover script here.'

    const renderModelStep = () => (
      <FlowStep number={1} title="Model">
        <div className="grid gap-2 md:grid-cols-3">
          {models.map((model) => {
            const isActive = selectedModelId === model.model_id
            return (
              <button
                key={model.model_id}
                type="button"
                disabled={!model.available}
                aria-pressed={isActive}
                onClick={() => setSelectedModelId(model.model_id)}
                className={cn(
                  'min-w-0 rounded-lg border px-3 py-2.5 text-left transition-colors',
                  isActive
                    ? 'border-primary/70 bg-primary/10 shadow-[0_0_0_3px_rgba(61,123,255,0.12)]'
                    : 'border-white/[0.06] bg-[#0f1218] hover:border-white/[0.12] hover:bg-[#151823]',
                  !model.available && 'cursor-not-allowed opacity-45 hover:border-white/[0.06] hover:bg-[#0f1218]',
                )}
              >
                <div className="flex items-center gap-2">
                  {isActive && <span className="h-1.5 w-1.5 rounded-full bg-primary" />}
                  <span className="truncate text-sm font-semibold">{model.display_name}</span>
                </div>
                <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                  {model.available ? model.model_id : 'unavailable'}
                </p>
              </button>
            )
          })}
        </div>
        {modelsLoading ? (
          <p className="text-xs text-muted-foreground">Loading models...</p>
        ) : availableModels.length === 0 ? (
          <p className="text-xs text-muted-foreground">No runnable voice models are currently available.</p>
        ) : null}
      </FlowStep>
    )

    const renderProfilePicker = (options?: { compact?: boolean }) => (
      <div className="space-y-2">
        <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr),auto]">
          <select
            id="voice-profile-select"
            value={selectedProfileId}
            onChange={(event) => setSelectedProfileId(event.target.value)}
            className="nf-control w-full"
          >
            <option value="">Select a saved profile</option>
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
          {!options?.compact && (
            <Button type="button" variant="outline" size="sm" onClick={() => setActiveWorkspaceTab('profiles')}>
              Add Profile
            </Button>
          )}
        </div>
        {selectedProfile && (
          <p className="truncate text-xs text-muted-foreground">
            {selectedProfile.notes || selectedProfile.reference_transcript || 'Reference ready.'}
          </p>
        )}
      </div>
    )

    const renderContinuationReference = () => (
      <div className="space-y-3">
        <div className="inline-flex w-fit rounded-lg border border-white/[0.06] bg-[#0f1218] p-1">
          <button
            type="button"
            aria-pressed={voxContinuationReferenceSource === 'profile'}
            onClick={handleUseSavedVoiceProfile}
            className={cn(
              'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
              voxContinuationReferenceSource === 'profile' ? 'bg-[#1c1f2c] text-foreground' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            Saved Profile
          </button>
          <button
            type="button"
            aria-pressed={voxContinuationReferenceSource === 'record'}
            onClick={handleRecordReferenceNow}
            className={cn(
              'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
              voxContinuationReferenceSource === 'record' ? 'bg-[#1c1f2c] text-foreground' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            Record
          </button>
        </div>

        {voxContinuationReferenceSource === 'profile' ? (
          <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr),minmax(0,260px)]">
            {renderProfilePicker({ compact: true })}
            <div className="min-w-0 rounded-md border border-white/[0.06] bg-[#0f1218] px-3 py-2">
              <p className="truncate text-sm font-medium">
                {selectedProfile ? selectedProfile.name : 'Choose a saved profile'}
              </p>
              <p className="mt-1 truncate text-xs text-muted-foreground">
                {selectedProfileTranscript ? `Transcript: ${truncateText(selectedProfileTranscript, 96)}` : 'Transcript required below'}
              </p>
            </div>
          </div>
        ) : (
          <div className="rounded-md border border-white/[0.06] bg-[#0f1218] p-3">
            <div className="flex flex-wrap items-center gap-2">
              {!voxRecorder.isRecording ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="gap-2"
                  onClick={() => void handleStartRecordedReference()}
                >
                  <Mic2 className="h-3.5 w-3.5" />
                  Record
                </Button>
              ) : (
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  className="gap-2"
                  onClick={voxRecorder.stopRecording}
                >
                  <Square className="h-3.5 w-3.5" />
                  Stop
                </Button>
              )}

              {voxRecorder.isRecording && (
                <span className="font-mono text-xs text-foreground/90">{formatDuration(voxRecorder.duration)}</span>
              )}

              {(voxRecorder.audioUrl || voxRecordedReferenceId || voxRecordedReferencePending) && !voxRecorder.isRecording && (
                <Button type="button" variant="ghost" size="sm" className="gap-2" onClick={() => clearRecordedReference()}>
                  <Trash2 className="h-3.5 w-3.5" />
                  Re-record
                </Button>
              )}
            </div>

            {voxRecorder.audioUrl && !voxRecorder.isRecording && (
              <audio controls className="mt-2 h-8 w-full" src={voxRecorder.audioUrl}>
                Your browser does not support audio playback.
              </audio>
            )}
            {voxRecordedReferencePending && (
              <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Transcribing...
              </div>
            )}
            {voxRecordedReferenceId && !voxRecordedReferencePending && (
              <p className="mt-2 text-xs text-emerald-300">Recorded reference ready.</p>
            )}
            {voxRecordedReferenceError && <p className="mt-2 text-xs text-red-300">{voxRecordedReferenceError}</p>}
            {voxRecorder.error && <p className="mt-2 text-xs text-amber-300">{voxRecorder.error}</p>}
          </div>
        )}

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="vox-prompt-text">Reference Transcript</Label>
            {(voxContinuationUsesRecordedReference && voxRecordedReferenceId) ||
            (!voxContinuationUsesRecordedReference && selectedProfileTranscript) ? (
              <span className="rounded-full border border-white/[0.08] px-2 py-0.5 text-[11px] text-muted-foreground">
                {voxContinuationUsesRecordedReference ? 'STT filled' : 'Profile filled'}
              </span>
            ) : null}
          </div>
          <Textarea
            id="vox-prompt-text"
            value={voxPromptText}
            onChange={(event) => setVoxPromptText(event.target.value)}
            placeholder={
              voxContinuationUsesRecordedReference
                ? 'ASR will fill this after recording.'
                : 'Exact words spoken in the saved reference clip.'
            }
            rows={4}
            className="min-h-[92px] border-white/[0.08] bg-[#0f1218]"
          />
        </div>
      </div>
    )

    const renderModeReferenceStep = () => {
      if (!hasSelectedModel) return null

      if (!isVoxModel) {
        return (
          <FlowStep number={2} title="Reference">
            {renderProfilePicker()}
          </FlowStep>
        )
      }

      return (
        <FlowStep number={2} title="Mode / Reference">
          <div className="space-y-4">
            <div className="grid gap-2 md:grid-cols-3">
              {VOX_MODE_OPTIONS.map((option) => {
                const isActive = voxMode === option.value
                return (
                  <button
                    key={option.value}
                    type="button"
                    aria-pressed={isActive}
                    onClick={() => setVoxMode(option.value)}
                    className={cn(
                      'rounded-lg border px-3 py-2.5 text-left transition-colors',
                      isActive
                        ? 'border-primary/70 bg-primary/10 shadow-[0_0_0_3px_rgba(61,123,255,0.12)]'
                        : 'border-white/[0.06] bg-[#0f1218] hover:border-white/[0.12] hover:bg-[#151823]',
                    )}
                  >
                    <p className="text-sm font-semibold">{option.shortLabel}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{option.helper}</p>
                  </button>
                )
              })}
            </div>

            {isVoxDesignMode ? (
              <div className="rounded-md border border-white/[0.06] bg-[#0f1218] px-3 py-2 text-sm text-muted-foreground">
                No reference needed.
              </div>
            ) : isVoxContinuationMode ? (
              renderContinuationReference()
            ) : (
              renderProfilePicker()
            )}
          </div>
        </FlowStep>
      )
    }

    const renderScriptPanel = () => (
      <div className="space-y-2">
        <Textarea
          id="voiceover-script"
          value={script}
          onChange={(event) => setScript(event.target.value)}
          placeholder={scriptPlaceholder}
          rows={10}
          aria-label={scriptLabel}
          className="min-h-[170px] border-white/[0.08] bg-[#0f1218]"
        />
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>{script.length} characters</span>
          <span>{chunkEstimateLabel}</span>
        </div>
      </div>
    )

    return (
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr),300px] xl:grid-cols-[minmax(0,820px),300px]">
        <div className="space-y-6">
          {renderModelStep()}

          {!hasSelectedModel && (
            <div className="rounded-lg border border-dashed border-white/[0.08] bg-[#0a0c12] p-5 text-sm text-muted-foreground">
              Choose a TTS model to continue.
            </div>
          )}

          {renderModeReferenceStep()}

          {showVoiceProfileSelection && !profilesLoading && profiles.length === 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-dashed border-amber-500/30 bg-amber-500/10 p-3">
              <p className="text-sm font-semibold">Saved voice profile required</p>
              <Button type="button" variant="outline" size="sm" onClick={() => setActiveWorkspaceTab('profiles')}>
                Open Profiles
              </Button>
            </div>
          )}

          {hasSelectedModel && (
            <FlowStep
              number={3}
              title={scriptLabel}
              aside={<span className="font-mono text-[11px] text-muted-foreground">{script.length} chars</span>}
            >
              {renderScriptPanel()}
            </FlowStep>
          )}

          {hasSelectedModel && isVoxModel && !isVoxContinuationMode && (
            <details className="ml-0 rounded-lg border border-white/[0.06] bg-[#0f1218] p-3 sm:ml-[60px]" open={voxStyleText.trim().length > 0}>
              <summary className="cursor-pointer list-none text-sm font-semibold">Style / Control</summary>
              <Textarea
                id="vox-style-text"
                value={voxStyleText}
                onChange={(event) => setVoxStyleText(event.target.value)}
                placeholder="Warm, confident, slightly slower"
                rows={3}
                className="mt-3 border-white/[0.08] bg-[#0a0c12]"
              />
            </details>
          )}

          {hasSelectedModel && (
            <FlowStep number={4} title="Render">
              <div
                className={cn(
                  'grid gap-3 md:items-end',
                  supportsSpeedControl
                    ? 'md:grid-cols-[160px,minmax(0,1fr),auto]'
                    : 'md:grid-cols-[160px,auto]',
                )}
              >
                <div className="space-y-2">
                  <Label htmlFor="voiceover-output-format">Format</Label>
                  <select
                    id="voiceover-output-format"
                    value={outputFormat}
                    onChange={(event) => setOutputFormat(event.target.value === 'mp3' ? 'mp3' : 'wav')}
                    className="nf-control w-full"
                  >
                    <option value="wav">wav</option>
                    <option value="mp3">mp3</option>
                  </select>
                </div>

                {supportsSpeedControl && (
                  <details className="rounded-lg border border-white/[0.06] bg-[#0f1218] p-3">
                    <summary className="cursor-pointer list-none text-sm font-semibold">Speed</summary>
                    <div className="mt-3 space-y-3">
                      <div className="flex items-center justify-between gap-3">
                        <Label htmlFor="voiceover-speed">Value</Label>
                        <Input
                          id="voiceover-speed"
                          type="number"
                          min={MIN_SPEED}
                          max={MAX_SPEED}
                          step={SPEED_STEP}
                          value={speedInput}
                          onChange={(event) => setSpeedInput(event.target.value)}
                          onBlur={() => updateSpeed(parseFloat(speedInput))}
                          className="w-24 border-white/[0.08] bg-[#0a0c12] font-mono text-sm"
                        />
                      </div>
                      <Slider
                        value={speed}
                        onChange={(value) => updateSpeed(value)}
                        min={MIN_SPEED}
                        max={MAX_SPEED}
                        step={SPEED_STEP}
                      />
                    </div>
                  </details>
                )}

                <Button
                  type="button"
                  size="lg"
                  className="w-full gap-2 px-8 font-semibold md:min-w-[168px]"
                  disabled={!canGenerate}
                  onClick={handleGenerate}
                >
                  {submittingJob ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
                  {submittingJob ? 'Queueing...' : activeJobs.length > 0 ? 'Add to Queue' : 'Render'}
                </Button>
              </div>
            </FlowStep>
          )}

          {generationError && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {generationError}
            </div>
          )}
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
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
          <Mic2 className="h-5 w-5 text-primary" />
          Voiceover Studio
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">Generate, manage profiles, and review local renders.</p>
      </div>

      <div className="border-b border-white/[0.06]">
          <div className="flex flex-wrap gap-5">
            {workspaceTabs.map((tab) => {
              const Icon = tab.icon
              const active = activeWorkspaceTab === tab.id
              return (
                <button
                  key={tab.id}
                  type="button"
                  className={cn(
                    'flex items-center gap-2 border-b-2 px-0 pb-3 text-sm font-medium transition-colors',
                    active
                      ? 'border-primary text-foreground'
                      : 'border-transparent text-muted-foreground hover:text-foreground',
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
                </button>
              )
            })}
          </div>
      </div>

      {activeWorkspaceTab === 'generate' && renderGeneratePanel()}
      {activeWorkspaceTab === 'profiles' && renderProfilesPanel()}
      {activeWorkspaceTab === 'outputs' && renderRecentVoiceoversPanel()}
    </div>
  )
}
