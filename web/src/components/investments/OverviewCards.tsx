import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { formatPercent, formatUsd, signColor } from '@/lib/format'
import { useOverview } from '@/hooks/usePortfolioData'

function MetricCard({
  label,
  value,
  detail,
  detailColor,
}: {
  label: string
  value: string
  detail?: string
  detailColor?: string
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xs font-normal text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold tracking-tight tabular-nums text-foreground">
          {value}
        </div>
        {detail && <div className={`mt-1 text-xs tabular-nums ${detailColor}`}>{detail}</div>}
      </CardContent>
    </Card>
  )
}

function MetricSkeleton() {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-3 w-24" />
      </CardHeader>
      <CardContent>
        <Skeleton className="h-7 w-32" />
      </CardContent>
    </Card>
  )
}

// NEW_TASKS.md 6.1: one Value card (gain split realized/unrealized in the
// subline) instead of three separate "invested / value / gain" cards that
// spread one fact across three places.
export function OverviewCards() {
  const { data, isLoading, isError } = useOverview()

  if (isLoading) {
    return (
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <MetricSkeleton key={i} />
        ))}
      </div>
    )
  }

  if (isError || !data) {
    return (
      <p className="text-sm text-muted-foreground">
        No portfolio data yet — hit Sync to pull it from IBKR.
      </p>
    )
  }

  const xirrLabel = data.xirr_is_provisional ? 'XIRR (provisional)' : 'XIRR'
  const timingGap =
    data.timing_gap_pct === null
      ? undefined
      : `${data.timing_gap_pct >= 0 ? 'Timing helped' : 'Timing hurt'} ${formatPercent(data.timing_gap_pct)}`

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <MetricCard
        label="Value"
        value={formatUsd(data.value_usd)}
        detail={`${formatUsd(data.gain_usd)} (${formatPercent(data.gain_pct)}) — ${formatUsd(
          data.realized_gain_usd,
        )} realized, ${formatUsd(data.unrealized_gain_usd)} unrealized`}
        detailColor={signColor(data.gain_usd)}
      />
      <MetricCard
        label={xirrLabel}
        value={formatPercent(data.xirr_pct)}
        detail={`as of ${data.as_of}`}
        detailColor="text-muted-foreground"
      />
      <MetricCard
        label="Dollar alpha vs. HYSA"
        value={formatUsd(data.dollar_alpha_vs_hysa_usd)}
        detail="vs. a compounding HYSA counterfactual"
        detailColor={signColor(data.dollar_alpha_vs_hysa_usd)}
      />
      <MetricCard
        label="TWR"
        value={formatPercent(data.twr_annualized_pct ?? data.twr_pct)}
        detail={timingGap}
        detailColor="text-muted-foreground"
      />
    </div>
  )
}
