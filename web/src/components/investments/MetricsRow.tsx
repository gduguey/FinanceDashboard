import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { formatPercent, formatUsd, signColor } from '@/lib/format'
import { useSummary } from '@/hooks/usePortfolioData'

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

export function MetricsRow() {
  const { data, isLoading, isError } = useSummary()

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

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <MetricCard label="Total invested" value={formatUsd(data.total_invested_usd)} />
      <MetricCard
        label="Current value"
        value={formatUsd(data.current_value_usd)}
        detail={`as of ${data.as_of_date}`}
        detailColor="text-muted-foreground"
      />
      <MetricCard
        label="Total gain"
        value={formatUsd(data.total_gain_usd)}
        detail={formatPercent(data.total_gain_pct)}
        detailColor={signColor(data.total_gain_usd)}
      />
      <MetricCard
        label={`Alpha vs. ${(data.hysa_annual_rate * 100).toFixed(0)}% HYSA`}
        value={formatPercent(data.portfolio_alpha_pct)}
        detail={`${data.symbol_count} symbol${data.symbol_count === 1 ? '' : 's'}`}
        detailColor="text-muted-foreground"
      />
    </div>
  )
}
