'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Download, History, RefreshCw, Trash2 } from 'lucide-react'
import { deleteHistoryItem, fetchHistory, historyDownloadUrl, outputUrl } from '@/lib/api'
import type { GenerationHistoryItem } from '@/lib/types'
import { Badge } from './ui/badge'
import { Button } from './ui/button'

interface HistoryPaneProps {
  services?: string[]
  limit?: number
  pollMs?: number
}

function mediaKind(path: string): 'audio' | 'video' | 'other' {
  const lower = path.toLowerCase()
  if (lower.endsWith('.mp4') || lower.endsWith('.mov') || lower.endsWith('.mkv') || lower.endsWith('.webm')) {
    return 'video'
  }
  if (lower.endsWith('.wav') || lower.endsWith('.mp3') || lower.endsWith('.ogg') || lower.endsWith('.m4a')) {
    return 'audio'
  }
  return 'other'
}

export function HistoryPane({ services, limit = 100, pollMs = 8000 }: HistoryPaneProps) {
  const [items, setItems] = useState<GenerationHistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const serviceSet = useMemo(() => new Set(services ?? []), [services])

  const load = useCallback(async () => {
    try {
      const response = await fetchHistory({ limit })
      const filtered = services?.length
        ? response.items.filter((item) => serviceSet.has(item.service))
        : response.items
      setItems(filtered)
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load generation history')
    } finally {
      setLoading(false)
    }
  }, [limit, serviceSet, services?.length])

  useEffect(() => {
    load()
    const timer = setInterval(load, pollMs)
    return () => clearInterval(timer)
  }, [load, pollMs])

  const remove = useCallback(
    async (id: string) => {
      setDeletingId(id)
      try {
        await deleteHistoryItem(id)
        setItems((prev) => prev.filter((item) => item.id !== id))
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to delete history item')
      } finally {
        setDeletingId(null)
      }
    },
    [],
  )

  return (
    <div className="rounded-xl border border-border/50 bg-card/40 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <History className="h-4 w-4 text-primary" />
          Gallery / History
        </h3>
        <Button variant="ghost" size="sm" className="h-7 px-2" onClick={load}>
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
      </div>

      {error && <p className="mb-3 text-xs text-red-400">{error}</p>}
      {loading && <p className="text-xs text-muted-foreground">Loading history...</p>}
      {!loading && items.length === 0 && <p className="text-xs text-muted-foreground">No saved generations yet.</p>}

      <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
        {items.map((item) => {
          const kind = mediaKind(item.output_path)

          return (
            <div key={item.id} className="rounded-lg border border-border/50 bg-background/40 p-3">
              <div className="mb-2 flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium">{item.model_used || item.service}</p>
                  <p className="text-[11px] text-muted-foreground">{new Date(item.timestamp).toLocaleString()}</p>
                </div>
                <Badge variant="default" className="shrink-0">
                  {item.service}
                </Badge>
              </div>

              {item.prompt && (
                <p className="mb-2 line-clamp-2 text-xs text-muted-foreground">{item.prompt}</p>
              )}

              {kind === 'audio' && (
                <audio controls className="mb-2 h-8 w-full [&::-webkit-media-controls-panel]:bg-secondary rounded">
                  <source src={outputUrl(item.output_path)} />
                </audio>
              )}
              {kind === 'video' && (
                <video
                  controls
                  className="mb-2 max-h-40 w-full rounded border border-border/40 bg-black"
                  src={outputUrl(item.output_path)}
                />
              )}

              <div className="flex items-center gap-2">
                <a
                  href={historyDownloadUrl(item.id)}
                  className="inline-flex items-center gap-1 text-xs text-primary hover:text-primary/80"
                >
                  <Download className="h-3 w-3" />
                  Download
                </a>
                <button
                  type="button"
                  onClick={() => remove(item.id)}
                  disabled={deletingId === item.id}
                  className="inline-flex items-center gap-1 text-xs text-red-400 hover:text-red-300 disabled:opacity-50"
                >
                  <Trash2 className="h-3 w-3" />
                  {deletingId === item.id ? 'Deleting...' : 'Delete'}
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
