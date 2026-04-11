import type {
  AssetResponse,
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
  PresetListResponse,
  PresetProfile,
  ReactorResult,
  ServicesStatus,
  StudioTool,
  TTSProviderHealth,
  TTSProviderListResponse,
  TTSResult,
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

export async function fetchVoiceAssets(): Promise<AssetResponse> {
  return request('/api/v1/assets/voices')
}

export async function fetchTTSProviders(includeDisabled = true): Promise<TTSProviderListResponse> {
  const suffix = includeDisabled ? '?include_disabled=true' : ''
  return request(`/api/v1/tts/providers${suffix}`)
}

export async function fetchTTSProviderHealth(providerId: string): Promise<TTSProviderHealth> {
  return request(`/api/v1/tts/providers/${encodeURIComponent(providerId)}/health`)
}

export async function fetchTTSJob(jobId: string): Promise<JobRecord> {
  return request(`/api/v1/tts/jobs/${jobId}`)
}

export async function fetchTTSHistory(params?: {
  provider?: string
  limit?: number
}): Promise<HistoryResponse> {
  const query = new URLSearchParams()
  if (params?.provider) query.set('provider', params.provider)
  if (params?.limit) query.set('limit', String(params.limit))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return request(`/api/v1/tts/history${suffix}`)
}

export async function fetchLoraAssets(): Promise<AssetResponse> {
  return request('/api/v1/assets/loras')
}

export async function fetchPresets(tool?: StudioTool): Promise<PresetListResponse> {
  const suffix = tool ? `?tool=${encodeURIComponent(tool)}` : ''
  return request(`/api/v1/presets${suffix}`)
}

export async function savePreset(payload: {
  name: string
  tool: StudioTool
  state: Record<string, unknown>
}): Promise<PresetProfile> {
  return request('/api/v1/presets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function deletePreset(id: string): Promise<{ deleted: boolean }> {
  return request(`/api/v1/presets/${id}`, { method: 'DELETE' })
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

export async function submitTTSJob(payload: FormData | Record<string, unknown>): Promise<TTSResult> {
  const isFormData = payload instanceof FormData
  const res = await fetch('/api/v1/tts/jobs', {
    method: 'POST',
    headers: isFormData ? undefined : { 'Content-Type': 'application/json' },
    body: isFormData ? payload : JSON.stringify(payload),
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new ApiError(res.status, body || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function submitTTS(formData: FormData): Promise<TTSResult> {
  return submitTTSJob(formData)
}

export async function submitLivePortrait(formData: FormData): Promise<{ job_id: string }> {
  const res = await fetch('/api/v1/liveportrait/animate', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new ApiError(res.status, body || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function submitReactor(params: {
  prompt: string
  negative_prompt?: string
  lora_path?: string
  lora_strength?: number
  workflow?: Record<string, unknown>
  parameters?: Record<string, unknown>
}): Promise<ReactorResult> {
  const res = await fetch('/api/v1/reactor/generate', {
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

export function outputUrl(path: string, cacheBust?: string): string {
  const suffix = cacheBust ? `?v=${encodeURIComponent(cacheBust)}` : ''
  return `/api/v1/outputs/${path}${suffix}`
}

export function historyDownloadUrl(historyId: string): string {
  return `/api/v1/history/${historyId}/download`
}
