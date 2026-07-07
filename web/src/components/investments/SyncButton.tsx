import { Check, RefreshCw, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { formatRelativeTime } from '@/lib/format'
import { useSync, useSyncProgress } from '@/hooks/usePortfolioData'
import type { SyncStep } from '@/types/portfolio'

// Each leg of a sync (portfolio pull, prices, benchmark, CPI, HYSA rates)
// now succeeds or fails independently on the backend — one bad IBKR token
// no longer hides whether the rest actually refreshed. Failed legs get
// their own error on hover, not a generic "sync failed".
function StepResult({ step }: { step: SyncStep }) {
  const icon = step.ok ? (
    <Check className="size-3 text-emerald-600" />
  ) : (
    <X className="size-3 text-destructive" />
  )
  if (step.ok || !step.error) {
    return (
      <span className="flex items-center gap-1">
        {icon}
        {step.label}
      </span>
    )
  }
  return (
    <Tooltip>
      <TooltipTrigger className="flex items-center gap-1">
        {icon}
        {step.label}
      </TooltipTrigger>
      <TooltipContent className="max-w-64 text-pretty">{step.error}</TooltipContent>
    </Tooltip>
  )
}

// A first sync can take a while — most of it spent waiting on IBKR to
// generate the statement — so a bare spinner reads as "is this frozen?"
// Polling the step/percent the backend reports turns that dead time into
// a small bar plus a short line of what's actually happening.
export function SyncButton({ lastSyncedAt }: { lastSyncedAt: string | null }) {
  const sync = useSync()
  const progress = useSyncProgress(sync.isPending)
  const percent = Math.round(progress.data?.percent ?? 0)

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-3">
        <span className={`text-xs ${sync.isError || progress.data?.error ? 'text-destructive' : 'text-muted-foreground'}`}>
          {progress.data?.error ||
            (sync.isError
              ? 'Sync failed'
              : `Last synced ${formatRelativeTime(sync.data?.synced_at ?? lastSyncedAt)}`)}
        </span>
        <Button variant="outline" size="sm" disabled={sync.isPending} onClick={() => sync.mutate()}>
          <RefreshCw className={sync.isPending ? 'animate-spin' : ''} />
          Sync
        </Button>
      </div>
      {sync.isPending && (
        <div className="w-40">
          <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-foreground transition-[width] duration-300"
              style={{ width: `${percent}%` }}
            />
          </div>
          <div className="mt-0.5 text-right text-[10px] text-muted-foreground">
            {progress.data?.step ?? 'Starting…'} · {percent}%
          </div>
        </div>
      )}
      {!sync.isPending && sync.data?.steps && (
        <div className="flex flex-wrap justify-end gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
          {sync.data.steps.map((step) => (
            <StepResult key={step.label} step={step} />
          ))}
        </div>
      )}
    </div>
  )
}
