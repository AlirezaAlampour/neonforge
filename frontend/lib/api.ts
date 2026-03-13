import type { MemoryStatus, ServicesStatus, JobRecord } from './types'

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new ApiError(res.status, body || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function fetchMemory(): Promise<MemoryStatus> {
  return request('/memory')
}

export async function fetchServices(): Promise<ServicesStatus> {
  return request('/services/status')
}

export async function fetchJob(jobId: string): Promise<JobRecord> {
  return request(`/jobs/${jobId}`)
}

export async function submitTTS(formData: FormData): Promise<{ job_id: string }> {
  const res = await fetch('/api/v1/tts/synthesize-with-audio', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new ApiError(res.status, body || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function submitWan21(params: {
  prompt: string
  negative_prompt?: string
  num_frames?: number
  width?: number
  height?: number
  num_inference_steps?: number
  guidance_scale?: number
  seed?: number
}): Promise<{ job_id: string }> {
  const res = await fetch('/api/v1/wan21/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new ApiError(res.status, body || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function submitLipSync(formData: FormData): Promise<{ job_id: string }> {
  const res = await fetch('/api/v1/lipsync/sync', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new ApiError(res.status, body || `HTTP ${res.status}`)
  }
  return res.json()
}

export function outputUrl(path: string): string {
  return `/api/v1/outputs/${path}`
}
