import { request } from './api'
import type { ComfyUIAsset, HostResourceStatus } from './types'

export type Point = { x: number; y: number }
export type Tool = 'arrow' | 'path' | 'orbit' | 'waypoint' | 'subject' | 'entry' | 'exit'
export type Annotation = { id: string; reference_id: string; kind: Tool; points: Point[]; label: string }
export type ShotRequest = {
  prompt: string; reference_ids: string[]; annotations: Annotation[]
  mode: 'auto' | 'manual'; preset: 'professional' | 'intimate' | 'reveal' | 'action'
  duration: 2 | 3 | 5 | 10 | 15 | 30; orientation: 'vertical' | 'horizontal'; workflow: 'reference' | 'image'; seed: number
  director?: 'local' | 'opus' | 'auto'; engine?: 'h3' | 'grok'; plan_id?: string | null
  pipeline?: 'prompt' | '3d'; parent_plan_id?: string | null; revision?: string
}
export type ShotSpec = {
  version: '0.1'; request: ShotRequest; reference_ids: string[]
  planner?: string
  camera: { movement: string; targets: string[]; paths: Annotation[]; easing: string }
  timing: { start: number; end: number; action: string }[]
  composition: string; subject_constraints: string[]; width: number; height: number; frames: number; fps: number
  renderer_prompt: string; conditioning: 'prompt-guidance' | 'reference-video'; notes: string[]
  scene?: { description: string; reference_analysis: string; limitations: string[]; entities: {id: string; label: string; geometry: string; clip: string}[]; camera: {mode: string; distance: number} } | null
}
export type MotionJob = {
  job_id: string; service?: string; status: string; message: string; error?: string; created_at: string
  preview_url?: string; download_url?: string; shot: ShotSpec; prompt_id?: string
  completed_segments?: number; total_segments?: number
  blender_preview_url?: string | null
}
export type ScenePreview = { id: string; status: string; message?: string; error?: string; preview_url?: string; first_frame_url?: string; export_url?: string; prompt_url?: string; script_url?: string }
export type Capabilities = {
  h3: { available: boolean; render_ready: boolean; can_recover_memory?: boolean; reason?: string; busy?: boolean; available_gb: number; minimum_gb: number; workflows?: Record<string, { available: boolean; reason?: string }> }
  grok: { available: boolean; reason: string; authorization_url?: string | null; authorization_required?: boolean; manual_import?: boolean }
  planner: { active: string; opus_available?: boolean; subscription?: string; reason: string }
  blender?: { available: boolean; reason?: string; assets?: {id: string; label: string; rigged: boolean}[] }
  resources?: HostResourceStatus | null
}
export const motionApi = {
  capabilities: () => request<Capabilities>('/api/v1/motion-director/capabilities'),
  plan: (body: ShotRequest) => request<ShotSpec>('/api/v1/motion-director/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  buildPreview: (id: string) => request<ScenePreview>(`/api/v1/motion-director/plans/${id}/preview`, { method: 'POST' }),
  preview: (id: string) => request<ScenePreview>(`/api/v1/motion-director/plans/${id}/preview`),
  revise: (id: string, instruction: string) => request<ShotSpec>(`/api/v1/motion-director/plans/${id}/revise`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({instruction})}),
  submit: (body: ShotRequest) => request<MotionJob>('/api/v1/motion-director/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  jobs: () => request<{ items: MotionJob[] }>('/api/v1/motion-director/jobs'),
  upload: async (file: File) => {
    const form = new FormData(); form.append('file', file)
    return request<ComfyUIAsset>('/api/v1/motion-director/references', { method: 'POST', body: form })
  },
  reconcile: (jobId: string) => request<MotionJob>(`/api/v1/motion-director/jobs/${jobId}/reconcile`, { method: 'POST' }),
  recoverMemory: () => request('/api/v1/motion-director/memory/recover', { method: 'POST' }),
  importGrok: async (file: File, referenceJobId?: string, referencePlanId?: string) => {
    const body = new FormData(); body.append('file', file)
    if (referenceJobId) body.append('reference_job_id', referenceJobId)
    if (referencePlanId) body.append('reference_plan_id', referencePlanId)
    return request<MotionJob>('/api/v1/motion-director/imports/grok', { method: 'POST', body })
  },
}

export function annotationId(): string {
  // Private Tailscale HTTP origins do not expose crypto.randomUUID on phones.
  // getRandomValues remains available there; preserve UUID v4 entropy/format.
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function normalizedPoint(clientX: number, clientY: number, rect: { left: number; top: number; width: number; height: number }): Point {
  return { x: Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width))), y: Math.max(0, Math.min(1, (clientY - rect.top) / Math.max(1, rect.height))) }
}

export function finishAnnotation(kind: Tool, points: Point[]): Point[] | null {
  if (['subject', 'entry', 'exit', 'waypoint'].includes(kind)) return points.slice(0, 1)
  if (points.length < 2 || Math.hypot(points[0].x - points.at(-1)!.x, points[0].y - points.at(-1)!.y) < .005 && kind !== 'path') return null
  return kind === 'path' ? points.slice(0, 256) : [points[0], points.at(-1)!]
}

export function downloadMotionJSON(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
