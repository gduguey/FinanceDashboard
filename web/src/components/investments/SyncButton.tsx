import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { formatRelativeTime } from '@/lib/format'
import { useSync, useSyncProgress } from '@/hooks/usePortfolioData'

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
        <span className="text-xs text-muted-foreground">
          {sync.isError
            ? 'Sync failed'
            : `Last synced ${formatRelativeTime(sync.data?.synced_at ?? lastSyncedAt)}`}
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
    </div>
  )
}
