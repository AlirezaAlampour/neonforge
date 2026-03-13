'use client'

import { CheckCircle2, XCircle, Loader2, Clock, X, Download, Play } from 'lucide-react'
import type { JobRecord } from '@/lib/types'
import { cn } from '@/lib/utils'
import { outputUrl } from '@/lib/api'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Progress } from './ui/progress'

interface JobTrackerProps {
  jobs: JobRecord[]
  onDismiss: (jobId: string) => void
}

const statusConfig = {
  queued: {
    icon: Clock,
    label: 'Queued',
    badge: 'warning' as const,
    animate: false,
  },
  running: {
    icon: Loader2,
    label: 'Processing',
    badge: 'default' as const,
    animate: true,
  },
  completed: {
    icon: CheckCircle2,
    label: 'Completed',
    badge: 'success' as const,
    animate: false,
  },
  failed: {
    icon: XCircle,
    label: 'Failed',
    badge: 'danger' as const,
    animate: false,
  },
}

export function JobTracker({ jobs, onDismiss }: JobTrackerProps) {
  if (jobs.length === 0) return null

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium text-muted-foreground">Active Jobs</h3>
      {jobs.map((job) => {
        const config = statusConfig[job.status]
        const Icon = config.icon
        const isMedia = job.result_path?.match(/\.(mp4|wav|webm|mp3)$/)
        const isAudio = job.result_path?.match(/\.(wav|mp3|webm)$/)

        return (
          <div
            key={job.job_id}
            className={cn(
              'rounded-lg border border-border/50 bg-card/50 p-4 transition-all duration-300',
              job.status === 'running' && 'ring-1 ring-primary/30',
            )}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <Icon
                  className={cn(
                    'h-5 w-5 shrink-0',
                    job.status === 'completed' && 'text-emerald-400',
                    job.status === 'failed' && 'text-red-400',
                    job.status === 'running' && 'text-primary animate-spin',
                    job.status === 'queued' && 'text-amber-400',
                  )}
                />
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate">
                    {job.service} &middot;{' '}
                    <span className="font-mono text-xs text-muted-foreground">
                      {job.job_id.slice(0, 8)}
                    </span>
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <Badge variant={config.badge}>{config.label}</Badge>
                {(job.status === 'completed' || job.status === 'failed') && (
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => onDismiss(job.job_id)}>
                    <X className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            </div>

            {job.status === 'running' && (
              <div className="mt-3">
                <Progress indeterminate />
              </div>
            )}

            {job.status === 'failed' && job.error && (
              <p className="mt-2 text-xs text-red-400/80 truncate">{job.error}</p>
            )}

            {job.status === 'completed' && job.result_path && (
              <div className="mt-3 space-y-2">
                {isAudio && (
                  <audio controls className="w-full h-8 [&::-webkit-media-controls-panel]:bg-secondary rounded">
                    <source src={outputUrl(job.result_path)} />
                  </audio>
                )}
                {isMedia && !isAudio && (
                  <video
                    controls
                    className="w-full rounded-lg border border-border/30 max-h-48 bg-black"
                    src={outputUrl(job.result_path)}
                  />
                )}
                <div className="flex gap-2">
                  <a
                    href={outputUrl(job.result_path)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 text-xs text-primary hover:text-primary/80 transition-colors"
                  >
                    <Play className="h-3 w-3" /> Open
                  </a>
                  <a
                    href={outputUrl(job.result_path)}
                    download
                    className="inline-flex items-center gap-1.5 text-xs text-primary hover:text-primary/80 transition-colors"
                  >
                    <Download className="h-3 w-3" /> Download
                  </a>
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
