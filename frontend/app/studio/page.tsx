'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Boxes,
  Clapperboard,
  Mic,
  RefreshCw,
  Save,
  Send,
  Sparkles,
  Trash2,
  UploadCloud,
  Video,
} from 'lucide-react'
import { FileDropzone } from '@/components/file-dropzone'
import { HistoryPane } from '@/components/history-pane'
import { JobTracker } from '@/components/job-tracker'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { Textarea } from '@/components/ui/textarea'
import { useJobPoller } from '@/hooks/use-job-poller'
import {
  deleteComfyUIAsset,
  deletePreset,
  fetchComfyUIAssets,
  fetchComfyUIModels,
  fetchComfyUITemplates,
  fetchLoraAssets,
  fetchPresets,
  fetchTTSProviders,
  fetchVoiceAssets,
  savePreset,
  submitComfyUIJob,
  submitLivePortrait,
  submitReactor,
  submitTTSJob,
  uploadComfyUIAsset,
} from '@/lib/api'
import { useStudioStore } from '@/lib/stores/studio-store'
import { formatBytes } from '@/lib/utils'
import type {
  AssetItem,
  ComfyUIAsset,
  ComfyUIModelsResponse,
  ComfyUITemplate,
  PresetProfile,
  StudioTool,
  TTSProvider,
} from '@/lib/types'

const tabs: Array<{ id: StudioTool; label: string; icon: typeof Mic }> = [
  { id: 'character-swap', label: 'Character Swap', icon: Boxes },
  { id: 'f5tts', label: 'TTS', icon: Mic },
  { id: 'liveportrait', label: 'LivePortrait', icon: Video },
  { id: 'reactor', label: 'ReActor', icon: Clapperboard },
]

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

