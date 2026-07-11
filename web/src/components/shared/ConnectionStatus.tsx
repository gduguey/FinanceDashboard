import { CheckCircle2, XCircle } from 'lucide-react'
import type { ConnectionState } from '@/hooks/usePortfolioData'

// Four real states, not two: nothing entered, something entered but
// rejected by the provider, still checking, or actually verified working
// — "Connected" only ever means the last one, never just "something's
// typed in". Shared by Settings, the sidebar's Investments switch, and
// Transactions' provider badges, so all three can never disagree.
export function ConnectionStatus({ state, error }: { state: ConnectionState; error: string | null }) {
  if (state === 'none') {
    return <span className="text-sm text-muted-foreground">Not connected</span>
  }
  if (state === 'checking') {
    return <span className="text-sm text-muted-foreground">Checking…</span>
  }
  if (state === 'invalid') {
    return (
      <span className="flex min-w-0 items-center gap-1.5 text-sm text-destructive">
        <XCircle className="size-4 shrink-0" />
        <span className="shrink-0">Can't authenticate</span>
        {error && (
          // Provider error text varies wildly in length (a one-line "bad
          // key" message vs. a whole nested error object) — truncate
          // rather than let a verbose one blow out the layout; full text
          // is still there on hover.
          <span className="min-w-0 truncate" title={error}>
            — {error}
          </span>
        )}
      </span>
    )
  }
  return (
    <span className="flex items-center gap-1.5 text-sm text-emerald-600">
      <CheckCircle2 className="size-4 shrink-0" />
      Connected
    </span>
  )
}
