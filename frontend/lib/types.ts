export interface MemoryStatus {
  total_gb: number
  available_gb: number
  used_gb: number
  used_pct: number
  swap_total_gb: number
  swap_free_gb: number
  swap_used_gb: number
  swap_used_pct: number
  thresholds: {
    warn_pct: number
    hard_pct: number
    reserve_heavy_gb: number
    reserve_medium_gb: number
    reserve_light_gb: number
  }
}

export interface ServiceStatus {
  alive: boolean
  ready: boolean
  last_activity: number | null
}

export type ServicesStatus = Record<string, ServiceStatus>

export type JobState = 'queued' | 'running' | 'completed' | 'failed'

export interface JobRecord {
  job_id: string
  service: string
  status: JobState
  created_at: string
  started_at?: string | null
  completed_at?: string | null
  result_path?: string | null
  error?: string | null
}

export interface TTSResult {
  output_path: string
  sample_rate: number
  duration: number
  processing_time: number
  job_id: string
}

export interface Wan21Result {
  output_path: string
  processing_time: number
  num_frames: number
  resolution: string
  uma_used_gb: number
  job_id: string
}

export interface LipSyncResult {
  output_path: string
  processing_time: number
  backend: string
  job_id: string
}

export interface GenerationHistoryItem {
  id: string
  job_id: string | null
  service: string
  model_used: string | null
  prompt: string | null
  parameters: Record<string, unknown>
  timestamp: string
  output_path: string
  download_url: string
  preview_url: string
}

export interface HistoryResponse {
  items: GenerationHistoryItem[]
}

export interface AssetItem {
  name: string
  path: string
  relative_path: string
  size_bytes: number
  modified_at: string
}

export interface AssetResponse {
  root: string
  items: AssetItem[]
}

export type StudioTool = 'f5tts' | 'liveportrait' | 'reactor'

export interface PresetProfile {
  id: string
  name: string
  tool: StudioTool
  state: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface PresetListResponse {
  items: PresetProfile[]
}

export interface ReactorResult {
  job_id: string
  output_path?: string | null
  queue_response: unknown
}
