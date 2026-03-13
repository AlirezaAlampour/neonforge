'use client'

import { Mic, Square, Pause, Play, Trash2, AlertTriangle } from 'lucide-react'
import { useMediaRecorder } from '@/hooks/use-media-recorder'
import { formatDuration } from '@/lib/utils'
import { cn } from '@/lib/utils'
import { Button } from './ui/button'

interface AudioRecorderProps {
  onRecorded: (blob: Blob) => void
  audioBlob: Blob | null
  audioUrl: string | null
}

export function AudioRecorder({ onRecorded, audioBlob: externalBlob, audioUrl: externalUrl }: AudioRecorderProps) {
  const recorder = useMediaRecorder()
  const audioUrl = externalUrl || recorder.audioUrl

  const handleStop = () => {
    recorder.stopRecording()
    // The blob is set asynchronously in onstop; use a small delay
    setTimeout(() => {
      if (recorder.audioBlob) onRecorded(recorder.audioBlob)
    }, 100)
  }

  // Watch for internal blob changes
  if (recorder.audioBlob && !externalBlob) {
    onRecorded(recorder.audioBlob)
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        {!recorder.isRecording && !audioUrl && (
          <Button
            type="button"
            variant="outline"
            onClick={recorder.startRecording}
            className="gap-2"
          >
            <Mic className="h-4 w-4" />
            Record Mic
          </Button>
        )}

        {recorder.isRecording && (
          <>
            <Button type="button" variant="destructive" onClick={handleStop} className="gap-2">
              <Square className="h-3.5 w-3.5" />
              Stop
            </Button>
            <Button
              type="button"
              variant="outline"
              size="icon"
              onClick={recorder.isPaused ? recorder.resumeRecording : recorder.pauseRecording}
            >
              {recorder.isPaused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
            </Button>
            <div className="flex items-center gap-2 ml-2">
              <span
                className={cn(
                  'h-2.5 w-2.5 rounded-full',
                  recorder.isPaused ? 'bg-amber-400' : 'bg-red-500 animate-pulse',
                )}
              />
              <span className="text-sm font-mono tabular-nums text-muted-foreground">
                {formatDuration(recorder.duration)}
              </span>
            </div>
          </>
        )}

        {audioUrl && !recorder.isRecording && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={recorder.clearRecording}
            className="text-muted-foreground hover:text-red-400"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        )}
      </div>

      {audioUrl && !recorder.isRecording && (
        <audio controls className="w-full h-8 [&::-webkit-media-controls-panel]:bg-secondary rounded">
          <source src={audioUrl} />
        </audio>
      )}

      {recorder.error && (
        <div className="flex items-start gap-2 rounded-lg bg-amber-500/10 border border-amber-500/20 p-3">
          <AlertTriangle className="h-4 w-4 text-amber-400 shrink-0 mt-0.5" />
          <div>
            <p className="text-xs text-amber-300">{recorder.error}</p>
            <p className="text-xs text-amber-400/60 mt-1">
              Tip: Use HTTPS or enable chrome://flags/#unsafely-treat-insecure-origin-as-secure
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
