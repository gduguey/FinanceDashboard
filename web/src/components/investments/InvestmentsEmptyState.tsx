import { ArrowRight, Settings as SettingsIcon } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useIbkrSettings, useOverview } from '@/hooks/usePortfolioData'

// Mirrors NoAccountsYetBanner's role on the Money side: one clear
// explanation of the next step, shown once at the top of a page, instead
// of each card below silently rendering its own "no data" text with no
// obvious action to take. Renders nothing once real portfolio data exists.
export function InvestmentsEmptyState() {
  const { data: ibkr, isLoading: ibkrLoading } = useIbkrSettings()
  const { data: overview, isLoading: overviewLoading, isError } = useOverview()

  if (ibkrLoading || overviewLoading) return null
  if (overview && !isError) return null

  if (!ibkr?.configured) {
    return (
      <Link
        to="/settings"
        className="flex items-center justify-between gap-3 rounded-lg border border-dashed border-border bg-muted/40 px-4 py-3 text-sm transition-colors hover:bg-muted/70"
      >
        <span className="flex items-center gap-2 text-muted-foreground">
          <SettingsIcon className="size-4" />
          IBKR isn't connected yet — add your Flex Web Service token in Settings to see your portfolio here.
        </span>
        <span className="flex items-center gap-1 font-medium text-foreground">
          Settings <ArrowRight className="size-3.5" />
        </span>
      </Link>
    )
  }

  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border bg-muted/40 px-4 py-12 text-center">
      <p className="text-sm text-muted-foreground">
        IBKR is connected — use the Sync button at the top of the page to pull your portfolio.
      </p>
    </div>
  )
}
