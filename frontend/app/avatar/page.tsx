import { AlertTriangle, Image as ImageIcon, Music, UserRound, Video } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export default function AvatarPage() {
  return (
    <div className="space-y-8">
      <div>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
            <UserRound className="h-6 w-6 text-primary" />
            Avatar
          </h1>
          <Badge variant="secondary">Disabled</Badge>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Turn a character reference and speech into a talking avatar video.
        </p>
      </div>

      <Card className="border-amber-500/30">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <AlertTriangle className="h-4 w-4 text-amber-400" />
            Backend not validated on this host
          </CardTitle>
          <CardDescription>
            EchoMimicV3-Flash is the selected deployment target, but NeonForge does not expose generation until its
            ARM64 runtime and a real image-plus-audio render pass on the DGX Spark memory gate.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          EchoMimicV3-Flash offered the best quality-to-infrastructure balance in the evaluated open avatar benchmark.
          The existing LivePortrait code remains a hidden legacy dependency and is not presented as this workflow.
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardContent className="p-5">
            <ImageIcon className="h-5 w-5 text-primary" />
            <p className="mt-3 text-sm font-semibold">1. Reference</p>
            <p className="mt-1 text-xs text-muted-foreground">A human portrait or supported stylized character.</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <Music className="h-5 w-5 text-primary" />
            <p className="mt-3 text-sm font-semibold">2. Audio</p>
            <p className="mt-1 text-xs text-muted-foreground">Generated or uploaded speech with a meaningful duration.</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <Video className="h-5 w-5 text-primary" />
            <p className="mt-3 text-sm font-semibold">3. Result</p>
            <p className="mt-1 text-xs text-muted-foreground">A tracked local video job once the backend is proven.</p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