export default function StudioPage() {
  const { jobs, trackJob, dismissJob } = useJobPoller()
  const [voiceAssets, setVoiceAssets] = useState<AssetItem[]>([])
  const [loraAssets, setLoraAssets] = useState<AssetItem[]>([])
  const [presets, setPresets] = useState<PresetProfile[]>([])
  const [selectedPresetId, setSelectedPresetId] = useState('')
  const [presetName, setPresetName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [f5RefAudioFile, setF5RefAudioFile] = useState<File | null>(null)
  const [ttsContinuationFile, setTTSContinuationFile] = useState<File | null>(null)
  const [liveSourceFile, setLiveSourceFile] = useState<File | null>(null)
  const [liveDrivingFile, setLiveDrivingFile] = useState<File | null>(null)
  const [referenceUploadFile, setReferenceUploadFile] = useState<File | null>(null)
  const [drivingUploadFile, setDrivingUploadFile] = useState<File | null>(null)
  const [ttsProviders, setTTSProviders] = useState<TTSProvider[]>([])
  const [comfyTemplates, setComfyTemplates] = useState<ComfyUITemplate[]>([])
  const [comfyAssets, setComfyAssets] = useState<ComfyUIAsset[]>([])
  const [comfyModels, setComfyModels] = useState<ComfyUIModelsResponse | null>(null)
  const [comfyAssetsLoaded, setComfyAssetsLoaded] = useState(false)
  const [refreshingComfy, setRefreshingComfy] = useState(false)
  const [uploadingAssetKind, setUploadingAssetKind] = useState<'image' | 'video' | null>(null)
  const [deletingComfyAssetId, setDeletingComfyAssetId] = useState<string | null>(null)

  const activeTab = useStudioStore((state) => state.activeTab)
  const setActiveTab = useStudioStore((state) => state.setActiveTab)
  const f5tts = useStudioStore((state) => state.f5tts)
  const liveportrait = useStudioStore((state) => state.liveportrait)
  const reactor = useStudioStore((state) => state.reactor)
  const characterSwap = useStudioStore((state) => state.characterSwap)
  const updateF5TTS = useStudioStore((state) => state.updateF5TTS)
  const updateLivePortrait = useStudioStore((state) => state.updateLivePortrait)
  const updateReactor = useStudioStore((state) => state.updateReactor)
  const updateCharacterSwap = useStudioStore((state) => state.updateCharacterSwap)
  const getPresetState = useStudioStore((state) => state.getPresetState)
  const applyPresetState = useStudioStore((state) => state.applyPresetState)

  const refreshAssets = useCallback(async () => {
    try {
      const [voices, loras] = await Promise.all([fetchVoiceAssets(), fetchLoraAssets()])
      setVoiceAssets(voices.items)
      setLoraAssets(loras.items)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load saved assets')
    }
  }, [])

  const refreshTTSProviders = useCallback(async () => {
    try {
      const response = await fetchTTSProviders()
      setTTSProviders(response.items)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load TTS providers')
    }
  }, [])

  const refreshComfyData = useCallback(async () => {
    const [templatesResult, assetsResult, modelsResult] = await Promise.allSettled([
      fetchComfyUITemplates(),
      fetchComfyUIAssets(),
      fetchComfyUIModels(),
    ])

    const failures: string[] = []

    if (templatesResult.status === 'fulfilled') {
      setComfyTemplates(templatesResult.value.items)
    } else {
      failures.push(
        templatesResult.reason instanceof Error
          ? templatesResult.reason.message
          : 'Failed to load ComfyUI templates',
      )
    }

    if (assetsResult.status === 'fulfilled') {
      setComfyAssets(assetsResult.value.items)
      setComfyAssetsLoaded(true)
    } else {
      setComfyAssets([])
      setComfyAssetsLoaded(true)
      failures.push(
        assetsResult.reason instanceof Error
          ? assetsResult.reason.message
          : 'Failed to load ComfyUI assets',
      )
    }

    if (modelsResult.status === 'fulfilled') {
      setComfyModels(modelsResult.value)
    } else {
      failures.push(
        modelsResult.reason instanceof Error
          ? modelsResult.reason.message
          : 'Failed to load ComfyUI model inventory',
      )
    }

    if (failures.length > 0) {
      setError(failures[0])
    }
  }, [])

  const handleRefreshComfy = useCallback(async () => {
    setRefreshingComfy(true)
    setError(null)
    try {
      await refreshComfyData()
    } finally {
      setRefreshingComfy(false)
    }
  }, [refreshComfyData])

  const refreshPresets = useCallback(async () => {
    try {
      const response = await fetchPresets(activeTab)
      setPresets(response.items)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load presets')
    }
  }, [activeTab])

  useEffect(() => {
    refreshAssets()
    refreshTTSProviders()
    refreshComfyData()
  }, [refreshAssets, refreshComfyData, refreshTTSProviders])

  const enabledTTSProviders = useMemo(
    () => ttsProviders.filter((provider) => provider.enabled),
    [ttsProviders],
  )

  const selectedTTSProvider = useMemo(
    () => enabledTTSProviders.find((provider) => provider.provider_id === f5tts.provider) ?? enabledTTSProviders[0] ?? null,
    [enabledTTSProviders, f5tts.provider],
  )

  useEffect(() => {
    setSelectedPresetId('')
    refreshPresets()
  }, [activeTab, refreshPresets])

  useEffect(() => {
    if (!characterSwap.templateId && comfyTemplates.length > 0) {
      updateCharacterSwap({ templateId: comfyTemplates[0].id })
    }
  }, [characterSwap.templateId, comfyTemplates, updateCharacterSwap])

  useEffect(() => {
    if (!f5tts.provider && enabledTTSProviders.length > 0) {
      updateF5TTS({ provider: enabledTTSProviders[0].provider_id })
    }
  }, [enabledTTSProviders, f5tts.provider, updateF5TTS])

  useEffect(() => {
    if (!selectedTTSProvider) return

    updateF5TTS({
      provider: selectedTTSProvider.provider_id,
      outputFormat:
        f5tts.outputFormat && selectedTTSProvider.supported_output_formats.includes(f5tts.outputFormat)
          ? f5tts.outputFormat
          : selectedTTSProvider.default_output_format || selectedTTSProvider.supported_output_formats[0] || '',
      targetSampleRate:
        f5tts.targetSampleRate &&
        selectedTTSProvider.supported_target_sample_rates.includes(Number(f5tts.targetSampleRate))
          ? f5tts.targetSampleRate
          : selectedTTSProvider.default_target_sample_rate
            ? String(selectedTTSProvider.default_target_sample_rate)
            : '',
      options: selectedTTSProvider.option_fields.reduce<Record<string, unknown>>((acc, field) => {
        acc[field.id] = f5tts.options[field.id] ?? field.default ?? ''
        return acc
      }, {}),
      voiceMode: selectedTTSProvider.capabilities.supports_reference_audio ? f5tts.voiceMode : 'none',
      referenceText: selectedTTSProvider.capabilities.supports_reference_audio ? f5tts.referenceText : '',
      transcript: selectedTTSProvider.capabilities.transcript_guided_continuation ? f5tts.transcript : '',
      stylePrompt: selectedTTSProvider.capabilities.style_prompt ? f5tts.stylePrompt : '',
    })

    if (!selectedTTSProvider.capabilities.supports_reference_audio) {
      setF5RefAudioFile(null)
    }
    if (!selectedTTSProvider.capabilities.continuation_edit) {
      setTTSContinuationFile(null)
    }
  }, [selectedTTSProvider?.provider_id])

  const selectedPreset = useMemo(
    () => presets.find((preset) => preset.id === selectedPresetId) ?? null,
    [presets, selectedPresetId],
  )

  const selectedTemplate = useMemo(
    () => comfyTemplates.find((template) => template.id === characterSwap.templateId) ?? null,
    [characterSwap.templateId, comfyTemplates],
  )

  const imageAssets = useMemo(
    () => comfyAssets.filter((asset) => asset.kind === 'image'),
    [comfyAssets],
  )

  const videoAssets = useMemo(
    () => comfyAssets.filter((asset) => asset.kind === 'video'),
    [comfyAssets],
  )

  const selectedReferenceAsset = useMemo(
    () => imageAssets.find((asset) => asset.id === characterSwap.referenceImageAssetId) ?? null,
    [characterSwap.referenceImageAssetId, imageAssets],
  )

  const selectedDrivingAsset = useMemo(
    () => videoAssets.find((asset) => asset.id === characterSwap.drivingVideoAssetId) ?? null,
    [characterSwap.drivingVideoAssetId, videoAssets],
  )

  const selectedTemplateValidation = selectedTemplate?.validation ?? null
  const hasBlockingCharacterSwapValidation = (selectedTemplateValidation?.missing.length ?? 0) > 0
  const canSubmitCharacterSwap =
    !!selectedTemplate &&
    !!selectedReferenceAsset &&
    !!selectedDrivingAsset &&
    comfyAssetsLoaded &&
    !submitting &&
    !hasBlockingCharacterSwapValidation

  useEffect(() => {
    if (!comfyAssetsLoaded) return

    const patch: Partial<typeof characterSwap> = {}
    if (characterSwap.referenceImageAssetId && !selectedReferenceAsset) {
      patch.referenceImageAssetId = ''
    }
    if (characterSwap.drivingVideoAssetId && !selectedDrivingAsset) {
      patch.drivingVideoAssetId = ''
    }
    if (Object.keys(patch).length > 0) {
      updateCharacterSwap(patch)
    }
  }, [
    characterSwap.drivingVideoAssetId,
    characterSwap.referenceImageAssetId,
    comfyAssetsLoaded,
    selectedDrivingAsset,
    selectedReferenceAsset,
    updateCharacterSwap,
  ])

  const handleSavePreset = useCallback(async () => {
    if (!presetName.trim()) return
    setError(null)
    try {
      const saved = await savePreset({
        name: presetName.trim(),
        tool: activeTab,
        state: getPresetState(activeTab),
      })
      setPresetName('')
      await refreshPresets()
      setSelectedPresetId(saved.id)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to save preset')
    }
  }, [activeTab, getPresetState, presetName, refreshPresets])

  const handleLoadPreset = useCallback(() => {
    if (!selectedPreset) return
    applyPresetState(activeTab, selectedPreset.state)
  }, [activeTab, applyPresetState, selectedPreset])

  const handleDeletePreset = useCallback(async () => {
    if (!selectedPreset) return
    setError(null)
    try {
      await deletePreset(selectedPreset.id)
      setSelectedPresetId('')
      await refreshPresets()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to delete preset')
    }
  }, [refreshPresets, selectedPreset])

  const handleF5Submit = useCallback(async () => {
    if (!selectedTTSProvider || !f5tts.text.trim()) return

    setSubmitting(true)
    setError(null)
    try {
      const formData = new FormData()
      formData.append('provider', selectedTTSProvider.provider_id)
      formData.append('text', f5tts.text.trim())
      if (f5tts.speakerName.trim()) formData.append('speaker_name', f5tts.speakerName.trim())
      if (f5tts.referenceText.trim()) formData.append('reference_text', f5tts.referenceText.trim())
      if (f5tts.transcript.trim()) formData.append('transcript', f5tts.transcript.trim())
      if (f5tts.stylePrompt.trim()) formData.append('style_prompt', f5tts.stylePrompt.trim())
      if (f5tts.outputFormat) formData.append('output_format', f5tts.outputFormat)
      if (f5tts.targetSampleRate) formData.append('target_sample_rate', f5tts.targetSampleRate)

      const normalizedOptions: Record<string, unknown> = {}
      for (const field of selectedTTSProvider.option_fields) {
        const value = f5tts.options[field.id]
        if (value === '' || value === null || value === undefined) continue
        normalizedOptions[field.id] = normalizeOptionValue(selectedTTSProvider, field.id, value)
      }
      if (Object.keys(normalizedOptions).length > 0) {
        formData.append('options', JSON.stringify(normalizedOptions))
      }

      if (selectedTTSProvider.capabilities.supports_reference_audio) {
        if (f5tts.voiceMode === 'saved' && f5tts.savedVoicePath) {
          formData.append('reference_audio_path', f5tts.savedVoicePath)
        } else if (f5tts.voiceMode === 'upload') {
          if (!f5RefAudioFile) {
            setError('Upload mode is selected but no reference audio file is attached.')
            return
          }
          formData.append('reference_audio', f5RefAudioFile)
        }
      }

      if (selectedTTSProvider.capabilities.continuation_edit && ttsContinuationFile) {
        formData.append('continuation_audio', ttsContinuationFile)
      }

      const result = await submitTTSJob(formData)
      trackJob(result.job_id, result.provider ?? selectedTTSProvider.provider_id)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'TTS request failed')
    } finally {
      setSubmitting(false)
    }
  }, [f5RefAudioFile, f5tts, selectedTTSProvider, ttsContinuationFile, trackJob])

  const handleLivePortraitSubmit = useCallback(async () => {
    if (!liveSourceFile || !liveDrivingFile) return

    setSubmitting(true)
    setError(null)
    try {
      const formData = new FormData()
      formData.append('source_image', liveSourceFile)
      formData.append('driving_video', liveDrivingFile)
      const result = await submitLivePortrait(formData)
      trackJob(result.job_id, 'liveportrait')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'LivePortrait request failed')
    } finally {
      setSubmitting(false)
    }
  }, [liveDrivingFile, liveSourceFile, trackJob])

  const handleReactorSubmit = useCallback(async () => {
    if (!reactor.prompt.trim()) return

    setSubmitting(true)
    setError(null)
    try {
      const result = await submitReactor({
        prompt: reactor.prompt.trim(),
        negative_prompt: reactor.negativePrompt.trim(),
        lora_path: reactor.savedLoraPath || undefined,
        lora_strength: reactor.loraStrength,
      })
      trackJob(result.job_id, 'reactor')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'ReActor request failed')
    } finally {
      setSubmitting(false)
    }
  }, [reactor, trackJob])

  const handleUploadCharacterSwapAsset = useCallback(
    async (kind: 'image' | 'video') => {
      const file = kind === 'image' ? referenceUploadFile : drivingUploadFile
      if (!file) return

      setUploadingAssetKind(kind)
      setError(null)
      try {
        const uploaded = await uploadComfyUIAsset(file, kind)
        await refreshComfyData()
        if (kind === 'image') {
          setReferenceUploadFile(null)
          updateCharacterSwap({ referenceImageAssetId: uploaded.id })
        } else {
          setDrivingUploadFile(null)
          updateCharacterSwap({ drivingVideoAssetId: uploaded.id })
        }
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Asset upload failed')
      } finally {
        setUploadingAssetKind(null)
      }
    },
    [drivingUploadFile, referenceUploadFile, refreshComfyData, updateCharacterSwap],
  )

  const handleDeleteCharacterSwapAsset = useCallback(
    async (assetId: string) => {
      setDeletingComfyAssetId(assetId)
      setError(null)
      try {
        await deleteComfyUIAsset(assetId)
        if (characterSwap.referenceImageAssetId === assetId) {
          updateCharacterSwap({ referenceImageAssetId: '' })
        }
        if (characterSwap.drivingVideoAssetId === assetId) {
          updateCharacterSwap({ drivingVideoAssetId: '' })
        }
        await refreshComfyData()
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to delete uploaded asset')
      } finally {
        setDeletingComfyAssetId(null)
      }
    },
    [characterSwap.drivingVideoAssetId, characterSwap.referenceImageAssetId, refreshComfyData, updateCharacterSwap],
  )

  const handleCharacterSwapSubmit = useCallback(async () => {
    if (!selectedTemplate) return
    if (!selectedReferenceAsset || !selectedDrivingAsset) {
      setError('Select uploaded assets from the current Character Swap asset list before queueing the job.')
      return
    }
    if (hasBlockingCharacterSwapValidation) {
      setError('Resolve missing-model validation errors before queueing this Character Swap job.')
      return
    }

    setSubmitting(true)
    setError(null)
    try {
      const params: Record<string, unknown> = {
        steps: characterSwap.steps,
        cfg: characterSwap.cfg,
        denoise_strength: characterSwap.denoiseStrength,
        frame_rate: characterSwap.frameRate,
        person_index: characterSwap.personIndex,
      }
      if (characterSwap.seed.trim()) {
        params.seed = Number(characterSwap.seed)
      }
      if (characterSwap.subjectPointsJson.trim()) {
        params.subject_points_json = characterSwap.subjectPointsJson.trim()
      }
      if (characterSwap.negativePointsJson.trim()) {
        params.negative_points_json = characterSwap.negativePointsJson.trim()
      }

      const result = await submitComfyUIJob({
        template_id: selectedTemplate.id,
        inputs: {
          reference_image: selectedReferenceAsset.id,
          driving_video: selectedDrivingAsset.id,
        },
        params,
        debug_dump: characterSwap.debugDump,
      })
      if (characterSwap.debugDump) {
        updateCharacterSwap({ debugDump: false })
      }
      trackJob(result.job_id, 'comfyui')
      await refreshComfyData()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Character Swap request failed')
    } finally {
      setSubmitting(false)
    }
  }, [
    characterSwap,
    hasBlockingCharacterSwapValidation,
    refreshComfyData,
    selectedDrivingAsset,
    selectedReferenceAsset,
    selectedTemplate,
    trackJob,
    updateCharacterSwap,
  ])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Creative Studio</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Persistent tab state, reusable assets, history gallery, one-click presets, and ComfyUI workflow templates.
        </p>
      </div>

      <Card>
        <CardContent className="flex flex-wrap gap-2 p-4">
          {tabs.map((tab) => {
            const Icon = tab.icon
            const active = activeTab === tab.id
            return (
              <Button
                key={tab.id}
                type="button"
                variant={active ? 'default' : 'outline'}
                className="gap-2"
                onClick={() => setActiveTab(tab.id)}
              >
                <Icon className="h-4 w-4" />
                {tab.label}
              </Button>
            )
          })}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Preset Profiles</CardTitle>
          <CardDescription>Save the current tab state and restore it with one click.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Input
              value={presetName}
              onChange={(e) => setPresetName(e.target.value)}
              placeholder='Preset name (e.g., "Swap - Cinematic Pass")'
              className="min-w-[260px] flex-1"
            />
            <Button type="button" onClick={handleSavePreset} className="gap-2">
              <Save className="h-4 w-4" />
              Save Preset
            </Button>
          </div>

          <div className="flex flex-wrap gap-2">
            <select
              value={selectedPresetId}
              onChange={(e) => setSelectedPresetId(e.target.value)}
              className="h-10 min-w-[260px] rounded-md border border-input bg-background px-3 text-sm"
            >
              <option value="">Select preset for this tab</option>
              {presets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.name}
                </option>
              ))}
            </select>
            <Button type="button" variant="outline" onClick={handleLoadPreset} disabled={!selectedPresetId}>
              Load
            </Button>
            <Button type="button" variant="outline" onClick={handleDeletePreset} disabled={!selectedPresetId}>
              Delete
            </Button>
          </div>
        </CardContent>
      </Card>

      {error && (
        <div className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr,380px]">
        <div className="space-y-6">
          {activeTab === 'character-swap' && (
            <>
              <Card>
                <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                  <div>
                    <CardTitle className="flex items-center gap-2 text-base">
                      <Boxes className="h-4 w-4 text-primary" />
                      Character Swap Templates
                    </CardTitle>
                    <CardDescription>
                      Choose a managed ComfyUI workflow template, validate required models, and queue a real tracked job.
                    </CardDescription>
                  </div>
                  <Button type="button" variant="outline" size="sm" className="gap-2" onClick={handleRefreshComfy}>
                    <RefreshCw className={`h-3.5 w-3.5 ${refreshingComfy ? 'animate-spin' : ''}`} />
                    Refresh
                  </Button>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid gap-3">
                    {comfyTemplates.map((template) => {
                      const active = template.id === characterSwap.templateId
                      return (
                        <button
                          key={template.id}
                          type="button"
                          onClick={() => updateCharacterSwap({ templateId: template.id })}
                          className={`rounded-xl border p-4 text-left transition-all ${
                            active
                              ? 'border-primary bg-primary/5 ring-1 ring-primary/20'
                              : 'border-border/50 bg-background/40 hover:border-primary/40'
                          }`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <p className="text-sm font-semibold">{template.name}</p>
                              <p className="mt-1 text-xs text-muted-foreground">{template.description}</p>
                            </div>
                            <span className="rounded-full border border-border/60 px-2 py-0.5 text-[11px] text-muted-foreground">
                              {template.category}
                            </span>
                          </div>
                          <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                            <span className="rounded-full bg-secondary px-2 py-0.5">
                              Available {template.validation.available.length}
                            </span>
                            <span className="rounded-full bg-secondary px-2 py-0.5">
                              Missing {template.validation.missing.length}
                            </span>
                            <span className="rounded-full bg-secondary px-2 py-0.5">
                              {template.gpu_tier} GPU tier
                            </span>
                          </div>
                        </button>
                      )
                    })}
                  </div>

                  {selectedTemplate && (
                    <div className="rounded-xl border border-border/50 bg-background/40 p-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-semibold">{selectedTemplate.name}</p>
                        <span className="rounded-full bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                          {selectedTemplate.workflow_format.toUpperCase()} template
                        </span>
                      </div>
                      <p className="mt-2 text-sm text-muted-foreground">{selectedTemplate.description}</p>

                      {selectedTemplateValidation && (
                        <div className="mt-4 space-y-3">
                          {selectedTemplateValidation.missing.length > 0 && (
                            <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3">
                              <p className="flex items-center gap-2 text-sm font-medium text-red-200">
                                <AlertTriangle className="h-4 w-4" />
                                Missing Models
                              </p>
                              <div className="mt-2 flex flex-wrap gap-2">
                                {selectedTemplateValidation.missing.map((item) => (
                                  <span
                                    key={`${item.node_id}-${item.filename}`}
                                    className="rounded-full border border-red-400/30 px-2 py-0.5 text-xs text-red-100"
                                  >
                                    {item.filename}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}

                          {selectedTemplateValidation.warnings.length > 0 && (
                            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
                              <p className="text-sm font-medium text-amber-200">Validation Warnings</p>
                              <div className="mt-2 space-y-1 text-xs text-amber-100/90">
                                {selectedTemplateValidation.warnings.map((warning) => (
                                  <p key={warning}>{warning}</p>
                                ))}
                              </div>
                            </div>
                          )}

                          <p className="text-xs text-muted-foreground">
                            Read-only model scan: {comfyModels?.items.length ?? 0} discovered files across{' '}
                            {comfyModels?.roots.length ?? 0} mounted model root(s).
                          </p>
                          {comfyModels?.scanned_roots?.length ? (
                            <div className="space-y-1 text-[11px] font-mono text-muted-foreground">
                              {comfyModels.scanned_roots.map((root) => (
                                <p key={root.path}>
                                  {root.source === 'comfyui_container' ? '[ai-comfyui] ' : ''}
                                  {root.path}
                                  {root.resolved_path !== root.path ? ` -> ${root.resolved_path}` : ''}
                                  {' · '}
                                  {root.exists && root.is_dir
                                    ? `${root.item_count} file(s)`
                                    : root.error || 'missing or unreadable'}
                                </p>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Template Inputs</CardTitle>
                  <CardDescription>
                    Upload files once into NeonForge, pick them as template inputs, then queue repeatable jobs.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  <div className="grid gap-6 xl:grid-cols-2">
                    <div className="space-y-3 rounded-xl border border-border/50 bg-background/40 p-4">
                      <div>
                        <Label>Reference Image</Label>
                        <p className="mt-1 text-xs text-muted-foreground">
                          Upload or select the still image to patch into template node `57.inputs.image`.
                        </p>
                      </div>
                      <select
                        value={characterSwap.referenceImageAssetId}
                        onChange={(e) => updateCharacterSwap({ referenceImageAssetId: e.target.value })}
                        className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                      >
                        <option value="">Select uploaded image</option>
                        {imageAssets.map((asset) => (
                          <option key={asset.id} value={asset.id}>
                            {asset.original_filename}
                          </option>
                        ))}
                      </select>
                      <p className="text-xs text-muted-foreground">
                        {selectedReferenceAsset
                          ? `Selected uploaded asset: ${selectedReferenceAsset.original_filename}`
                          : comfyAssetsLoaded
                            ? 'No uploaded backend image asset selected.'
                            : 'Loading uploaded backend image assets...'}
                      </p>
                      <FileDropzone
                        accept="image/*"
                        label="Drop reference image"
                        hint={referenceUploadFile?.name || 'PNG, JPG, WebP up to 20 MB'}
                        file={referenceUploadFile}
                        onFileChange={setReferenceUploadFile}
                        maxSizeMB={20}
                        icon="image"
                      />
                      <Button
                        type="button"
                        variant="outline"
                        className="gap-2"
                        disabled={!referenceUploadFile || uploadingAssetKind === 'image'}
                        onClick={() => void handleUploadCharacterSwapAsset('image')}
                      >
                        <UploadCloud className="h-4 w-4" />
                        {uploadingAssetKind === 'image' ? 'Uploading...' : 'Upload Image'}
                      </Button>
                    </div>

                    <div className="space-y-3 rounded-xl border border-border/50 bg-background/40 p-4">
                      <div>
                        <Label>Driving Video</Label>
                        <p className="mt-1 text-xs text-muted-foreground">
                          Upload or select the source clip to patch into template node `63.inputs.video`.
                        </p>
                      </div>
                      <select
                        value={characterSwap.drivingVideoAssetId}
                        onChange={(e) => updateCharacterSwap({ drivingVideoAssetId: e.target.value })}
                        className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                      >
                        <option value="">Select uploaded video</option>
                        {videoAssets.map((asset) => (
                          <option key={asset.id} value={asset.id}>
                            {asset.original_filename}
                          </option>
                        ))}
                      </select>
                      <p className="text-xs text-muted-foreground">
                        {selectedDrivingAsset
                          ? `Selected uploaded asset: ${selectedDrivingAsset.original_filename}`
                          : comfyAssetsLoaded
                            ? 'No uploaded backend video asset selected.'
                            : 'Loading uploaded backend video assets...'}
                      </p>
                      <FileDropzone
                        accept="video/*"
                        label="Drop driving video"
                        hint={drivingUploadFile?.name || 'MP4, MOV, WebM up to 500 MB'}
                        file={drivingUploadFile}
                        onFileChange={setDrivingUploadFile}
                        maxSizeMB={500}
                        icon="video"
                      />
                      <Button
                        type="button"
                        variant="outline"
                        className="gap-2"
                        disabled={!drivingUploadFile || uploadingAssetKind === 'video'}
                        onClick={() => void handleUploadCharacterSwapAsset('video')}
                      >
                        <UploadCloud className="h-4 w-4" />
                        {uploadingAssetKind === 'video' ? 'Uploading...' : 'Upload Video'}
                      </Button>
                    </div>
                  </div>

                  <details className="rounded-xl border border-border/50 bg-background/40 p-4">
                    <summary className="cursor-pointer text-sm font-medium">Advanced Parameters</summary>
                    <div className="mt-4 grid gap-4 sm:grid-cols-2">
                      <div className="space-y-2">
                        <Label htmlFor="swap-seed">Seed</Label>
                        <Input
                          id="swap-seed"
                          value={characterSwap.seed}
                          onChange={(e) => updateCharacterSwap({ seed: e.target.value })}
                          placeholder="42"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="swap-steps">Steps</Label>
                        <Input
                          id="swap-steps"
                          type="number"
                          value={characterSwap.steps}
                          onChange={(e) => updateCharacterSwap({ steps: Number(e.target.value || 0) })}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="swap-cfg">CFG</Label>
                        <Input
                          id="swap-cfg"
                          type="number"
                          step="0.1"
                          value={characterSwap.cfg}
                          onChange={(e) => updateCharacterSwap({ cfg: Number(e.target.value || 0) })}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="swap-denoise">Denoise Strength</Label>
                        <Input
                          id="swap-denoise"
                          type="number"
                          step="0.1"
                          value={characterSwap.denoiseStrength}
                          onChange={(e) => updateCharacterSwap({ denoiseStrength: Number(e.target.value || 0) })}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="swap-fps">Frame Rate</Label>
                        <Input
                          id="swap-fps"
                          type="number"
                          value={characterSwap.frameRate}
                          onChange={(e) => updateCharacterSwap({ frameRate: Number(e.target.value || 0) })}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="swap-person-index">Pose Person Index</Label>
                        <Input
                          id="swap-person-index"
                          type="number"
                          min={0}
                          value={characterSwap.personIndex}
                          onChange={(e) => updateCharacterSwap({ personIndex: Number(e.target.value || 0) })}
                        />
                      </div>
                      <div className="space-y-2 sm:col-span-2">
                        <Label htmlFor="swap-subject-points">SAM Positive Points JSON</Label>
                        <Textarea
                          id="swap-subject-points"
                          value={characterSwap.subjectPointsJson}
                          onChange={(e) => updateCharacterSwap({ subjectPointsJson: e.target.value })}
                          rows={4}
                          placeholder='[{"x":575.8604020500962,"y":461.00299638143633},{"x":589.0269647654002,"y":105.50580306822965}]'
                        />
                        <p className="text-xs text-muted-foreground">
                          Positive target points in driving-video pixel space. Update these when the subject face/head
                          is not near the workflow&apos;s baked-in 1280x720 sample coordinates.
                        </p>
                      </div>
                      <div className="space-y-2 sm:col-span-2">
                        <Label htmlFor="swap-negative-points">SAM Negative Points JSON</Label>
                        <Textarea
                          id="swap-negative-points"
                          value={characterSwap.negativePointsJson}
                          onChange={(e) => updateCharacterSwap({ negativePointsJson: e.target.value })}
                          rows={3}
                          placeholder='[{"x":0,"y":0}]'
                        />
                        <p className="text-xs text-muted-foreground">
                          Optional negative points for excluding background or the wrong person from the SAM2 mask.
                        </p>
                      </div>
                      <label className="flex items-start gap-3 rounded-lg border border-border/50 bg-background/60 p-3 sm:col-span-2">
                        <input
                          type="checkbox"
                          checked={characterSwap.debugDump}
                          onChange={(e) => updateCharacterSwap({ debugDump: e.target.checked })}
                          className="mt-0.5 h-4 w-4 rounded border-input bg-background"
                        />
                        <div>
                          <p className="text-sm font-medium">Write patched workflow dump and save debug artifacts</p>
                          <p className="text-xs text-muted-foreground">
                            For the next submitted Character Swap job only, save the exact patched ComfyUI prompt JSON
                            to `/tmp/neonforge-comfyui-debug/&lt;job-id&gt;.patched.json` and enable debug pose,
                            mask, face-crop, decoded-frame, and preview artifact outputs.
                          </p>
                        </div>
                      </label>
                    </div>
                  </details>

                  <Button type="button" className="gap-2" onClick={handleCharacterSwapSubmit} disabled={!canSubmitCharacterSwap}>
                    <Send className="h-4 w-4" />
                    {submitting ? 'Submitting...' : 'Queue Character Swap'}
                  </Button>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Uploaded Assets</CardTitle>
                  <CardDescription>
                    Manage the files stored under the dedicated ComfyUI uploads root. These stay separate from final outputs.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {!comfyAssetsLoaded && (
                    <p className="text-sm text-muted-foreground">Loading uploaded character-swap assets...</p>
                  )}
                  {comfyAssetsLoaded && comfyAssets.length === 0 && (
                    <p className="text-sm text-muted-foreground">No uploaded character-swap assets yet.</p>
                  )}
                  {comfyAssets.map((asset) => (
                    <div
                      key={asset.id}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border/50 bg-background/40 p-3"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{asset.original_filename}</p>
                        <p className="text-xs text-muted-foreground">
                          {asset.kind} · {formatBytes(asset.size_bytes)} · {new Date(asset.created_at).toLocaleString()}
                        </p>
                      </div>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="gap-2 text-red-300 hover:text-red-200"
                        disabled={deletingComfyAssetId === asset.id}
                        onClick={() => void handleDeleteCharacterSwapAsset(asset.id)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        {deletingComfyAssetId === asset.id ? 'Deleting...' : 'Delete'}
                      </Button>
                    </div>
                  ))}
                </CardContent>
              </Card>
            </>
          )}

          {activeTab === 'f5tts' && (
            <>
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Provider</CardTitle>
                  <CardDescription>Select a TTS provider and NeonForge will adapt the form to its capabilities.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <select
                    value={selectedTTSProvider?.provider_id ?? ''}
                    onChange={(e) => updateF5TTS({ provider: e.target.value })}
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                  >
                    {enabledTTSProviders.map((provider) => (
                      <option key={provider.provider_id} value={provider.provider_id}>
                        {provider.display_name}
                      </option>
                    ))}
                  </select>
                  {selectedTTSProvider && (
                    <p className="text-xs text-muted-foreground">
                      {selectedTTSProvider.description || `${selectedTTSProvider.display_name} is ready for NeonForge jobs.`}
                    </p>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Text to Speech</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <Textarea
                    value={f5tts.text}
                    onChange={(e) => updateF5TTS({ text: e.target.value })}
                    placeholder="Enter the text to synthesize..."
                    rows={4}
                  />
                </CardContent>
              </Card>

              {selectedTTSProvider?.capabilities.supports_reference_audio && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Voice Clone Source</CardTitle>
                    <CardDescription>Select a saved voice asset or upload an ad-hoc sample.</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant={f5tts.voiceMode === 'none' ? 'default' : 'outline'}
                        onClick={() => updateF5TTS({ voiceMode: 'none' })}
                      >
                        None
                      </Button>
                      <Button
                        type="button"
                        variant={f5tts.voiceMode === 'saved' ? 'default' : 'outline'}
                        onClick={() => updateF5TTS({ voiceMode: 'saved' })}
                      >
                        Saved Voice
                      </Button>
                      <Button
                        type="button"
                        variant={f5tts.voiceMode === 'upload' ? 'default' : 'outline'}
                        onClick={() => updateF5TTS({ voiceMode: 'upload' })}
                      >
                        Upload
                      </Button>
                    </div>

                    {f5tts.voiceMode === 'saved' && (
                      <select
                        value={f5tts.savedVoicePath}
                        onChange={(e) => updateF5TTS({ savedVoicePath: e.target.value })}
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

                    {f5tts.voiceMode === 'upload' && (
                      <FileDropzone
                        accept="audio/*"
                        label="Drop reference audio here"
                        hint="WAV, MP3, WebM up to 50 MB"
                        file={f5RefAudioFile}
                        onFileChange={(file) => {
                          setF5RefAudioFile(file)
                          updateF5TTS({ uploadedRefAudioName: file?.name ?? null })
                        }}
                        maxSizeMB={50}
                        icon="audio"
                      />
                    )}

                    <Input
                      value={f5tts.referenceText}
                      onChange={(e) => updateF5TTS({ referenceText: e.target.value })}
                      placeholder="Optional reference transcript..."
                    />
                  </CardContent>
                </Card>
              )}

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Provider Controls</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-2">
                    <Label>Speaker / Reference Name</Label>
                    <Input
                      value={f5tts.speakerName}
                      onChange={(e) => updateF5TTS({ speakerName: e.target.value })}
                      placeholder="Optional provider-specific speaker or reference identifier"
                    />
                  </div>

                  {selectedTTSProvider?.capabilities.style_prompt && (
                    <div className="space-y-2">
                      <Label>Style Prompt</Label>
                      <Textarea
                        value={f5tts.stylePrompt}
                        onChange={(e) => updateF5TTS({ stylePrompt: e.target.value })}
                        rows={3}
                        placeholder="Describe the intended delivery, pacing, or tone..."
                      />
                    </div>
                  )}

                  {selectedTTSProvider?.capabilities.continuation_edit && (
                    <FileDropzone
                      accept="audio/*"
                      label="Drop continuation seed audio here"
                      hint={f5tts.continuationAudioName || 'Optional seed clip for continuation/edit capable providers'}
                      file={ttsContinuationFile}
                      onFileChange={(file) => {
                        setTTSContinuationFile(file)
                        updateF5TTS({ continuationAudioName: file?.name ?? null })
                      }}
                      maxSizeMB={50}
                      icon="audio"
                    />
                  )}

                  {selectedTTSProvider?.capabilities.transcript_guided_continuation && (
                    <div className="space-y-2">
                      <Label>Transcript Guidance</Label>
                      <Textarea
                        value={f5tts.transcript}
                        onChange={(e) => updateF5TTS({ transcript: e.target.value })}
                        rows={3}
                        placeholder="Optional transcript or continuation guidance..."
                      />
                    </div>
                  )}

                  {(selectedTTSProvider?.supported_output_formats.length ?? 0) > 1 && (
                    <div className="space-y-2">
                      <Label>Output Format</Label>
                      <select
                        value={f5tts.outputFormat}
                        onChange={(e) => updateF5TTS({ outputFormat: e.target.value })}
                        className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                      >
                        {selectedTTSProvider?.supported_output_formats.map((format) => (
                          <option key={format} value={format}>
                            {format.toUpperCase()}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}

                  {(selectedTTSProvider?.supported_target_sample_rates.length ?? 0) > 1 && (
                    <div className="space-y-2">
                      <Label>Target Sample Rate</Label>
                      <select
                        value={f5tts.targetSampleRate}
                        onChange={(e) => updateF5TTS({ targetSampleRate: e.target.value })}
                        className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                      >
                        {selectedTTSProvider?.supported_target_sample_rates.map((sampleRate) => (
                          <option key={sampleRate} value={sampleRate}>
                            {sampleRate.toLocaleString()} Hz
                          </option>
                        ))}
                      </select>
                    </div>
                  )}

                  {selectedTTSProvider?.option_fields.map((field) => {
                    const value = f5tts.options[field.id]
                    const numericValue = typeof value === 'number' ? value : Number(value ?? field.default ?? 0)
                    if ((field.type === 'number' || field.type === 'integer') && field.min != null && field.max != null) {
                      return (
                        <div key={field.id} className="space-y-2">
                          <div className="flex items-center justify-between">
                            <Label>{field.label}</Label>
                            <span className="text-xs text-muted-foreground">{numericValue.toFixed(2)}</span>
                          </div>
                          <Slider
                            value={numericValue}
                            min={field.min}
                            max={field.max}
                            step={field.step ?? 0.1}
                            onChange={(next) =>
                              updateF5TTS({
                                options: {
                                  ...f5tts.options,
                                  [field.id]: next,
                                },
                              })
                            }
                          />
                          {field.description && <p className="text-xs text-muted-foreground">{field.description}</p>}
                        </div>
                      )
                    }
                    return (
                      <div key={field.id} className="space-y-2">
                        <Label>{field.label}</Label>
                        <Input
                          type={field.type === 'number' || field.type === 'integer' ? 'number' : 'text'}
                          value={String(value ?? field.default ?? '')}
                          onChange={(e) =>
                            updateF5TTS({
                              options: {
                                ...f5tts.options,
                                [field.id]: e.target.value,
                              },
                            })
                          }
                        />
                        {field.description && <p className="text-xs text-muted-foreground">{field.description}</p>}
                      </div>
                    )
                  })}
                </CardContent>
              </Card>

              <Button
                type="button"
                className="gap-2"
                onClick={handleF5Submit}
                disabled={submitting || !f5tts.text.trim() || !selectedTTSProvider}
              >
                <Send className="h-4 w-4" />
                {submitting ? 'Submitting...' : 'Generate Voice'}
              </Button>
            </>
          )}

          {activeTab === 'liveportrait' && (
            <>
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">LivePortrait Inputs</CardTitle>
                  <CardDescription>
                    The file references remain in state while you switch between tabs.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <FileDropzone
                    accept="image/*"
                    label="Drop source image"
                    hint={liveportrait.sourceImageName || 'PNG, JPG up to 20 MB'}
                    file={liveSourceFile}
                    onFileChange={(file) => {
                      setLiveSourceFile(file)
                      updateLivePortrait({ sourceImageName: file?.name ?? null })
                    }}
                    maxSizeMB={20}
                    icon="image"
                  />
                  <FileDropzone
                    accept="video/*"
                    label="Drop driving video"
                    hint={liveportrait.drivingVideoName || 'MP4, WebM, MOV up to 500 MB'}
                    file={liveDrivingFile}
                    onFileChange={(file) => {
                      setLiveDrivingFile(file)
                      updateLivePortrait({ drivingVideoName: file?.name ?? null })
                    }}
                    maxSizeMB={500}
                    icon="video"
                  />
                </CardContent>
              </Card>

              <Button
                type="button"
                className="gap-2"
                onClick={handleLivePortraitSubmit}
                disabled={submitting || !liveSourceFile || !liveDrivingFile}
              >
                <Send className="h-4 w-4" />
                {submitting ? 'Submitting...' : 'Animate Portrait'}
              </Button>
            </>
          )}

          {activeTab === 'reactor' && (
            <>
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Sparkles className="h-4 w-4 text-primary" />
                    ReActor / ComfyUI
                  </CardTitle>
                  <CardDescription>
                    Select a saved LoRA path and send it directly through the gateway to ComfyUI.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <Textarea
                    value={reactor.prompt}
                    onChange={(e) => updateReactor({ prompt: e.target.value })}
                    placeholder="Prompt for your ReActor workflow..."
                    rows={4}
                  />
                  <Textarea
                    value={reactor.negativePrompt}
                    onChange={(e) => updateReactor({ negativePrompt: e.target.value })}
                    placeholder="Optional negative prompt..."
                    rows={2}
                  />
                  <div className="space-y-2">
                    <Label>Saved LoRA</Label>
                    <select
                      value={reactor.savedLoraPath}
                      onChange={(e) => updateReactor({ savedLoraPath: e.target.value })}
                      className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                    >
                      <option value="">Select saved LoRA</option>
                      {loraAssets.map((asset) => (
                        <option key={asset.path} value={asset.path}>
                          {asset.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label>LoRA Strength</Label>
                      <span className="text-xs text-muted-foreground">{reactor.loraStrength.toFixed(2)}</span>
                    </div>
                    <Slider
                      value={reactor.loraStrength}
                      min={0}
                      max={1.5}
                      step={0.05}
                      onChange={(v) => updateReactor({ loraStrength: v })}
                    />
                  </div>
                </CardContent>
              </Card>

              <Button type="button" className="gap-2" onClick={handleReactorSubmit} disabled={submitting || !reactor.prompt.trim()}>
                <Send className="h-4 w-4" />
                {submitting ? 'Submitting...' : 'Queue ReActor Job'}
              </Button>
            </>
          )}
        </div>

        <div className="space-y-4">
          <JobTracker jobs={jobs} onDismiss={dismissJob} />
          <HistoryPane services={['f5tts', 'fish_speech', 'premium_clone_tts', 'liveportrait', 'reactor', 'comfyui']} />
        </div>
      </div>
    </div>
  )
}
