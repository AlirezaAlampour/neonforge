import { request } from './api'
import type { ComfyUIAsset, HostResourceStatus } from './types'

export type Point = { x: number; y: number }
export type Tool = 'arrow' | 'path' | 'orbit' | 'waypoint' | 'subject' | 'entry' | 'exit'
export type Annotation = { id: string; reference_id: string; kind: Tool; points: Point[]; label: string }
export type ShotRequest = {
  prompt: string; reference_ids: string[]; annotations: Annotation[]
  mode: 'auto' | 'manual'; preset: 'professional' | 'intimate' | 'reveal' | 'action'
  duration: 2 | 3 | 5; orientation: 'vertical' | 'horizontal'; workflow: 'reference' | 'image'; seed: number
  director?: 'local' | 'opus'; plan_id?: string | null
}
export type ShotSpec = {
  version: '0.1'; request: ShotRequest; reference_ids: string[]
  planner?: string
  camera: { movement: string; targets: string[]; paths: Annotation[]; easing: string }
  timing: { start: number; end: number; action: string }[]
  composition: string; subject_constraints: string[]; width: number; height: number; frames: number; fps: number
  renderer_prompt: string; conditioning: 'prompt-guidance'; notes: string[]
}
export type MotionJob = {
  job_id: string; status: string; message: string; error?: string; created_at: string
  preview_url?: string; download_url?: string; shot: ShotSpec; prompt_id?: string
}
export type Capabilities = {
  h3: { available: boolean; render_ready: boolean; reason?: string; busy?: boolean; available_gb: number; minimum_gb: number; workflows?: Record<string, { available: boolean; reason?: string }> }
  grok: { available: boolean; reason: string }
  planner: { active: string; opus_available?: boolean; subscription?: string; reason: string }
  resources?: HostResourceStatus | null
}
export const motionApi = {
  capabilities: () => request<Capabilities>('/api/v1/motion-director/capabilities'),
  plan: (body: ShotRequest) => request<ShotSpec>('/api/v1/motion-director/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  submit: (body: ShotRequest) => request<MotionJob>('/api/v1/motion-director/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  jobs: () => request<{ items: MotionJob[] }>('/api/v1/motion-director/jobs'),
  upload: async (file: File) => {
    const form = new FormData(); form.append('file', file)
    return request<ComfyUIAsset>('/api/v1/motion-director/references', { method: 'POST', body: form })
  },
  reconcile: (jobId: string) => request<MotionJob>(`/api/v1/motion-director/jobs/${jobId}/reconcile`, { method: 'POST' }),
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
