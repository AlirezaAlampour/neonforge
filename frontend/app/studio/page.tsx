'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Clapperboard, Mic, Save, Send, Sparkles, Video } from 'lucide-react'
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
  deletePreset,
  fetchLoraAssets,
  fetchPresets,
  fetchVoiceAssets,
  savePreset,
  submitLivePortrait,
  submitReactor,
  submitTTS,
} from '@/lib/api'
import { useStudioStore } from '@/lib/stores/studio-store'
import type { AssetItem, PresetProfile, StudioTool } from '@/lib/types'

const tabs: Array<{ id: StudioTool; label: string; icon: typeof Mic }> = [
  { id: 'f5tts', label: 'F5-TTS', icon: Mic },
  { id: 'liveportrait', label: 'LivePortrait', icon: Video },
  { id: 'reactor', label: 'ReActor', icon: Clapperboard },
]

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
  const [liveSourceFile, setLiveSourceFile] = useState<File | null>(null)
  const [liveDrivingFile, setLiveDrivingFile] = useState<File | null>(null)

  const activeTab = useStudioStore((state) => state.activeTab)
  const setActiveTab = useStudioStore((state) => state.setActiveTab)
  const f5tts = useStudioStore((state) => state.f5tts)
  const liveportrait = useStudioStore((state) => state.liveportrait)
  const reactor = useStudioStore((state) => state.reactor)
  const updateF5TTS = useStudioStore((state) => state.updateF5TTS)
  const updateLivePortrait = useStudioStore((state) => state.updateLivePortrait)
  const updateReactor = useStudioStore((state) => state.updateReactor)
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
  }, [refreshAssets])

  useEffect(() => {
    setSelectedPresetId('')
    refreshPresets()
  }, [activeTab, refreshPresets])

  const selectedPreset = useMemo(
    () => presets.find((preset) => preset.id === selectedPresetId) ?? null,
    [presets, selectedPresetId],
  )

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
    if (!f5tts.text.trim()) return

    setSubmitting(true)
    setError(null)
    try {
      const formData = new FormData()
      formData.append('text', f5tts.text)
      formData.append('speed', String(f5tts.speed))
      if (f5tts.refText.trim()) formData.append('ref_text', f5tts.refText.trim())

      if (f5tts.voiceMode === 'saved' && f5tts.savedVoicePath) {
        formData.append('saved_voice_path', f5tts.savedVoicePath)
      } else if (f5tts.voiceMode === 'upload') {
        if (!f5RefAudioFile) {
          setError('Upload mode is selected but no reference audio file is attached.')
          return
        }
        formData.append('ref_audio', f5RefAudioFile)
      }

      const result = await submitTTS(formData)
      trackJob(result.job_id, 'f5tts')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'TTS request failed')
    } finally {
      setSubmitting(false)
    }
  }, [f5RefAudioFile, f5tts, trackJob])

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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Creative Studio</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Persistent tab state, reusable assets, history gallery, and one-click presets.
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
              placeholder='Preset name (e.g., "Melodic Techno Visualizer - Style A")'
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
          {activeTab === 'f5tts' && (
            <>
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
                  <Input
                    value={f5tts.refText}
                    onChange={(e) => updateF5TTS({ refText: e.target.value })}
                    placeholder="Optional reference transcript..."
                  />
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label>Speed</Label>
                      <span className="text-xs text-muted-foreground">{f5tts.speed.toFixed(2)}x</span>
                    </div>
                    <Slider value={f5tts.speed} min={0.5} max={2.0} step={0.05} onChange={(v) => updateF5TTS({ speed: v })} />
                  </div>
                </CardContent>
              </Card>

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
                </CardContent>
              </Card>

              <Button type="button" className="gap-2" onClick={handleF5Submit} disabled={submitting || !f5tts.text.trim()}>
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
                    icon="video"
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
                  <CardTitle className="text-base flex items-center gap-2">
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
          <HistoryPane services={['f5tts', 'liveportrait', 'reactor']} />
        </div>
      </div>
    </div>
  )
}
