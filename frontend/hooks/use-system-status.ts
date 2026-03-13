'use client'

import { useState, useEffect, useCallback } from 'react'
import type { MemoryStatus, ServicesStatus } from '@/lib/types'
import { fetchMemory, fetchServices } from '@/lib/api'

export function useSystemStatus(pollInterval = 5000) {
  const [memory, setMemory] = useState<MemoryStatus | null>(null)
  const [services, setServices] = useState<ServicesStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [mem, svc] = await Promise.all([fetchMemory(), fetchServices()])
      setMemory(mem)
      setServices(svc)
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch status')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, pollInterval)
    return () => clearInterval(interval)
  }, [refresh, pollInterval])

  return { memory, services, loading, error, refresh }
}
