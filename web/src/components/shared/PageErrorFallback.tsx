import { AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'

// The one message shown any time a page can't do its job — a React
// crash (ErrorBoundary), a core data fetch failing outright (the
// accounting-store gate in App.tsx), or a real server error surfacing
// where a page would otherwise just render nothing. Never blank, always
// this — deliberately generic and personally signed rather than a wall
// of technical detail nobody but Gabi would act on anyway.
export function PageErrorFallback({ detail }: { detail?: string }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
      <AlertTriangle className="size-8 text-destructive" />
      <p className="text-sm font-medium text-foreground">A problem seems to have appeared.</p>
      <p className="max-w-sm text-xs text-muted-foreground">Let me know and I'll look into it.</p>
            <p className="max-w-sm text-xs text-muted-foreground">Gabi ^^</p>
      {detail && <p className="max-w-sm text-xs text-muted-foreground/70">{detail}</p>}
      <Button size="sm" variant="outline" onClick={() => window.location.reload()}>
        Reload
      </Button>
    </div>
  )
}
