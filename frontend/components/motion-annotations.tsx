'use client'

import { useRef, useState, type PointerEvent } from 'react'
import { finishAnnotation, normalizedPoint, type Annotation, type Point, type Tool } from '@/lib/motion-director'

export function AnnotationCanvas({ referenceId, annotations, tool, label, onAdd }: {
  referenceId: string; annotations: Annotation[]; tool: Tool; label: string; onAdd: (annotation: Annotation) => void
}) {
  const [stroke, setStroke] = useState<Point[]>([])
  const [aspect, setAspect] = useState(1)
  const active = useRef<{ pointer: number; points: Point[]; tool: Tool; reference: string } | null>(null)
  const point = (e: PointerEvent<SVGSVGElement>) => normalizedPoint(e.clientX, e.clientY, e.currentTarget.getBoundingClientRect())
  const finish = (e: PointerEvent<SVGSVGElement>) => {
    const drawing = active.current
    if (!drawing || drawing.pointer !== e.pointerId) return
    drawing.points.push(point(e))
    const points = finishAnnotation(drawing.tool, drawing.points)
    if (points) onAdd({ id: crypto.randomUUID(), reference_id: drawing.reference, kind: drawing.tool, points, label: label.trim() })
    active.current = null; setStroke([])
  }
  const drawn = annotations.filter(a => a.reference_id === referenceId)
  const paint = (a: Annotation, index: number) => {
    const first = a.points[0], last = a.points.at(-1)!
    const x = first.x * 1000, y = first.y * 1000
    const color = ['subject', 'entry', 'exit'].includes(a.kind) ? '#fbbf24' : '#38bdf8'
    return <g key={a.id} stroke={color} strokeWidth={3} fill="none" style={{ pointerEvents: 'none' }}>
      {a.kind === 'orbit' ? <ellipse cx={x} cy={y} rx={Math.max(10, Math.abs(last.x - first.x) * 1000)} ry={Math.max(10, Math.abs(last.y - first.y) * 1000)} vectorEffect="non-scaling-stroke" /> :
        ['arrow', 'path'].includes(a.kind) ? <polyline points={a.points.map(p => `${p.x * 1000},${p.y * 1000}`).join(' ')} markerEnd="url(#motion-arrow)" vectorEffect="non-scaling-stroke" /> :
          <circle cx={x} cy={y} r={a.kind === 'subject' ? 35 : 12} vectorEffect="non-scaling-stroke" />}
      <text x={Math.min(x + 16, 820)} y={Math.max(30, y - 18)} fill={color} stroke="none" fontSize={28}>{a.label || `${a.kind} ${index + 1}`}</text>
    </g>
  }
  return <div className="flex justify-center rounded-xl border border-white/10 bg-black/30 p-2">
    <div className="relative" style={{ width: `min(100%, ${Math.round(55 * aspect)}vh)` }}>
      {/* Original bytes stay on the asset service; only the SVG overlay changes. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={`/api/v1/motion-director/references/${referenceId}`} alt="Selected cinematic reference" className="block h-auto w-full rounded-lg" draggable={false} onLoad={e => setAspect(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)} />
      <svg aria-label={`Draw ${tool} on reference image`} role="img" viewBox="0 0 1000 1000" preserveAspectRatio="none" className="absolute inset-0 h-full w-full touch-none select-none rounded-lg"
        onPointerDown={e => {
          if (active.current || e.button !== 0) return
          e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId)
          const p = point(e); active.current = { pointer: e.pointerId, points: [p], tool, reference: referenceId }; setStroke([p])
        }}
        onPointerMove={e => {
          const drawing = active.current
          if (!drawing || drawing.pointer !== e.pointerId) return
          const p = point(e), previous = drawing.points.at(-1)!
          if (Math.hypot(p.x - previous.x, p.y - previous.y) < .006) return
          if (drawing.tool === 'path' && drawing.points.length < 254) drawing.points.push(p)
          else if (drawing.points.length === 1) drawing.points.push(p)
          else drawing.points[drawing.points.length - 1] = p
          setStroke([...drawing.points])
        }} onPointerUp={finish} onPointerCancel={() => { active.current = null; setStroke([]) }} onLostPointerCapture={() => { active.current = null; setStroke([]) }}>
        <defs><marker id="motion-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#38bdf8" stroke="none" /></marker></defs>
        {drawn.map(paint)}
        {stroke.length > 0 && paint({ id: 'draft', reference_id: referenceId, kind: tool, points: stroke, label }, drawn.length)}
      </svg>
    </div>
  </div>
}
