'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Activity, Mic, Video, Clapperboard, Zap, ChevronLeft, ChevronRight, LayoutGrid } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { MemoryGauge } from './memory-gauge'

const navItems = [
  { href: '/status', label: 'System Status', icon: Activity },
  { href: '/studio', label: 'Creative Studio', icon: LayoutGrid },
  { href: '/voice', label: 'Voice Studio', icon: Mic },
  { href: '/voiceover', label: 'Voiceover Studio', icon: Mic },
  { href: '/broll', label: 'B-Roll Studio', icon: Video },
  { href: '/lipsync', label: 'Lip Sync Studio', icon: Clapperboard },
]

export function Sidebar() {
  const pathname = usePathname()
  const [collapsed, setCollapsed] = useState(false)

  return (
    <aside
      className={cn(
        'flex flex-col border-r border-border/50 bg-card/50 backdrop-blur-xl transition-all duration-300',
        collapsed ? 'w-[68px]' : 'w-60',
      )}
    >
      {/* Brand */}
      <div className="flex h-14 items-center gap-2.5 border-b border-border/50 px-4">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-violet-600 shadow-lg shadow-blue-500/25">
          <Zap className="h-4 w-4 text-white" />
        </div>
        {!collapsed && (
          <span className="font-semibold text-lg bg-gradient-to-r from-blue-400 to-violet-400 bg-clip-text text-transparent">
            NeonForge
          </span>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 p-2 pt-3">
        {navItems.map((item) => {
          const isActive = pathname === item.href
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-200',
                isActive
                  ? 'bg-primary/10 text-primary shadow-sm'
                  : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground',
              )}
            >
              <item.icon className={cn('h-[18px] w-[18px] shrink-0', isActive && 'text-primary')} />
              {!collapsed && <span>{item.label}</span>}
            </Link>
          )
        })}
      </nav>

      {/* Memory gauge at bottom */}
      {!collapsed && (
        <div className="border-t border-border/50 p-3">
          <MemoryGauge compact />
        </div>
      )}

      {/* Collapse toggle */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="flex h-10 items-center justify-center border-t border-border/50 text-muted-foreground hover:text-foreground transition-colors"
      >
        {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
      </button>
    </aside>
  )
}
