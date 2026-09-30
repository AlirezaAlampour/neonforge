import { Image as ImageIcon, Music, Video } from 'lucide-react'

export default function AvatarPage() {
  return <div className="space-y-10">
    <header><h1 className="text-3xl font-semibold tracking-tight">Avatar</h1><p className="mt-2 text-muted-foreground">Give a portrait a voice.</p></header>
    <section className="rounded-2xl bg-card/50 px-6 py-12 sm:px-10">
      <span className="rounded-full bg-secondary px-3 py-1 text-xs text-muted-foreground">Coming soon</span>
      <h2 className="mt-6 text-2xl font-medium">One image. Your performance.</h2>
      <p className="mt-3 max-w-xl text-muted-foreground">Turn a reference image and speech into an expressive talking video. Local generation is still being validated; this workflow is currently unavailable.</p>
      <div className="mt-10 grid gap-8 sm:grid-cols-3">
        <div><ImageIcon className="h-6 w-6 text-primary" /><h3 className="mt-4 font-medium">Reference image</h3><p className="mt-2 text-sm text-muted-foreground">Choose your character.</p></div>
        <div><Music className="h-6 w-6 text-primary" /><h3 className="mt-4 font-medium">Audio</h3><p className="mt-2 text-sm text-muted-foreground">Add a voice or recorded speech.</p></div>
        <div><Video className="h-6 w-6 text-primary" /><h3 className="mt-4 font-medium">Avatar video</h3><p className="mt-2 text-sm text-muted-foreground">A performance you can preview and save.</p></div>
      </div>
    </section>
  </div>
}
