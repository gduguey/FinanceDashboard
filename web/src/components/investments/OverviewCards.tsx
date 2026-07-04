import { TrendingDown, TrendingUp } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { Skeleton } from '@/components/ui/skeleton'
import { formatPercent, formatUsd, signColor } from '@/lib/format'
import { useOverview, useRisk } from '@/hooks/usePortfolioData'
import type { GlossaryTerm } from '@/lib/glossary'

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-sm font-medium tabular-nums text-foreground">{value}</div>
    </div>
  )
}

function StatCard({
  label,
  tooltip,
  value,
  detail,
  detailColor,
}: {
  label: string
  tooltip?: GlossaryTerm
  value: string
  detail?: string
  detailColor?: string
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5 text-xs font-normal text-muted-foreground">
          {label}
          {tooltip && <InfoTooltip term={tooltip} />}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold tracking-tight tabular-nums text-foreground">{value}</div>
        {detail && <div className={`mt-1 text-xs tabular-nums ${detailColor}`}>{detail}</div>}
      </CardContent>
    </Card>
  )
}

function StatSkeleton({ big }: { big?: boolean }) {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-3 w-24" />
      </CardHeader>
      <CardContent>
        <Skeleton className={big ? 'h-10 w-48' : 'h-7 w-32'} />
      </CardContent>
    </Card>
  )
}

// NEW_TASKS.md 6.1: one hero Value card (current value, gain split
// realized/unrealized, plus money-in-from-you and dividends-received
// context) instead of a flat list of same-sized cards — the fact that
// matters most should look like it matters most. The secondary row
// (XIRR, dollar alpha, TWR, risk) fills out evenly instead of leaving an
// orphaned single card on its own row.
export function OverviewCards() {
  const { data, isLoading, isError } = useOverview()
  const risk = useRisk()

  if (isLoading) {
    return (
      <div className="space-y-4">
        <StatSkeleton big />
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <StatSkeleton key={i} />
          ))}
        </div>
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

  const isUp = data.gain_usd >= 0
  const xirrLabel = data.xirr_is_provisional ? 'XIRR (provisional)' : 'XIRR'
  const xirrTooltip: GlossaryTerm = data.xirr_is_provisional ? 'xirrProvisional' : 'xirr'
  const timingGap =
    data.timing_gap_pct === null
      ? undefined
      : `${data.timing_gap_pct >= 0 ? 'Timing helped' : 'Timing hurt'} ${formatPercent(data.timing_gap_pct)}`

  return (
    <div className="space-y-4">
      <Card className="overflow-hidden">
        <CardContent className="p-6">
          <div className="flex flex-wrap items-start justify-between gap-6">
            <div>
              <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                Value
                <InfoTooltip term="portfolioValue" />
              </div>
              <div className="mt-1 text-4xl font-semibold tracking-tight tabular-nums text-foreground">
                {formatUsd(data.value_usd)}
              </div>
              <div className={`mt-2 flex items-center gap-1 text-sm font-medium ${signColor(data.gain_usd)}`}>
                {isUp ? <TrendingUp className="size-4" /> : <TrendingDown className="size-4" />}
                {formatUsd(data.gain_usd)} ({formatPercent(data.gain_pct)})
                <span className="font-normal text-muted-foreground">as of {data.as_of}</span>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
              <MiniStat label="Money in (deposits)" value={formatUsd(data.total_deposited_usd)} />
              <MiniStat label="Dividends received" value={formatUsd(data.total_dividends_usd)} />
              <MiniStat label="Realized gain" value={formatUsd(data.realized_gain_usd)} />
              <MiniStat label="Unrealized gain" value={formatUsd(data.unrealized_gain_usd)} />
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label={xirrLabel} tooltip={xirrTooltip} value={formatPercent(data.xirr_pct)} />
        <StatCard
          label="Dollar alpha vs. HYSA"
          tooltip="dollarAlphaHysa"
          value={formatUsd(data.dollar_alpha_vs_hysa_usd)}
          detail="vs. a compounding HYSA counterfactual"
          detailColor="text-muted-foreground"
        />
        <StatCard
          label="TWR"
          tooltip="twr"
          value={formatPercent(data.twr_annualized_pct ?? data.twr_pct)}
          detail={timingGap}
          detailColor="text-muted-foreground"
        />
        <StatCard
          label="Largest peak-to-trough"
          tooltip="maxDrawdown"
          value={risk.data ? formatPercent(risk.data.max_drawdown_pct) : '—'}
        />
      </div>
    </div>
  )
}
