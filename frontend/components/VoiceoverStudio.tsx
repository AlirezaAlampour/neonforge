'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
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
import { formatDuration } from '@/lib/utils'
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
const VOX_MODEL_ID = 'voxcpm2'
const VOX_MODE_DESIGN = 'design'
const VOX_MODE_CLONE = 'clone'
const VOX_MODE_CONTINUATION = 'continuation'
const VOX_SINGLE_PASS_MAX_CHARS = 1200
const VOX_CONTINUATION_SINGLE_PASS_MAX_CHARS = 1800
const VOX_CHUNK_ESTIMATE_SIZE = 650

type VoxMode = 'design' | 'clone' | 'continuation'
type VoxContinuationReferenceSource = 'profile' | 'record'

interface TemporaryReferenceUploadResponse {
  temp_reference_id: string
  transcript: string
}

const VOX_MODE_OPTIONS: Array<{
  value: VoxMode
  label: string
  helper: string
}> = [
  {
    value: VOX_MODE_DESIGN,
    label: 'Voice Design',
    helper: 'No reference audio. Create a new voice from text and optional control guidance.',
  },
  {
    value: VOX_MODE_CLONE,
    label: 'Clone My Voice',
    helper: 'Use a saved reference clip. Optional control guidance can steer delivery and style.',
  },
  {
    value: VOX_MODE_CONTINUATION,
    label: 'Continue From Reference',
    helper: 'Advanced. Requires the exact transcript of the reference clip for prompt-style continuation.',
  },
]

function clampSpeed(value: number): number {
  if (!Number.isFinite(value)) return 1
  return Math.min(MAX_SPEED, Math.max(MIN_SPEED, value))
}

