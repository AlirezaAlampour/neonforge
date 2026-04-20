'use client'

import type { ReactNode } from 'react'
import { usePathname } from 'next/navigation'
import { cn } from '@/lib/utils'

interface MainShellProps {
  children: ReactNode
}

export function MainShell({ children }: MainShellProps) {
  const pathname = usePathname()
  const isVoiceoverStudio = pathname === '/voiceover'

  return (
    <main className="flex-1 overflow-y-auto">
      <div
        className={cn(
          'mx-auto px-6 py-8 lg:px-8',
          isVoiceoverStudio ? 'max-w-[104rem] 2xl:max-w-[112rem]' : 'max-w-5xl',
        )}
      >
        {children}
      </div>
    </main>
  )
}
