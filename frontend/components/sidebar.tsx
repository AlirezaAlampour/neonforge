'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Activity, Mic, Video, Clapperboard, ChevronLeft, ChevronRight, LayoutGrid } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { MemoryGauge } from './memory-gauge'

const SIDEBAR_COLLAPSED_KEY = 'neonforge-sidebar-collapsed-v1'

const navItems = [
  { href: '/status', label: 'System Status', icon: Activity },
  { href: '/studio', label: 'Creative Studio', icon: LayoutGrid },
  { href: '/voice', label: 'Voice Studio', icon: Mic },
  { href: '/voiceover', label: 'Voiceover Studio', icon: Mic },
  { href: '/broll', label: 'B-Roll Studio', icon: Video },
  { href: '/lipsync', label: 'Lip Sync Studio', icon: Clapperboard },
]

function NeonForgeLogo({ collapsed }: { collapsed: boolean }) {
  return (
    <div className={cn('flex min-w-0 items-center', collapsed ? 'justify-center' : 'gap-2.5')}>
      <svg
        aria-hidden="true"
        className="h-8 w-8 shrink-0 overflow-visible"
        viewBox="0 0 100 100"
        fill="none"
      >
        <defs>
          <linearGradient id="nf-anvil-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#1c1f2c" />
            <stop offset="100%" stopColor="#0a0c12" />
          </linearGradient>
        </defs>
        <g className="drop-shadow-[0_0_7px_rgba(244,63,140,0.6)]">
          <path d="M52 10 L44 32 L52 32 L46 52 L62 28 L54 28 L60 10 Z" fill="#f43f8c" />
        </g>
        <g className="drop-shadow-[0_0_7px_rgba(56,189,248,0.55)]">
          <path
            d="M14 50 L26 48 L34 50 L34 56 L80 56 L86 50 L86 58 L80 64 L60 64 L62 78 L74 84 L74 88 L26 88 L26 84 L38 78 L40 64 L34 64 L34 60 Z"
            fill="url(#nf-anvil-fill)"
            stroke="#38bdf8"
            strokeLinejoin="round"
            strokeWidth="1.8"
          />
          <path d="M14 50 L26 48 L34 50" stroke="#38bdf8" strokeLinejoin="round" strokeWidth="1.6" />
          <line x1="34" x2="80" y1="56" y2="56" stroke="#38bdf8" strokeOpacity="0.55" strokeWidth="0.8" />
        </g>
        <g stroke="#7c5cff" strokeWidth="0.75" fill="none" opacity="0.9">
          <path d="M50 68 L56 68 L58 70 L66 70" />
          <path d="M58 70 L58 76 L62 78" />
          <circle cx="66" cy="70" r="1.2" fill="#7c5cff" />
          <circle cx="50" cy="68" r="1.2" fill="#7c5cff" />
        </g>
      </svg>
      {!collapsed && (
        <span className="truncate bg-gradient-to-r from-sky-300 via-violet-300 to-pink-400 bg-clip-text text-[17px] font-bold tracking-tight text-transparent">
          NeonForge
        </span>
      )}
    </div>
  )
}

export function Sidebar() {
  const pathname = usePathname()
  const [collapsed, setCollapsed] = useState(false)
  const [restored, setRestored] = useState(false)

  useEffect(() => {
    try {
      setCollapsed(window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true')
    } catch {
      setCollapsed(false)
    } finally {
      setRestored(true)
    }
  }, [])

  useEffect(() => {
    if (!restored) return
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed))
  }, [collapsed, restored])

  return (
    <aside
      className={cn(
        'flex flex-col border-r border-white/[0.06] bg-[#0a0c12]/95 backdrop-blur-xl transition-all duration-300',
        collapsed ? 'w-14' : 'w-[220px]',
      )}
    >
      {/* Brand */}
      <div className={cn('flex h-14 items-center border-b border-white/[0.06]', collapsed ? 'justify-center px-2' : 'px-3')}>
        <NeonForgeLogo collapsed={collapsed} />
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 p-2 pt-3">
        {navItems.map((item) => {
          const isActive = pathname === item.href
          return (
            <Link
              key={item.href}
              href={item.href}
              title={collapsed ? item.label : undefined}
              className={cn(
                'group relative flex h-[34px] items-center gap-3 rounded-md px-3 text-[13px] font-medium transition-all duration-200',
                collapsed && 'justify-center px-2',
                isActive
                  ? 'bg-primary/10 text-primary shadow-[inset_0_0_0_1px_rgba(61,123,255,0.12)]'
                  : 'text-muted-foreground hover:bg-white/[0.04] hover:text-foreground',
              )}
            >
              <item.icon className={cn('h-[17px] w-[17px] shrink-0', isActive && 'text-primary')} />
              {!collapsed && <span>{item.label}</span>}
            </Link>
          )
        })}
      </nav>

      {/* Memory gauge at bottom */}
      {!collapsed && (
        <div className="border-t border-white/[0.06] p-2.5">
          <MemoryGauge compact />
        </div>
      )}

      {/* Collapse toggle */}
      <button
        type="button"
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        aria-expanded={!collapsed}
        onClick={() => setCollapsed(!collapsed)}
        className="flex h-10 items-center justify-center border-t border-white/[0.06] text-muted-foreground transition-colors hover:bg-white/[0.03] hover:text-foreground"
      >
        {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
      </button>
    </aside>
  )
}
