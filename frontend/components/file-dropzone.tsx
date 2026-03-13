'use client'

import { useCallback, useState, type DragEvent } from 'react'
import { Upload, X, FileAudio, FileVideo } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatBytes } from '@/lib/utils'
import { Button } from './ui/button'

interface FileDropzoneProps {
  accept: string
  label: string
  hint?: string
  file: File | null
  onFileChange: (file: File | null) => void
  maxSizeMB?: number
  icon?: 'audio' | 'video'
}

export function FileDropzone({
  accept,
  label,
  hint,
  file,
  onFileChange,
  maxSizeMB = 500,
  icon = 'audio',
}: FileDropzoneProps) {
  const [dragOver, setDragOver] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const validateAndSet = useCallback(
    (f: File) => {
      setError(null)
      if (f.size > maxSizeMB * 1024 * 1024) {
        setError(`File too large (max ${maxSizeMB} MB)`)
        return
      }
      onFileChange(f)
    },
    [maxSizeMB, onFileChange],
  )

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault()
      setDragOver(false)
      const f = e.dataTransfer.files[0]
      if (f) validateAndSet(f)
    },
    [validateAndSet],
  )

  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault()
    setDragOver(true)
  }, [])

  const handleDragLeave = useCallback(() => setDragOver(false), [])

  const Icon = icon === 'video' ? FileVideo : FileAudio

  if (file) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-border/50 bg-card/30 p-3">
        <Icon className="h-8 w-8 shrink-0 text-primary/70" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium truncate">{file.name}</p>
          <p className="text-xs text-muted-foreground">{formatBytes(file.size)}</p>
        </div>
        <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" onClick={() => onFileChange(null)}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
    )
  }

  return (
    <div>
      <label
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        className={cn(
          'flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-6',
          'transition-all duration-200',
          dragOver
            ? 'border-primary bg-primary/5 scale-[1.01]'
            : 'border-border/50 hover:border-primary/50 hover:bg-accent/30',
        )}
      >
        <Upload className={cn('h-8 w-8 mb-2', dragOver ? 'text-primary' : 'text-muted-foreground')} />
        <p className="text-sm font-medium">{label}</p>
        {hint && <p className="text-xs text-muted-foreground mt-1">{hint}</p>}
        <input
          type="file"
          accept={accept}
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) validateAndSet(f)
            e.target.value = ''
          }}
        />
      </label>
      {error && <p className="mt-1.5 text-xs text-red-400">{error}</p>}
    </div>
  )
}
