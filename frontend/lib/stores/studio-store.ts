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
  personIndex: number
  subjectPointsJson: string
  negativePointsJson: string
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

const CHARACTER_SWAP_STORE_VERSION = 2
const LEGACY_CHARACTER_SWAP_STEPS_DEFAULT = 6
const LEGACY_CHARACTER_SWAP_DENOISE_DEFAULT = 5
const LEGACY_CHARACTER_SWAP_SUBJECT_POINTS_JSON =
  '[{"x":381.21190175127964,"y":356.03653739792486},{"x":400.329750813901,"y":91.57295869833005}]'
const DEFAULT_CHARACTER_SWAP_SUBJECT_POINTS_JSON =
  '[{"x":575.8604020500962,"y":461.00299638143633},{"x":589.0269647654002,"y":105.50580306822965}]'
const DEFAULT_CHARACTER_SWAP_NEGATIVE_POINTS_JSON = '[{"x":0,"y":0}]'

const initialCharacterSwap: CharacterSwapFormState = {
  templateId: '',
  referenceImageAssetId: '',
  drivingVideoAssetId: '',
  seed: '42',
  steps: 4,
  cfg: 1,
  denoiseStrength: 0.9,
  frameRate: 16,
  personIndex: 0,
  subjectPointsJson: DEFAULT_CHARACTER_SWAP_SUBJECT_POINTS_JSON,
  negativePointsJson: DEFAULT_CHARACTER_SWAP_NEGATIVE_POINTS_JSON,
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

function toStringOr(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback
}

function toNumberOr(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function toVoiceMode(value: unknown): F5TTSFormState['voiceMode'] {
  return value === 'saved' || value === 'upload' || value === 'none' ? value : 'none'
}

function migrateCharacterSwapState(value: unknown, version: number): Record<string, unknown> {
  const payload = toRecord(value)
  if (version >= CHARACTER_SWAP_STORE_VERSION) {
    return payload
  }

  const migrated: Record<string, unknown> = { ...payload }

  if (toNumberOr(payload.steps, initialCharacterSwap.steps) === LEGACY_CHARACTER_SWAP_STEPS_DEFAULT) {
    migrated.steps = initialCharacterSwap.steps
  }
  if (toNumberOr(payload.denoiseStrength, initialCharacterSwap.denoiseStrength) === LEGACY_CHARACTER_SWAP_DENOISE_DEFAULT) {
    migrated.denoiseStrength = initialCharacterSwap.denoiseStrength
  }

  const subjectPointsJson = toStringOrEmpty(payload.subjectPointsJson)
  if (!subjectPointsJson || subjectPointsJson === LEGACY_CHARACTER_SWAP_SUBJECT_POINTS_JSON) {
    migrated.subjectPointsJson = DEFAULT_CHARACTER_SWAP_SUBJECT_POINTS_JSON
  }

  if (!toStringOrEmpty(payload.negativePointsJson)) {
    migrated.negativePointsJson = DEFAULT_CHARACTER_SWAP_NEGATIVE_POINTS_JSON
  }

  if (!('personIndex' in payload)) {
    migrated.personIndex = initialCharacterSwap.personIndex
  }

  return migrated
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
              steps: toNumberOr(payload.steps, 4),
              cfg: toNumberOr(payload.cfg, 1),
              denoiseStrength: toNumberOr(payload.denoiseStrength, 0.9),
              frameRate: toNumberOr(payload.frameRate, 16),
              personIndex: toNumberOr(payload.personIndex, 0),
              subjectPointsJson: toStringOr(payload.subjectPointsJson, initialCharacterSwap.subjectPointsJson),
              negativePointsJson: toStringOr(payload.negativePointsJson, initialCharacterSwap.negativePointsJson),
              debugDump: Boolean(payload.debugDump),
            },
          }
        }),
    }),
    {
      name: 'neonforge-studio-state',
      version: CHARACTER_SWAP_STORE_VERSION,
      migrate: (persistedState, version) => {
        const payload = toRecord(persistedState)
        return {
          ...payload,
          characterSwap: migrateCharacterSwapState(payload.characterSwap, version),
        }
      },
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
