'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Activity, Boxes, Clapperboard, Mic, UserRound, Video, ChevronLeft, ChevronRight } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'

const SIDEBAR_COLLAPSED_KEY = 'neonforge-sidebar-collapsed-v1'
const navItems = [
  { href: '/voiceover', label: 'Voiceover', icon: Mic },
  { href: '/video', label: 'Video', icon: Video },
  { href: '/motion-director', label: 'Motion Director', icon: Clapperboard },
  { href: '/character', label: 'Character', icon: Boxes },
  { href: '/avatar', label: 'Avatar', icon: UserRound },
  { href: '/lipsync', label: 'Lip Sync', icon: Clapperboard },
  { href: '/status', label: 'System Info', icon: Activity },
]

export function DaidyraMark({className}: {className?: string}) {
  return <svg aria-hidden="true" viewBox="0 0 48 48" fill="none" className={cn('h-8 w-8 shrink-0 text-amber-200',className)}>
    <path d="M26 5H7v38h34V5h-8v30H15V13h11v14h-4v-6" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />
    <path d="M33 13h-7M7 35h8" stroke="currentColor" strokeWidth="2" />
    <circle cx="22" cy="21" r="2" fill="currentColor" />
  </svg>
}

export function Sidebar() {
  const pathname = usePathname()
  const [collapsed, setCollapsed] = useState(false)
  const [restored, setRestored] = useState(false)
  useEffect(() => {
    try {setCollapsed(window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true')} catch {setCollapsed(false)}
    finally {setRestored(true)}
  }, [])
  useEffect(() => {
    if (!restored) return
    try {window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed))} catch {}
  }, [collapsed, restored])
  return <>
    <div className="shrink-0 border-b border-white/10 bg-[#101117] md:hidden">
      <div className="flex h-14 items-center gap-2.5 px-4"><DaidyraMark className="h-7 w-7" /><Link href="/motion-director" className="text-lg font-semibold tracking-wide">Daidyra</Link><span className="ml-auto text-[10px] uppercase tracking-[.2em] text-muted-foreground">Creative studio</span></div>
      <nav aria-label="Studio workspaces" className="flex gap-1 overflow-x-auto px-2 pb-2">{navItems.map(item=><Link key={item.href} href={item.href} aria-current={pathname===item.href?'page':undefined} className={cn('flex min-h-11 shrink-0 items-center gap-2 rounded-lg px-3 text-xs',pathname===item.href?'bg-amber-100/10 text-amber-100':'text-muted-foreground')}><item.icon className="h-4 w-4" />{item.label}</Link>)}</nav>
    </div>
    <aside className={cn('hidden w-16 shrink-0 flex-col border-r border-white/[0.07] bg-[#101117] md:flex',!collapsed&&'md:w-[220px]')}>
      <Link href="/motion-director" aria-label="Daidyra studio" className={cn('flex h-20 items-center border-b border-white/[0.07]',collapsed?'justify-center':'gap-3 px-5')}><DaidyraMark />{!collapsed&&<div><span className="text-xl font-semibold tracking-wide">Daidyra</span><span className="mt-1 block text-[9px] uppercase tracking-[.22em] text-muted-foreground">The creative studio</span></div>}</Link>
      <nav aria-label="Studio navigation" className="flex-1 space-y-1.5 p-3 pt-6">{navItems.map(item=>{
        const active=pathname===item.href
        return <Link key={item.href} href={item.href} aria-label={item.label} aria-current={active?'page':undefined} title={collapsed?item.label:undefined} className={cn('flex min-h-11 items-center gap-3 rounded-lg px-3 text-[13px] transition-colors',collapsed&&'justify-center px-2',active?'bg-amber-100/[.08] text-amber-100 shadow-[inset_0_0_0_1px_rgba(240,208,150,.15)]':'text-muted-foreground hover:bg-white/5 hover:text-foreground')}><item.icon className="h-[18px] w-[18px] shrink-0" />{!collapsed&&<span>{item.label}</span>}</Link>
      })}</nav>
      {!collapsed&&<p className="px-5 py-4 text-[11px] leading-relaxed text-muted-foreground">A thread from idea<br />to moving image.</p>}
      <button type="button" aria-label={collapsed?'Expand sidebar':'Collapse sidebar'} aria-expanded={!collapsed} onClick={()=>setCollapsed(!collapsed)} className="flex min-h-11 items-center justify-center border-t border-white/10 text-muted-foreground hover:bg-white/5">{collapsed?<ChevronRight className="h-4 w-4" />:<ChevronLeft className="h-4 w-4" />}</button>
    </aside>
  </>
}
