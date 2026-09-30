'use client'

import { CheckCircle2, XCircle, Loader2, X, Download, RotateCcw, Copy } from 'lucide-react'
import type { JobRecord } from '@/lib/types'
import { outputUrl } from '@/lib/api'
import { Button } from './ui/button'
import { Progress } from './ui/progress'

interface JobTrackerProps {
  jobs: JobRecord[]
  onDismiss: (jobId: string) => void
  onReuse?: (jobId: string) => void
  onRegenerate?: (jobId: string) => void
  actionsDisabled?: boolean
  canReuse?: (jobId: string) => boolean
}
const labels = { queued: 'Preparing', preparing: 'Preparing', loading: 'Loading', running: 'Generating', finalizing: 'Finalizing', completed: 'Complete', failed: 'Could not finish' }

export function JobTracker({ jobs, onDismiss, onReuse, onRegenerate, actionsDisabled, canReuse }: JobTrackerProps) {
  if (!jobs.length) return null
  return <section className="space-y-8" aria-label="Results" aria-live="polite">
    {jobs.map((job) => {
      const done = job.status === 'completed'
      const failed = job.status === 'failed'
      const active = !done && !failed
      const isAudio = /\.(wav|mp3|flac|ogg)$/i.test(job.result_path || '')
      const url = job.result_path ? outputUrl(job.result_path, job.completed_at ?? job.job_id) : undefined
      const elapsed = job.started_at && job.completed_at ? Math.round((Date.parse(job.completed_at) - Date.parse(job.started_at)) / 1000) : null
      return <article key={job.job_id} className="space-y-4 rounded-2xl bg-card/40 p-5">
        <div className="flex items-center gap-3">
          {active ? <Loader2 className="h-4 w-4 animate-spin text-primary" /> : failed ? <XCircle className="h-4 w-4 text-red-400" /> : <CheckCircle2 className="h-4 w-4 text-emerald-400" />}
          <h2 className="text-sm font-medium">{labels[job.status]}</h2>
          {elapsed !== null && <span className="text-xs text-muted-foreground">{elapsed}s</span>}
          {!active && <Button variant="ghost" size="icon" className="ml-auto h-7 w-7" aria-label="Dismiss result" onClick={() => onDismiss(job.job_id)}><X className="h-4 w-4" /></Button>}
        </div>
        {active && <><Progress indeterminate /><p className="text-sm text-muted-foreground">{job.status === 'running' ? 'Creating your result. You can leave this page open while it renders.' : 'Getting everything ready. The first generation can take a little longer.'}</p></>}
        {failed && <p role="alert" className="whitespace-pre-wrap break-words text-sm text-red-300">{job.error || 'Generation stopped before producing a result. Try again or check System Info.'}</p>}
        {done && url && <>{isAudio ? <audio controls className="w-full" src={url} /> : <video controls playsInline className="max-h-[70vh] w-full rounded-xl bg-black" src={url} />}
          <div className="flex flex-wrap items-center gap-3"><a href={url} download className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground"><Download className="h-4 w-4" />Download</a>
            {onReuse && (!canReuse || canReuse(job.job_id)) && <Button variant="ghost" onClick={() => onReuse(job.job_id)}><Copy className="mr-2 h-4 w-4" />Reuse settings</Button>}
            {onRegenerate && (!canReuse || canReuse(job.job_id)) && <Button variant="ghost" disabled={actionsDisabled} onClick={() => onRegenerate(job.job_id)}><RotateCcw className="mr-2 h-4 w-4" />Regenerate</Button>}
          </div></>}
        <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">Generation details</summary><div className="mt-3 space-y-2 break-words"><p>Job: {job.job_id}</p><p>Service: {job.service}</p>{job.message && <p>{job.message}</p>}{job.debug_dump_path && <p>{job.debug_dump_path}</p>}{job.debug_artifacts?.map((artifact) => <a className="mr-3 inline-block text-primary" key={artifact.id} href={outputUrl(artifact.relative_path)} target="_blank" rel="noreferrer">{artifact.label}</a>)}</div></details>
      </article>
    })}
  </section>
}
