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
  lipsync: { label: 'Lip Sync', tier: 'Warm', icon: Server },
}

export default function StatusPage() {
  const { services, workloads, loading, error, refresh } = useSystemStatus()

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">System Info</h1>
          <p className="text-sm text-muted-foreground mt-1">Shared memory and workflow readiness</p>
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

      {error && (
        <div className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

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

              const serviceState = status.state || (status.ready ? 'ready' : status.alive ? 'loading' : 'disabled')
              const healthy = serviceState === 'ready'
              const busy = serviceState === 'loading' || serviceState === 'in_use'
              const failed = serviceState === 'missing_model' || serviceState === 'runtime_error'
              const stateLabel = status.state_label || (healthy ? 'Ready' : status.alive ? 'Loading' : 'Disabled')
              const badgeVariant: 'success' | 'warning' | 'danger' | 'secondary' = healthy
                ? 'success'
                : busy
                  ? 'warning'
                  : failed
                    ? 'danger'
                    : 'secondary'

              return (
                <Card
                  key={name}
                  className={cn(
                    'transition-all duration-200 hover:border-border',
                    healthy && 'border-emerald-500/20',
                    failed && 'border-red-500/20',
                    serviceState === 'disabled' && 'opacity-70',
                  )}
                >
                  <CardContent className="p-5">
                    <div className="flex items-start justify-between">
                      <div className="flex items-center gap-3">
                        <div
                          className={cn(
                            'flex h-10 w-10 items-center justify-center rounded-lg',
                            healthy && 'bg-emerald-500/10 text-emerald-400',
                            busy && 'bg-amber-500/10 text-amber-400',
                            failed && 'bg-red-500/10 text-red-400',
                            serviceState === 'disabled' && 'bg-secondary text-muted-foreground',
                          )}
                        >
                          <Icon className="h-5 w-5" />
                        </div>
                        <div>
                          <p className="font-semibold text-sm">{meta.label}</p>
                          <p className="text-xs text-muted-foreground">
                            {meta.tier}{status.legacy ? ' · Legacy' : ''}
                          </p>
                        </div>
                      </div>
                      <Badge variant={badgeVariant}>{stateLabel}</Badge>
                    </div>

                    <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
                      {status.detail || (status.alive ? 'Service process is responding.' : 'Service is stopped.')}
                    </p>

                    <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
                      <div className="flex items-center gap-1.5">
                        <span
                          className={cn(
                            'h-1.5 w-1.5 rounded-full',
                            healthy
                              ? 'bg-emerald-400 animate-pulse-slow'
                              : busy
                                ? 'bg-amber-400'
                                : failed
                                  ? 'bg-red-400'
                                  : 'bg-slate-600',
                          )}
                        />
                        {status.alive ? 'Process alive' : 'Container stopped'}
                      </div>
                      {status.backend && <span>Backend: {status.backend}</span>}
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

      {workloads && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Zap className="h-4 w-4 text-primary" /> Automatic model lifecycle
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(workloads.managed_services).map(([name, policy]) => (
                <div key={name} className="rounded-lg border border-border/50 bg-background/40 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium">{name}</p>
                    <Badge variant={policy.claimed ? 'warning' : policy.running ? 'success' : 'secondary'}>
                      {policy.claimed ? 'In use' : policy.running ? 'Idle / ready' : 'Stopped'}
                    </Badge>
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground">
                    {policy.workload_class} · {policy.min_available_gb} GiB base minimum · idle unload {Math.round(policy.idle_timeout_sec / 60)}m
                  </p>
                </div>
              ))}
            </div>
            <div className="space-y-2 text-sm">
              <h3 className="font-medium">Current workload claims</h3>
              {!workloads.claims.length && <p className="text-muted-foreground">No active generation claims.</p>}
              {workloads.claims.map((claim) => <p key={claim.claim_id}>{claim.model_label} <span className="text-xs text-muted-foreground">{claim.job_id || claim.claim_id}</span></p>)}
            </div>
            <div className="space-y-2 text-xs text-muted-foreground">
              <h3 className="text-sm font-medium text-foreground">Recent lifecycle activity</h3>
              {workloads.events.slice(-12).reverse().map((event, index) => <div key={`${event.at}-${index}`} className="flex flex-wrap gap-x-3 gap-y-1 border-b border-border/30 py-2"><time>{new Date(event.at * 1000).toLocaleTimeString()}</time><span>{event.service}</span><span>{event.event.replaceAll('_', ' ')}</span>{event.reason && <span>{event.reason}</span>}{event.recovered_gb !== undefined && <span>Reclaimed {event.recovered_gb} GiB</span>}{event.min_available_gb !== undefined && <span>Lowest available {event.min_available_gb} GiB</span>}</div>)}
            </div>
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer font-medium">Lifecycle diagnostics</summary>
              <p className="mt-2">Protected: {workloads.protected_services.join(', ')}</p>
              <p className="mt-1">Active claims: {workloads.claims.length}</p>
              <p className="mt-1 font-mono">{JSON.stringify(workloads.metrics)}</p>
            </details>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
