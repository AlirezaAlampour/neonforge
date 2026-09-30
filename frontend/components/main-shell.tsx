'use client'

import type { ReactNode } from 'react'
import { usePathname } from 'next/navigation'
import { cn } from '@/lib/utils'

interface MainShellProps {
  children: ReactNode
}

export function MainShell({ children }: MainShellProps) {
  const pathname = usePathname()
  const isWideWorkspace = pathname === '/voiceover' || pathname === '/character'

  return (
    <main className="flex-1 overflow-y-auto">
      <div
        className={cn(
          'mx-auto px-6 py-8 lg:px-8',
          isWideWorkspace ? 'max-w-7xl' : 'max-w-5xl',
        )}
      >
        {children}
      </div>
    </main>
  )
}
