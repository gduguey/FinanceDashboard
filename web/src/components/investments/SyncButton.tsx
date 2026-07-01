import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { formatRelativeTime } from '@/lib/format'
import { useSync } from '@/hooks/usePortfolioData'

export function SyncButton({ lastSyncedAt }: { lastSyncedAt: string | null }) {
  const sync = useSync()

  return (
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
  )
}
