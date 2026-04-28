'use client'

import { useSystemStatus } from '@/hooks/use-system-status'
import { cn } from '@/lib/utils'

interface MemoryGaugeProps {
  compact?: boolean
}

export function MemoryGauge({ compact }: MemoryGaugeProps) {
  const { memory } = useSystemStatus(compact ? 10000 : 5000)

  if (!memory) {
    return (
      <div className={cn('animate-pulse', compact ? 'h-9' : 'h-20')}>
        <div className="h-full rounded-md bg-white/[0.04]" />
      </div>
    )
  }

  const pct = memory.used_pct
  const color =
    pct >= memory.thresholds.hard_pct
      ? 'text-red-400'
      : pct >= memory.thresholds.warn_pct
        ? 'text-amber-400'
        : 'text-emerald-400'

  const barColor =
    pct >= memory.thresholds.hard_pct
      ? 'bg-red-500'
      : pct >= memory.thresholds.warn_pct
        ? 'bg-amber-500'
        : 'bg-emerald-500'

  if (compact) {
    return (
      <div className="rounded-lg border border-white/[0.06] bg-[#0f1218] px-2.5 py-2">
        <div className="flex items-center justify-between text-xs">
          <span className="font-medium text-muted-foreground">UMA</span>
          <span className={cn('font-mono font-medium tabular-nums', color)}>{pct}%</span>
        </div>
        <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className={cn('h-full rounded-full transition-all duration-700 ease-out', barColor)}
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="mt-1 text-[10px] text-muted-foreground/70">
          {memory.used_gb} / {memory.total_gb} GB
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Arc gauge */}
      <div className="flex flex-col items-center">
        <div className="relative w-40 h-20 overflow-hidden">
          <svg viewBox="0 0 120 60" className="w-full h-full">
            {/* Background arc */}
            <path
              d="M 10 55 A 50 50 0 0 1 110 55"
              fill="none"
              stroke="hsl(var(--secondary))"
              strokeWidth="8"
              strokeLinecap="round"
            />
            {/* Value arc */}
            <path
              d="M 10 55 A 50 50 0 0 1 110 55"
              fill="none"
              stroke="currentColor"
              strokeWidth="8"
              strokeLinecap="round"
              strokeDasharray={`${(pct / 100) * 157} 157`}
              className={cn('transition-all duration-700 ease-out', color)}
            />
          </svg>
          <div className="absolute inset-x-0 bottom-0 text-center">
            <span className={cn('text-2xl font-bold tabular-nums', color)}>{pct}%</span>
          </div>
        </div>
        <p className="text-sm text-muted-foreground mt-1">
          {memory.used_gb} / {memory.total_gb} GB used
        </p>
      </div>

      {/* Details */}
      <div className="grid grid-cols-2 gap-3 text-xs">
        <div className="rounded-lg bg-secondary/30 p-2.5">
          <p className="text-muted-foreground">Available</p>
          <p className="font-semibold text-foreground mt-0.5">{memory.available_gb} GB</p>
        </div>
        <div className="rounded-lg bg-secondary/30 p-2.5">
          <p className="text-muted-foreground">Swap Used</p>
          <p className="font-semibold text-foreground mt-0.5">{memory.swap_used_gb} GB</p>
        </div>
      </div>

      {/* Threshold markers */}
      <div className="space-y-1 text-[10px]">
        <div className="flex justify-between text-muted-foreground/70">
          <span>Warn: {memory.thresholds.warn_pct}%</span>
          <span>Hard: {memory.thresholds.hard_pct}%</span>
        </div>
      </div>
    </div>
  )
}
