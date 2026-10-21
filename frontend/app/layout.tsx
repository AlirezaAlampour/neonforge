import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'
import { MainShell } from '@/components/main-shell'
import { Sidebar } from '@/components/sidebar'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })

export const metadata: Metadata = {
  title: 'Daidyra',
  description: 'Local-first voice and video creation for NVIDIA DGX Spark',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.variable} font-sans antialiased`}>
        <div className="flex h-dvh flex-col overflow-hidden bg-background md:flex-row">
          <Sidebar />
          <MainShell>{children}</MainShell>
        </div>
      </body>
    </html>
  )
}
