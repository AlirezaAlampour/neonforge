import type {
  ComfyUIAsset,
  ComfyUIAssetResponse,
  ComfyUIJobDetail,
  ComfyUIJobSubmitResult,
  ComfyUIModelsResponse,
  ComfyUITemplate,
  ComfyUITemplateListResponse,
  HistoryResponse,
  JobRecord,
  MemoryStatus,
  ServicesStatus,
  WorkloadLifecycleStatus,
} from './types'

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

export async function fetchWorkloads(): Promise<WorkloadLifecycleStatus> {
  return request('/workloads/status')
}

export async function fetchJob(jobId: string): Promise<JobRecord> {
  return request(`/jobs/${jobId}`)
}

export async function fetchHistory(params?: {
  service?: string
  limit?: number
}): Promise<HistoryResponse> {
  const query = new URLSearchParams()
  if (params?.service) query.set('service', params.service)
  if (params?.limit) query.set('limit', String(params.limit))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return request(`/api/v1/history${suffix}`)
}

export async function deleteHistoryItem(id: string): Promise<{ deleted: boolean; file_deleted: boolean }> {
  return request(`/api/v1/history/${id}`, { method: 'DELETE' })
}

export async function fetchComfyUITemplates(): Promise<ComfyUITemplateListResponse> {
  return request('/api/v1/comfyui/templates')
}

export async function fetchComfyUITemplate(templateId: string): Promise<ComfyUITemplate> {
  return request(`/api/v1/comfyui/templates/${templateId}`)
}

export async function fetchComfyUIModels(): Promise<ComfyUIModelsResponse> {
  return request('/api/v1/comfyui/models')
}

export async function fetchComfyUIAssets(kind?: 'image' | 'video'): Promise<ComfyUIAssetResponse> {
  const suffix = kind ? `?kind=${encodeURIComponent(kind)}` : ''
  return request(`/api/v1/comfyui/assets${suffix}`)
}

export async function uploadComfyUIAsset(file: File, kind: 'image' | 'video'): Promise<ComfyUIAsset> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('kind', kind)

  const res = await fetch('/api/v1/comfyui/assets/upload', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new ApiError(res.status, body || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function deleteComfyUIAsset(assetId: string): Promise<{ deleted: boolean; file_deleted: boolean }> {
  return request(`/api/v1/comfyui/assets/${assetId}`, { method: 'DELETE' })
}

export async function submitComfyUIJob(payload: {
  template_id: string
  inputs: Record<string, string>
  params: Record<string, unknown>
  debug_dump?: boolean
}): Promise<ComfyUIJobSubmitResult> {
  return request('/api/v1/comfyui/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function fetchComfyUIJob(jobId: string): Promise<ComfyUIJobDetail> {
  return request(`/api/v1/comfyui/jobs/${jobId}`)
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

export function outputUrl(path: string, cacheBust?: string): string {
  const suffix = cacheBust ? `?v=${encodeURIComponent(cacheBust)}` : ''
  return `/api/v1/outputs/${path}${suffix}`
}

export function historyDownloadUrl(historyId: string): string {
  return `/api/v1/history/${historyId}/download`
}
