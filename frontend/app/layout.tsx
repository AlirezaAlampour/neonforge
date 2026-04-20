import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'
import { MainShell } from '@/components/main-shell'
import { Sidebar } from '@/components/sidebar'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })

export const metadata: Metadata = {
  title: 'NeonForge Console',
  description: "Director's Console for DGX Spark AI Stack",
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
