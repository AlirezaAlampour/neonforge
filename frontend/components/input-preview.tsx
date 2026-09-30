'use client'

import { useEffect, useState } from 'react'

export function InputPreview({ file, kind }: { file: File | null; kind: 'image' | 'video' | 'audio' }) {
  const [url, setUrl] = useState<string>()
  useEffect(() => {
    if (!file) { setUrl(undefined); return }
    const objectUrl = URL.createObjectURL(file)
    setUrl(objectUrl)
    return () => URL.revokeObjectURL(objectUrl)
  }, [file])
  if (!url) return null
  if (kind === 'audio') return <audio controls src={url} className="w-full" />
  if (kind === 'video') return <video controls playsInline src={url} className="max-h-72 w-full rounded-xl bg-black" />
  return <img src={url} alt="Reference preview" className="max-h-72 w-full rounded-xl object-contain" />
}