function isOutputFormat(value: unknown): value is 'wav' | 'mp3' {
  return value === 'wav' || value === 'mp3'
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
  const activeJobsRef = useRef<TrackedVoiceoverJob[]>([])
  const voxRecordedReferenceTokenRef = useRef(0)
  const voxProcessedRecordingRef = useRef<Blob | null>(null)
  const voxRecorder = useMediaRecorder()
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
  const activeJobsKey = useMemo(
    () => activeJobs.map((job) => job.jobId).sort().join(','),
    [activeJobs],
  )

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
      formData.append('audio_file', recordedBlob, `vox-continuation-reference-${Date.now()}.webm`)

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
  }, [deleteTempReference])

  useEffect(() => {
    if (!voxContinuationUsesRecordedReference || !voxRecorder.audioBlob) return
    if (voxRecorder.audioBlob === voxProcessedRecordingRef.current) return

    voxProcessedRecordingRef.current = voxRecorder.audioBlob
    void uploadRecordedReference(voxRecorder.audioBlob)
  }, [uploadRecordedReference, voxContinuationUsesRecordedReference, voxRecorder.audioBlob])

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

  const handleDeleteRecentVoiceover = async (item: RecentVoiceover) => {
    const confirmed = window.confirm(`Delete recent voiceover "${item.filename}"?`)
    if (!confirmed) return

    setDeletingOutputId(item.job_id)
    setRecentError(null)
    try {
      await apiRequest<{ deleted: boolean }>(`/api/v1/voiceover/output/${item.job_id}`, {
        method: 'DELETE',
      })
      setTrackedJobs((current) => current.filter((job) => job.jobId !== item.job_id))
      setRecentVoiceovers((current) => current.filter((voiceover) => voiceover.job_id !== item.job_id))
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
                      accept=".wav,.mp3,.m4a,audio/wav,audio/mpeg,audio/mp4,audio/x-m4a"
                      label="Drop a WAV, MP3, or M4A sample"
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
                  <p className="text-xs text-muted-foreground">WAV, MP3, and M4A are accepted. Clips longer than 30 seconds are rejected when duration tools are available.</p>
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
                  Save your first voice profile to reuse references later. Vox Voice Design and recorded continuation can still run without one.
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
                {requiresSavedVoiceProfile ? (
                  <>
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
                    <p className="text-xs text-muted-foreground">
                      {isVoxModel
                        ? 'Required for Clone My Voice, or for continuation when you choose Use Saved Voice Profile.'
                        : 'Choose the saved reference clip you want this runtime to use.'}
                    </p>
                  </>
                ) : isVoxContinuationMode ? (
                  <div className="rounded-xl bg-background/30 p-4 ring-1 ring-border/35">
                    <p className="text-sm font-medium">Recorded reference clips can drive this continuation run.</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Record a fresh clip below and Vox will use it directly without creating a permanent voice profile first.
                    </p>
                  </div>
                ) : (
                  <div className="rounded-xl bg-background/30 p-4 ring-1 ring-border/35">
                    <p className="text-sm font-medium">Voice Design skips saved reference audio.</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Vox will design a voice from text alone. Your saved profiles stay available if you switch back to Clone My Voice or Continue From Reference.
                    </p>
                  </div>
                )}
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

            {isVoxModel && (
              <div className="space-y-3 rounded-xl bg-background/30 p-4 ring-1 ring-border/35">
                <div>
                  <p className="text-sm font-semibold">Vox Mode</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Vox supports voice design, controllable cloning, and explicit transcript-guided continuation. Pick the mode that matches what you want it to do.
                  </p>
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
                            ? 'border-primary bg-primary/10 text-foreground'
                            : 'border-border/50 bg-background/40 text-muted-foreground hover:border-border hover:text-foreground'
                        }`}
                      >
                        <p className="text-sm font-semibold">{option.label}</p>
                        <p className="mt-1 text-xs leading-5">{option.helper}</p>
                      </button>
                    )
                  })}
                </div>
              </div>
            )}

            <div className="space-y-2 rounded-xl bg-background/30 p-4 ring-1 ring-border/35">
              <Label htmlFor="voiceover-script">Script</Label>
              <p className="text-xs text-muted-foreground">
                {isVoxContinuationMode
                  ? 'Continuation works best when this script naturally follows the reference clip.'
                  : 'Paste the voiceover script here. Longer scripts are chunked conservatively and stitched into one final file.'}
              </p>
              <Textarea
                id="voiceover-script"
                value={script}
                onChange={(event) => setScript(event.target.value)}
                placeholder="Paste the voiceover script here."
                rows={10}
                className="min-h-[220px]"
              />
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>{script.length} characters</span>
                <span>{chunkEstimateLabel}</span>
              </div>
            </div>

            {isVoxModel && !isVoxContinuationMode && (
              <div className="space-y-2 rounded-xl bg-background/30 p-4 ring-1 ring-border/35">
                <Label htmlFor="vox-style-text">Style / Control</Label>
                <p className="text-xs text-muted-foreground">
                  Optional. Use a short natural-language hint like “warm, intimate, slightly slower” to steer delivery without switching into continuation mode.
                </p>
                <Textarea
                  id="vox-style-text"
                  value={voxStyleText}
                  onChange={(event) => setVoxStyleText(event.target.value)}
                  placeholder="Warm, confident, slightly slower, with a soft smile"
                  rows={3}
                />
              </div>
            )}

            {isVoxContinuationMode && (
              <div className="space-y-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
                <div className="space-y-3">
                  <div>
                    <p className="text-sm font-semibold">Continuation Reference</p>
                    <p className="mt-1 text-xs text-amber-100/80">
                      Pick an existing saved profile, or record a fresh reference clip right here and let ASR pre-fill the transcript for you.
                    </p>
                  </div>
                  <div className="flex flex-wrap rounded-lg border border-amber-500/20 bg-background/25 p-1">
                    <button
                      type="button"
                      onClick={handleUseSavedVoiceProfile}
                      className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                        voxContinuationReferenceSource === 'profile'
                          ? 'bg-amber-500/15 text-foreground'
                          : 'text-amber-100/75 hover:text-foreground'
                      }`}
                    >
                      Use Saved Voice Profile
                    </button>
                    <button
                      type="button"
                      onClick={handleRecordReferenceNow}
                      className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                        voxContinuationReferenceSource === 'record'
                          ? 'bg-amber-500/15 text-foreground'
                          : 'text-amber-100/75 hover:text-foreground'
                      }`}
                    >
                      Record Reference Now
                    </button>
                  </div>
                </div>

                {voxContinuationUsesRecordedReference && (
                  <div className="space-y-3 rounded-xl bg-background/25 p-4 ring-1 ring-amber-500/15">
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
                        <Button type="button" variant="destructive" className="gap-2" onClick={voxRecorder.stopRecording}>
                          <Square className="h-3.5 w-3.5" />
                          Stop Recording
                        </Button>
                      )}

                      {voxRecorder.isRecording && (
                        <div className="flex items-center gap-2 text-sm text-amber-50/90">
                          <span className="h-2.5 w-2.5 rounded-full bg-red-500 animate-pulse" />
                          <span className="font-mono tabular-nums">{formatDuration(voxRecorder.duration)}</span>
                        </div>
                      )}

                      {(voxRecorder.audioUrl || voxRecordedReferenceId || voxRecordedReferencePending) && !voxRecorder.isRecording && (
                        <Button type="button" variant="ghost" className="gap-2" onClick={() => clearRecordedReference()}>
                          <Trash2 className="h-4 w-4" />
                          Discard / Re-record
                        </Button>
                      )}
                    </div>

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
                        Recorded reference ready. Vox will use this clip for continuation, and the transcript below is fully editable.
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
                  </div>
                )}

                <div className="space-y-2">
                  <Label htmlFor="vox-prompt-text">Reference Transcript</Label>
                  <p className="text-xs text-amber-100/80">
                    {voxContinuationUsesRecordedReference
                      ? 'ASR will auto-fill this after recording. You can edit the transcript before you generate.'
                      : 'Paste the exact transcript of the saved reference clip so Vox can continue from it instead of treating it like normal voice cloning.'}
                  </p>
                  <Textarea
                    id="vox-prompt-text"
                    value={voxPromptText}
                    onChange={(event) => setVoxPromptText(event.target.value)}
                    placeholder={voxContinuationUsesRecordedReference ? 'ASR will fill this after you stop recording.' : 'Paste the exact transcript of the saved reference clip.'}
                    rows={4}
                  />
                </div>
              </div>
            )}

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

            <div className="space-y-4 rounded-xl bg-background/35 p-4 ring-1 ring-border/40">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold">Job Activity</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Active jobs keep polling after a refresh on this browser. Finished outputs also appear below in Recent Voiceovers.
                  </p>
                </div>
                <p className="text-xs text-muted-foreground">
                  {activeJobs.length} active, {trackedJobs.length} tracked
                </p>
              </div>

              {trackedJobs.length > 0 ? (
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
                            <audio controls className="w-full" src={`${currentStatus.output_url}?v=${encodeURIComponent(currentStatus.created_at ?? job.createdAt)}`}>
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
                <div className="rounded-xl border border-dashed border-border/50 p-6 text-center text-sm text-muted-foreground">
                  Start a voiceover to track its progress here, even if you refresh the page.
                </div>
              )}
            </div>
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
