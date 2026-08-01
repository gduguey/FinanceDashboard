import { Check, RefreshCw, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { isSyncFinished, useActiveSyncRun, useStartSync, useSyncRun } from '@/hooks/usePortfolioData'
import { formatRelativeTime } from '@/lib/format'
import type { SyncStep } from '@/types/portfolio'

// Each leg of a sync (portfolio pull, prices, benchmark, CPI, HYSA rates)
// now succeeds or fails independently on the backend — one bad IBKR token
// no longer hides whether the rest actually refreshed. Failed legs get
// their own error on hover, not a generic "sync failed".
function StepResult({ step }: { step: SyncStep }) {
  const icon = step.ok ? <Check className="size-3 text-emerald-600" /> : <X className="size-3 text-destructive" />
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
//
// The sync is its own record on the server now rather than something that
// happens inside the request this button makes, and two things follow. The
// button no longer waits for it: the POST comes back at once with a run to
// watch. And a run this tab never started is still watchable — a reload
// part-way through, or a second tab, adopts whatever is already in flight
// instead of showing nothing until it finishes.
export function SyncButton({ lastSyncedAt }: { lastSyncedAt: string | null }) {
  const start = useStartSync()
  const active = useActiveSyncRun()
  const [runId, setRunId] = useState<string | null>(null)
  const run = useSyncRun(runId)

  // Adopt a sync that was already going when this mounted. Only while this
  // tab has no run of its own — once the user starts one, `runId` is theirs
  // and the one-shot `active` query must not overwrite it.
  useEffect(() => {
    if (runId === null && active.data) setRunId(active.data.id)
  }, [runId, active.data])

  const running = runId !== null && !isSyncFinished(run.data)
  const pending = start.isPending || running
  const percent = Math.round(run.data?.percent ?? 0)
  // A run that failed *as a run* — the runner did not finish, or a restart
  // interrupted it. A failed broker leg is not this: that comes back as a
  // completed run and is reported per step below.
  const runError = run.data?.state === 'failed' ? (run.data.error ?? 'Sync failed') : null

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-3">
        <span className={`text-xs ${start.isError || runError ? 'text-destructive' : 'text-muted-foreground'}`}>
          {runError ||
            (start.isError ? 'Sync failed' : `Last synced ${formatRelativeTime(run.data?.synced_at ?? lastSyncedAt)}`)}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={pending}
          onClick={() => start.mutate(undefined, { onSuccess: (started) => setRunId(started.id) })}
        >
          <RefreshCw className={pending ? 'animate-spin' : ''} />
          Sync
        </Button>
      </div>
      {pending && (
        <div className="w-40">
          <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-foreground transition-[width] duration-300"
              style={{ width: `${percent}%` }}
            />
          </div>
          <div className="mt-0.5 text-right text-[10px] text-muted-foreground">
            {run.data?.step ?? 'Starting…'} · {percent}%
          </div>
        </div>
      )}
      {!pending && run.data && run.data.steps.length > 0 && (
        <div className="flex flex-wrap justify-end gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
          {run.data.steps.map((step) => (
            <StepResult key={step.label} step={step} />
          ))}
        </div>
      )}
    </div>
  )
}
