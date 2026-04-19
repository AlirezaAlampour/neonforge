'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

const DEFAULT_RECORDING_FILE_EXTENSION = 'webm'
const DEFAULT_PREFERRED_MIME_TYPES = [
  'audio/wav',
  'audio/webm;codecs=pcm',
  'audio/webm;codecs=opus',
  'audio/mp4;codecs=mp4a.40.2',
  'audio/mp4',
  'audio/ogg;codecs=opus',
  'audio/webm',
  'audio/ogg',
]

export interface MediaRecorderOptions {
  audioConstraints?: MediaTrackConstraints
  preferredMimeTypes?: string[]
}

export interface MediaRecorderState {
  isRecording: boolean
  isPaused: boolean
  duration: number
  audioBlob: Blob | null
  audioUrl: string | null
  error: string | null
  mimeType: string | null
  fileExtension: string
  inputLevel: number
  startRecording: () => Promise<void>
  stopRecording: () => void
  pauseRecording: () => void
  resumeRecording: () => void
  clearRecording: () => void
}

function inferRecordingFileExtension(mimeType: string | null): string {
  const normalizedMimeType = mimeType?.toLowerCase() ?? ''

  if (normalizedMimeType.includes('wav')) return 'wav'
  if (normalizedMimeType.includes('mpeg') || normalizedMimeType.includes('mp3')) return 'mp3'
  if (
    normalizedMimeType.includes('mp4') ||
    normalizedMimeType.includes('m4a') ||
    normalizedMimeType.includes('aac')
  ) {
    return 'm4a'
  }
  if (normalizedMimeType.includes('ogg')) return 'ogg'
  return DEFAULT_RECORDING_FILE_EXTENSION
}

function resolveSupportedMimeType(preferredMimeTypes: string[]): string | null {
  if (typeof window === 'undefined' || typeof window.MediaRecorder === 'undefined') {
    return null
  }

  if (typeof window.MediaRecorder.isTypeSupported !== 'function') {
    return null
  }

  return preferredMimeTypes.find((candidate) => window.MediaRecorder.isTypeSupported(candidate)) ?? null
}

function buildPreferredAudioConstraints(overrides?: MediaTrackConstraints): MediaTrackConstraints {
  const supportedConstraints = navigator.mediaDevices.getSupportedConstraints?.() ?? {}
  const constraints: MediaTrackConstraints = { ...overrides }

  if (supportedConstraints.channelCount && constraints.channelCount === undefined) {
    constraints.channelCount = 1
  }
  if (supportedConstraints.sampleRate && constraints.sampleRate === undefined) {
    constraints.sampleRate = 48000
  }
  if (supportedConstraints.sampleSize && constraints.sampleSize === undefined) {
    constraints.sampleSize = 24
  }
  if (supportedConstraints.echoCancellation && constraints.echoCancellation === undefined) {
    constraints.echoCancellation = false
  }
  if (supportedConstraints.noiseSuppression && constraints.noiseSuppression === undefined) {
    constraints.noiseSuppression = false
  }
  if (supportedConstraints.autoGainControl && constraints.autoGainControl === undefined) {
    constraints.autoGainControl = false
  }

  return constraints
}

