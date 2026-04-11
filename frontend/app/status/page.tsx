'use client'

import { RefreshCw, Cpu, HardDrive, Zap, Server } from 'lucide-react'
import { useSystemStatus } from '@/hooks/use-system-status'
import { formatRelativeTime } from '@/lib/utils'
import { cn } from '@/lib/utils'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { MemoryGauge } from '@/components/memory-gauge'

const serviceLabels: Record<string, { label: string; tier: string; icon: typeof Cpu }> = {
  whisper: { label: 'Whisper STT', tier: 'Always-On', icon: Cpu },
  f5tts: { label: 'F5-TTS', tier: 'Warm', icon: Zap },
  fish_speech: { label: 'Fish Speech', tier: 'Optional', icon: Zap },
  premium_clone_tts: { label: 'Premium Clone TTS', tier: 'Optional', icon: Zap },
  liveportrait: { label: 'LivePortrait', tier: 'Warm', icon: Server },
  lipsync: { label: 'Lip Sync', tier: 'Warm', icon: Server },
  wan21: { label: 'Wan 2.1', tier: 'Lazy-Start', icon: HardDrive },
}

export default function StatusPage() {
  const { memory, services, loading, refresh } = useSystemStatus()

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">System Status</h1>
          <p className="text-sm text-muted-foreground mt-1">DGX Spark UMA memory and model status</p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh} className="gap-2">
          <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      {/* Memory Gauge */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <HardDrive className="h-4 w-4 text-primary" />
            UMA Memory
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col items-center">
            <MemoryGauge />
          </div>
        </CardContent>
      </Card>

      {/* Services Grid */}
      <div>
        <h2 className="text-lg font-semibold mb-4">Model Services</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {services &&
            Object.entries(services).map(([name, status]) => {
              const meta = serviceLabels[name] || {
                label: name,
                tier: 'Unknown',
                icon: Server,
              }
              const Icon = meta.icon

              let state: 'active' | 'idle' | 'offline'
              let stateLabel: string
              let badgeVariant: 'success' | 'warning' | 'danger'

              if (status.ready) {
                state = 'active'
                stateLabel = 'Active'
                badgeVariant = 'success'
              } else if (status.alive) {
                state = 'idle'
                stateLabel = 'Idle'
                badgeVariant = 'warning'
              } else {
                state = 'offline'
                stateLabel = 'Asleep'
                badgeVariant = 'danger'
              }

              return (
                <Card
                  key={name}
                  className={cn(
                    'transition-all duration-200 hover:border-border',
                    state === 'active' && 'border-emerald-500/20',
                    state === 'offline' && 'opacity-60',
                  )}
                >
                  <CardContent className="p-5">
                    <div className="flex items-start justify-between">
                      <div className="flex items-center gap-3">
                        <div
                          className={cn(
                            'flex h-10 w-10 items-center justify-center rounded-lg',
                            state === 'active' && 'bg-emerald-500/10 text-emerald-400',
                            state === 'idle' && 'bg-amber-500/10 text-amber-400',
                            state === 'offline' && 'bg-secondary text-muted-foreground',
                          )}
                        >
                          <Icon className="h-5 w-5" />
                        </div>
                        <div>
                          <p className="font-semibold text-sm">{meta.label}</p>
                          <p className="text-xs text-muted-foreground">{meta.tier}</p>
                        </div>
                      </div>
                      <Badge variant={badgeVariant}>{stateLabel}</Badge>
                    </div>

                    <div className="mt-4 flex items-center gap-4 text-xs text-muted-foreground">
                      <div className="flex items-center gap-1.5">
                        <span
                          className={cn(
                            'h-1.5 w-1.5 rounded-full',
                            state === 'active'
                              ? 'bg-emerald-400 animate-pulse-slow'
                              : state === 'idle'
                                ? 'bg-amber-400'
                                : 'bg-slate-600',
                          )}
                        />
                        {status.alive ? 'Process alive' : 'Container stopped'}
                      </div>
                      {status.last_activity && (
                        <span>Last: {formatRelativeTime(status.last_activity)}</span>
                      )}
                    </div>
                  </CardContent>
                </Card>
              )
            })}

          {!services && loading && (
            <>
              {[1, 2, 3, 4, 5].map((i) => (
                <Card key={i} className="animate-pulse">
                  <CardContent className="p-5">
                    <div className="h-20 rounded bg-secondary/50" />
                  </CardContent>
                </Card>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
