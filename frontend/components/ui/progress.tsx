import { cn } from '@/lib/utils'

interface ProgressProps {
  value?: number
  indeterminate?: boolean
  className?: string
}

export function Progress({ value = 0, indeterminate, className }: ProgressProps) {
  return (
    <div className={cn('relative h-2 w-full overflow-hidden rounded-full bg-secondary', className)}>
      {indeterminate ? (
        <div className="absolute inset-0 h-full w-1/3 animate-[indeterminate_1.5s_ease-in-out_infinite] rounded-full bg-primary" />
      ) : (
        <div
          className="h-full rounded-full bg-primary transition-all duration-500 ease-out"
          style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
        />
      )}
    </div>
  )
}
