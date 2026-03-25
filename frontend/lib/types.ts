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
  debug_dump_path?: string | null
  message?: string | null
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

export interface ComfyUIValidationItem {
  node_id: string
  node_type: string
  input_name: string
  category: string
  value: string
  filename: string
  satisfied_by_root?: string
  satisfied_by_path?: string
  satisfied_by_source?: string
  matches?: Array<{
    relative_path: string
    root: string
    path?: string
    source?: string
    container?: string | null
  }>
}

export interface ComfyUIModelValidation {
  template_id: string
  available: ComfyUIValidationItem[]
  missing: ComfyUIValidationItem[]
  warnings: string[]
}

export interface ComfyUITemplateInputSpec {
  id: string
  label: string
  kind: 'image' | 'video'
  description: string
  accepted_extensions: string[]
  max_size_mb: number
}

export interface ComfyUITemplateParamSpec {
  id: string
  label: string
  type: 'integer' | 'number' | 'string' | 'boolean'
  description: string
  default: number | string | boolean | null
  min?: number | null
  max?: number | null
  step?: number | null
}

export interface ComfyUITemplateOutputSpec {
  node_id?: string | null
  node_type?: string | null
  media_keys: string[]
}

export interface ComfyUITemplate {
  id: string
  name: string
  description: string
  category: string
  workflow_file?: string
  workflow_format: string
  gpu_tier: string
  required_inputs: ComfyUITemplateInputSpec[]
  optional_params: ComfyUITemplateParamSpec[]
  runtime_mappings: Array<{
    input_id: string
    node_id: string
    input_name: string
    value_source: string
    value?: unknown
  }>
  output_type: string
  output: ComfyUITemplateOutputSpec
  validation: ComfyUIModelValidation
}

export interface ComfyUITemplateListResponse {
  items: ComfyUITemplate[]
}

export interface ComfyUIAsset {
  id: string
  kind: 'image' | 'video'
  original_filename: string
  stored_filename: string
  relative_path: string
  content_type?: string | null
  size_bytes: number
  created_at: string
}

export interface ComfyUIAssetResponse {
  root: string
  items: ComfyUIAsset[]
}

export interface ComfyUIModelsResponse {
  roots: string[]
  scanned_roots: Array<{
    path: string
    resolved_path: string
    exists: boolean
    is_dir: boolean
    item_count: number
    source?: string
    container?: string | null
    error?: string | null
  }>
  items: Array<{
    filename: string
    path: string
    relative_path: string
    root: string
    size_bytes: number
    modified_at: string
    source?: string
    container?: string | null
  }>
  templates: Array<{
    template_id: string
    template_name: string
    validation: ComfyUIModelValidation
  }>
}

export interface ComfyUIJobSubmitResult {
  job_id: string
  status: JobState
  template_id: string
  validation: ComfyUIModelValidation
  debug_dump_path?: string | null
}

export interface ComfyUIJobDetail {
  job_id: string
  template_id: string
  template_name: string
  status: JobState
  message?: string | null
  prompt_id?: string | null
  created_at: string
  started_at?: string | null
  completed_at?: string | null
  result_path?: string | null
  debug_dump_path?: string | null
  history_id?: string | null
  error?: string | null
  inputs: Record<string, string>
  params: Record<string, unknown>
  validation: ComfyUIModelValidation
}

export type StudioTool = 'f5tts' | 'liveportrait' | 'reactor' | 'character-swap'

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
