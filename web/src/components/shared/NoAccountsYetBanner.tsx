import { ArrowRight, Import } from 'lucide-react'
import { Link } from 'react-router-dom'

// Shown above a page's own charts/tables whenever the user has no real
// accounts yet — every one of those widgets already degrades gracefully
// on its own ("No data yet"), but seeing 2-3 of them at once with no
// explanation reads as broken rather than "you haven't added anything".
// This is the one place that names the actual next step, the same way
// TransactionsTab's own empty state already does for its one table.
export function NoAccountsYetBanner() {
  return (
    <Link
      to="/import"
      className="flex items-center justify-between gap-3 rounded-lg border border-dashed border-border bg-muted/40 px-4 py-3 text-sm transition-colors hover:bg-muted/70"
    >
      <span className="flex items-center gap-2 text-muted-foreground">
        <Import className="size-4" />
        No accounts yet — import a statement to start seeing your numbers here.
      </span>
      <span className="flex items-center gap-1 font-medium text-foreground">
        Import <ArrowRight className="size-3.5" />
      </span>
    </Link>
  )
}
