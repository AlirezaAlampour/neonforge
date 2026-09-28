import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'
import { MainShell } from '@/components/main-shell'
import { Sidebar } from '@/components/sidebar'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })

export const metadata: Metadata = {
  title: 'NeonForge',
  description: 'Local-first voice and video creation for NVIDIA DGX Spark',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.variable} font-sans antialiased`}>
        <div className="flex h-screen overflow-hidden bg-background">
          <Sidebar />
          <MainShell>{children}</MainShell>
        </div>
      </body>
    </html>
  )
}
