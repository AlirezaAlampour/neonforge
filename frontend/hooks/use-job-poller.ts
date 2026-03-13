'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import type { JobRecord } from '@/lib/types'
import { fetchJob } from '@/lib/api'

export function useJobPoller() {
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
      ...prev,
    ])

    const poll = async () => {
      try {
        const data = await fetchJob(jobId)
        setJobs(prev => prev.map(j => (j.job_id === jobId ? data : j)))
        if (data.status === 'completed' || data.status === 'failed') {
          const interval = intervalsRef.current.get(jobId)
          if (interval) {
            clearInterval(interval)
            intervalsRef.current.delete(jobId)
          }
        }
      } catch {
        // Will retry on next interval
      }
    }

    poll()
    const interval = setInterval(poll, 2000)
    intervalsRef.current.set(jobId, interval)
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
    return () => {
      intervalsRef.current.forEach(interval => clearInterval(interval))
    }
  }, [])

  return { jobs, trackJob, dismissJob }
}