export function useMediaRecorder(options?: MediaRecorderOptions): MediaRecorderState {
  const preferredMimeTypes = options?.preferredMimeTypes ?? DEFAULT_PREFERRED_MIME_TYPES
  const audioConstraints = options?.audioConstraints
  const [isRecording, setIsRecording] = useState(false)
  const [isPaused, setIsPaused] = useState(false)
  const [duration, setDuration] = useState(0)
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null)
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [mimeType, setMimeType] = useState<string | null>(null)
  const [fileExtension, setFileExtension] = useState(DEFAULT_RECORDING_FILE_EXTENSION)
  const [inputLevel, setInputLevel] = useState(0)

  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const audioUrlRef = useRef<string | null>(null)
  const levelMeterIntervalRef = useRef<number | null>(null)
  const meterAudioContextRef = useRef<AudioContext | null>(null)
  const meterAnalyserRef = useRef<AnalyserNode | null>(null)
  const meterSourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const meterBufferRef = useRef<Uint8Array | null>(null)

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const stopLevelMeter = useCallback(() => {
    if (typeof window !== 'undefined' && levelMeterIntervalRef.current !== null) {
      window.clearInterval(levelMeterIntervalRef.current)
    }
    levelMeterIntervalRef.current = null
    meterSourceRef.current?.disconnect()
    meterSourceRef.current = null
    meterAnalyserRef.current?.disconnect()
    meterAnalyserRef.current = null
    meterBufferRef.current = null

    const meterAudioContext = meterAudioContextRef.current
    meterAudioContextRef.current = null
    if (meterAudioContext) {
      void meterAudioContext.close().catch(() => undefined)
    }

    setInputLevel(0)
  }, [])

  const startLevelMeter = useCallback((stream: MediaStream) => {
    stopLevelMeter()

    if (typeof window === 'undefined' || typeof window.AudioContext === 'undefined') {
      return
    }

    try {
      const audioContext = new window.AudioContext()
      const analyser = audioContext.createAnalyser()
      const source = audioContext.createMediaStreamSource(stream)
      const data = new Uint8Array(1024)

      analyser.fftSize = 1024
      analyser.smoothingTimeConstant = 0.8
      source.connect(analyser)

      meterAudioContextRef.current = audioContext
      meterAnalyserRef.current = analyser
      meterSourceRef.current = source
      meterBufferRef.current = data

      if (audioContext.state === 'suspended') {
        void audioContext.resume().catch(() => undefined)
      }

      levelMeterIntervalRef.current = window.setInterval(() => {
        const activeAnalyser = meterAnalyserRef.current
        const activeBuffer = meterBufferRef.current
        if (!activeAnalyser || !activeBuffer) {
          setInputLevel(0)
          return
        }

        activeAnalyser.getByteTimeDomainData(activeBuffer)

        let sumSquares = 0
        for (const sample of activeBuffer) {
          const normalized = (sample - 128) / 128
          sumSquares += normalized * normalized
        }

        const rms = Math.sqrt(sumSquares / activeBuffer.length)
        setInputLevel(Math.max(0, Math.min(1, rms * 4)))
      }, 80)
    } catch {
      stopLevelMeter()
    }
  }, [stopLevelMeter])

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    stopLevelMeter()
  }, [stopLevelMeter])

  const replaceAudioUrl = useCallback((nextUrl: string | null) => {
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current)
    }
    audioUrlRef.current = nextUrl
    setAudioUrl(nextUrl)
  }, [])

  const startRecording = useCallback(async () => {
    try {
      if (typeof window === 'undefined' || typeof navigator === 'undefined') {
        setError('Microphone recording is only available in the browser.')
        return
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        setError('Microphone recording is not supported in this browser.')
        return
      }
      if (typeof window.MediaRecorder === 'undefined') {
        setError('This browser runtime does not support in-app microphone recording.')
        return
      }

      setError(null)
      setAudioBlob(null)
      setMimeType(null)
      setFileExtension(DEFAULT_RECORDING_FILE_EXTENSION)
      setInputLevel(0)
      replaceAudioUrl(null)
      stopStream()

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: buildPreferredAudioConstraints(audioConstraints),
      })
      streamRef.current = stream
      startLevelMeter(stream)

      const preferredMimeType = resolveSupportedMimeType(preferredMimeTypes)
      const recorder = preferredMimeType
        ? new MediaRecorder(stream, { mimeType: preferredMimeType })
        : new MediaRecorder(stream)
      recorderRef.current = recorder
      chunksRef.current = []

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      recorder.onstop = () => {
        const resolvedMimeType =
          recorder.mimeType || chunksRef.current[0]?.type || preferredMimeType || 'audio/webm'
        const blob = new Blob(chunksRef.current, { type: resolvedMimeType })
        setAudioBlob(blob)
        setMimeType(resolvedMimeType)
        setFileExtension(inferRecordingFileExtension(resolvedMimeType))
        replaceAudioUrl(URL.createObjectURL(blob))
        stopStream()
        clearTimer()
      }

      recorder.onerror = () => {
        setError('Recording failed while the browser was capturing audio.')
        setIsRecording(false)
        setIsPaused(false)
        clearTimer()
        stopStream()
      }

      recorder.start(250)
      setIsRecording(true)
      setIsPaused(false)
      setDuration(0)
      timerRef.current = setInterval(() => setDuration((d) => d + 1), 1000)
    } catch (err: unknown) {
      clearTimer()
      stopStream()
      setIsRecording(false)
      setIsPaused(false)
      setInputLevel(0)

      const e = err as DOMException
      if (e.name === 'NotAllowedError') {
        setError('Microphone access denied. Check browser permissions or enable HTTPS.')
      } else if (e.name === 'NotFoundError') {
        setError('No microphone found on this device.')
      } else if (e.name === 'NotReadableError') {
        setError('Microphone is already in use by another application or browser tab.')
      } else if (e.name === 'SecurityError') {
        setError('Microphone recording requires a secure browser context such as HTTPS or localhost.')
      } else {
        setError(`Recording failed: ${e.message}`)
      }
    }
  }, [audioConstraints, clearTimer, preferredMimeTypes, replaceAudioUrl, stopStream])

  const stopRecording = useCallback(() => {
    if (recorderRef.current?.state !== 'inactive') {
      recorderRef.current?.stop()
    }
    clearTimer()
    setIsRecording(false)
    setIsPaused(false)
  }, [clearTimer])

  const pauseRecording = useCallback(() => {
    if (recorderRef.current?.state !== 'recording') {
      return
    }

    recorderRef.current.pause()
    clearTimer()
    setIsPaused(true)
  }, [clearTimer])

  const resumeRecording = useCallback(() => {
    if (recorderRef.current?.state !== 'paused') {
      return
    }

    recorderRef.current.resume()
    clearTimer()
    timerRef.current = setInterval(() => setDuration((d) => d + 1), 1000)
    setIsPaused(false)
  }, [clearTimer])

  const clearRecording = useCallback(() => {
    setAudioBlob(null)
    setMimeType(null)
    setFileExtension(DEFAULT_RECORDING_FILE_EXTENSION)
    replaceAudioUrl(null)
    setDuration(0)
    setError(null)
    setInputLevel(0)
  }, [replaceAudioUrl])

  useEffect(() => {
    return () => {
      clearTimer()
      if (recorderRef.current) {
        recorderRef.current.ondataavailable = null
        recorderRef.current.onstop = null
        recorderRef.current.onerror = null
        if (recorderRef.current.state !== 'inactive') {
          recorderRef.current.stop()
        }
      }
      stopStream()
      if (audioUrlRef.current) {
        URL.revokeObjectURL(audioUrlRef.current)
      }
    }
  }, [clearTimer, stopStream])

  return {
    isRecording,
    isPaused,
    duration,
    audioBlob,
    audioUrl,
    error,
    mimeType,
    fileExtension,
    inputLevel,
    startRecording, stopRecording, pauseRecording, resumeRecording, clearRecording,
  }
}
