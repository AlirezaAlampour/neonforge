'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { usePathname } from 'next/navigation'
import type { JobRecord } from '@/lib/types'
import { fetchJob } from '@/lib/api'

export function useJobPoller() {
  const pathname = usePathname()
  const storageKey = `neonforge-media-jobs:${pathname}`
  const [restored, setRestored] = useState(false)
  const [jobs, setJobs] = useState<JobRecord[]>([])
  const intervalsRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map())

  const trackJob = useCallback((jobId: string, service: string) => {
    setJobs(prev => [
      {
        job_id: jobId,
        service,
        status: 'queued',
        created_at: new Date().toISOString(),
      },
      ...prev.filter((job) => job.job_id !== jobId),
    ])

    const poll = async () => {
      try {
        const data = await fetchJob(jobId)
        setJobs(prev => prev.map(j => (j.job_id === jobId ? { ...data, service } : j)))
        if (data.status === 'completed' || data.status === 'failed') {
          const interval = intervalsRef.current.get(jobId)
          if (interval) {
            clearInterval(interval)
            intervalsRef.current.delete(jobId)
          }
        }
      } catch (error) {
        if (error instanceof Error && 'status' in error && error.status === 404) {
          setJobs(prev => prev.map(job => job.job_id === jobId ? { ...job, status: 'failed', error: 'This job is no longer in the recent status cache. Its saved output is retained in history.' } : job))
          const interval = intervalsRef.current.get(jobId)
          if (interval) clearInterval(interval)
          intervalsRef.current.delete(jobId)
        }
      }
    }

    const previous = intervalsRef.current.get(jobId)
    if (previous) clearInterval(previous)
    const interval = setInterval(poll, 2000)
    intervalsRef.current.set(jobId, interval)
    void poll()
  }, [])

  const dismissJob = useCallback((jobId: string) => {
    const interval = intervalsRef.current.get(jobId)
    if (interval) {
      clearInterval(interval)
      intervalsRef.current.delete(jobId)
    }
    setJobs(prev => prev.filter(j => j.job_id !== jobId))
  }, [])

  useEffect(() => {
    try {
      const saved: Array<{ job_id: string; service: string }> = JSON.parse(localStorage.getItem(storageKey) || '[]')
      saved.slice(0, 20).reverse().forEach((job) => {
        if (typeof job.job_id === 'string' && typeof job.service === 'string') trackJob(job.job_id, job.service)
      })
    } catch { /* Storage is optional; generation remains available. */ }
    setRestored(true)
    return () => {
      intervalsRef.current.forEach(interval => clearInterval(interval))
      intervalsRef.current.clear()
    }
  }, [storageKey, trackJob])

  useEffect(() => {
    if (!restored) return
    try { localStorage.setItem(storageKey, JSON.stringify(jobs.slice(0, 20).map(({ job_id, service }) => ({ job_id, service })))) }
    catch { /* Private browsing or full storage must not block a result. */ }
  }, [jobs, restored, storageKey])

  return { jobs, trackJob, dismissJob }
}
