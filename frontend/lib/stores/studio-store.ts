'use client'

import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import type { StudioTool } from '@/lib/types'

export interface F5TTSFormState {
  text: string
  refText: string
  speed: number
  voiceMode: 'none' | 'saved' | 'upload'
  savedVoicePath: string
  uploadedRefAudioName: string | null
}

export interface LivePortraitFormState {
  sourceImageName: string | null
  drivingVideoName: string | null
}

export interface ReactorFormState {
  prompt: string
  negativePrompt: string
  savedLoraPath: string
  loraStrength: number
}

export interface CharacterSwapFormState {
  templateId: string
  referenceImageAssetId: string
  drivingVideoAssetId: string
  seed: string
  steps: number
  cfg: number
  denoiseStrength: number
  frameRate: number
  debugDump: boolean
}

interface StudioState {
  activeTab: StudioTool
  f5tts: F5TTSFormState
  liveportrait: LivePortraitFormState
  reactor: ReactorFormState
  characterSwap: CharacterSwapFormState
  setActiveTab: (tab: StudioTool) => void
  updateF5TTS: (patch: Partial<F5TTSFormState>) => void
  updateLivePortrait: (patch: Partial<LivePortraitFormState>) => void
  updateReactor: (patch: Partial<ReactorFormState>) => void
  updateCharacterSwap: (patch: Partial<CharacterSwapFormState>) => void
  getPresetState: (tool: StudioTool) => Record<string, unknown>
  applyPresetState: (tool: StudioTool, state: Record<string, unknown>) => void
}

const initialF5TTS: F5TTSFormState = {
  text: '',
  refText: '',
  speed: 1,
  voiceMode: 'none',
  savedVoicePath: '',
  uploadedRefAudioName: null,
}

const initialLivePortrait: LivePortraitFormState = {
  sourceImageName: null,
  drivingVideoName: null,
}

const initialReactor: ReactorFormState = {
  prompt: '',
  negativePrompt: '',
  savedLoraPath: '',
  loraStrength: 0.75,
}

const initialCharacterSwap: CharacterSwapFormState = {
  templateId: '',
  referenceImageAssetId: '',
  drivingVideoAssetId: '',
  seed: '42',
  steps: 6,
  cfg: 1,
  denoiseStrength: 5,
  frameRate: 16,
  debugDump: false,
}

function toRecord(value: unknown): Record<string, unknown> {
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return {}
}

function toStringOrEmpty(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function toNullableString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function toNumberOr(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function toVoiceMode(value: unknown): F5TTSFormState['voiceMode'] {
  return value === 'saved' || value === 'upload' || value === 'none' ? value : 'none'
}

export const useStudioStore = create<StudioState>()(
  persist(
    (set, get) => ({
      activeTab: 'f5tts',
      f5tts: initialF5TTS,
      liveportrait: initialLivePortrait,
      reactor: initialReactor,
      characterSwap: initialCharacterSwap,
      setActiveTab: (tab) => set({ activeTab: tab }),
      updateF5TTS: (patch) =>
        set((state) => ({
          f5tts: {
            ...state.f5tts,
            ...patch,
          },
        })),
      updateLivePortrait: (patch) =>
        set((state) => ({
          liveportrait: {
            ...state.liveportrait,
            ...patch,
          },
        })),
      updateReactor: (patch) =>
        set((state) => ({
          reactor: {
            ...state.reactor,
            ...patch,
          },
        })),
      updateCharacterSwap: (patch) =>
        set((state) => ({
          characterSwap: {
            ...state.characterSwap,
            ...patch,
          },
        })),
      getPresetState: (tool) => {
        const state = get()
        if (tool === 'f5tts') return { ...state.f5tts }
        if (tool === 'liveportrait') return { ...state.liveportrait }
        if (tool === 'reactor') return { ...state.reactor }
        return { ...state.characterSwap }
      },
      applyPresetState: (tool, state) =>
        set((current) => {
          const payload = toRecord(state)
          if (tool === 'f5tts') {
            return {
              f5tts: {
                ...current.f5tts,
                text: toStringOrEmpty(payload.text),
                refText: toStringOrEmpty(payload.refText),
                speed: toNumberOr(payload.speed, 1),
                voiceMode: toVoiceMode(payload.voiceMode),
                savedVoicePath: toStringOrEmpty(payload.savedVoicePath),
                uploadedRefAudioName: toNullableString(payload.uploadedRefAudioName),
              },
            }
          }
          if (tool === 'liveportrait') {
            return {
              liveportrait: {
                ...current.liveportrait,
                sourceImageName: toNullableString(payload.sourceImageName),
                drivingVideoName: toNullableString(payload.drivingVideoName),
              },
            }
          }
          if (tool === 'reactor') {
            return {
              reactor: {
                ...current.reactor,
                prompt: toStringOrEmpty(payload.prompt),
                negativePrompt: toStringOrEmpty(payload.negativePrompt),
                savedLoraPath: toStringOrEmpty(payload.savedLoraPath),
                loraStrength: toNumberOr(payload.loraStrength, 0.75),
              },
            }
          }
          return {
            characterSwap: {
              ...current.characterSwap,
              templateId: toStringOrEmpty(payload.templateId),
              referenceImageAssetId: toStringOrEmpty(payload.referenceImageAssetId),
              drivingVideoAssetId: toStringOrEmpty(payload.drivingVideoAssetId),
              seed: toStringOrEmpty(payload.seed),
              steps: toNumberOr(payload.steps, 6),
              cfg: toNumberOr(payload.cfg, 1),
              denoiseStrength: toNumberOr(payload.denoiseStrength, 5),
              frameRate: toNumberOr(payload.frameRate, 16),
              debugDump: Boolean(payload.debugDump),
            },
          }
        }),
    }),
    {
      name: 'neonforge-studio-state',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        activeTab: state.activeTab,
        f5tts: state.f5tts,
        liveportrait: state.liveportrait,
        reactor: state.reactor,
        characterSwap: state.characterSwap,
      }),
    },
  ),
)
