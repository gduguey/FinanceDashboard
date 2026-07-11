import { AlertTriangle, Clock } from 'lucide-react'
import { Link } from 'react-router-dom'
import { formatRelativeTime } from '@/lib/format'
import { useOverview } from '@/hooks/usePortfolioData'
import { useSyncStatus } from '@/hooks/useAccountingData'

// Neither threshold comes from config — this is a UI nudge, not a
// correctness rule, so a rough "does this look neglected" cutoff is
// enough. IBKR is worth syncing every so often to keep prices/positions
// current; bank statements land roughly monthly, so a much longer window
// before flagging it as stale avoids nagging between one statement and
// the next.
const IBKR_STALE_DAYS = 14
const IMPORT_STALE_DAYS = 45

function daysAgo(iso: string | null): number | null {
  if (!iso) return null
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000)
}

function SyncStatusItem({
  label,
  iso,
  staleDays,
  href,
}: {
  label: string
  iso: string | null
  staleDays: number
  href: string
}) {
  const age = daysAgo(iso)
  const stale = age === null || age > staleDays
  return (
    <Link
      to={href}
      className={`flex items-center gap-1.5 transition-colors hover:underline ${stale ? 'text-amber-600 dark:text-amber-500' : 'text-muted-foreground'}`}
    >
      {stale ? <AlertTriangle className="size-3.5" /> : <Clock className="size-3.5" />}
      {label} {age === null ? 'never imported' : `synced ${formatRelativeTime(iso)}`}
    </Link>
  )
}

// The one place that answers "am I looking at stale numbers?" before
// anything else on the page — IBKR's own last sync and accounting's last
// statement import are each tracked on their own page, but nobody visits
// either page just to check freshness, so Overview surfaces both up front.
export function SyncStatusBar() {
  const { data: overview } = useOverview()
  const { data: syncStatus } = useSyncStatus()

  return (
    <div className="flex flex-wrap items-center gap-4 text-xs">
      <SyncStatusItem
        label="IBKR"
        iso={overview?.last_synced_at ?? null}
        staleDays={IBKR_STALE_DAYS}
        href="/investments"
      />
      <span className="text-border">·</span>
      <SyncStatusItem
        label="Bank data"
        iso={syncStatus?.last_import_at ?? null}
        staleDays={IMPORT_STALE_DAYS}
        href="/import"
      />
    </div>
  )
}
