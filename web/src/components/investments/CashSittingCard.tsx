import { AlertTriangle, Wallet } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { formatDate, formatUsd, signColor } from '@/lib/format'
import { benchmarkLabel } from '@/lib/labels'
import { useBenchmarkSetting, useCashSitting } from '@/hooks/usePortfolioData'
import type { CashSittingWarningLevel } from '@/types/portfolio'

const WARNING_COPY: Record<Exclude<CashSittingWarningLevel, 'none'>, string> = {
  light: 'Cash has been sitting uninvested for over a week.',
  heavy: 'Cash has been sitting uninvested for over two weeks.',
}

const WARNING_CLASSES: Record<CashSittingWarningLevel, string> = {
  none: 'border-border',
  light: 'border-amber-300 bg-amber-50',
  heavy: 'border-destructive/40 bg-destructive/10',
}

// A deposit or a sale that isn't followed by a real purchase just sits —
// this surfaces that directly, with what it's cost so far, rather than
// leaving it to be noticed only as a lower-than-expected balance. See
// `dashboard.cash_sitting` for exactly what counts as "sitting" and why.
export function CashSittingCard() {
  const { data, isLoading, isError, error } = useCashSitting()
  const { data: benchmarkSetting } = useBenchmarkSetting()

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Uninvested cash</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    )
  }

  if (isError || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Uninvested cash</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            {error?.message || 'No portfolio data yet — hit Sync to pull it from IBKR.'}
          </p>
        </CardContent>
      </Card>
    )
  }

  const benchmarkName = benchmarkLabel(benchmarkSetting)

  return (
    <Card className={WARNING_CLASSES[data.warning_level]}>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          <Wallet className="size-4 text-muted-foreground" />
          Uninvested cash
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <div className="text-2xl font-semibold tracking-tight tabular-nums text-foreground">
            {formatUsd(data.cash_usd)}
          </div>
          <div className="text-sm text-muted-foreground">
            Sitting since {formatDate(data.sitting_since)} ({data.days_sitting}{' '}
            {data.days_sitting === 1 ? 'day' : 'days'})
          </div>
        </div>

        {data.warning_level !== 'none' && (
          <div className="flex items-start gap-1.5 text-sm">
            <AlertTriangle
              className={`mt-0.5 size-4 shrink-0 ${data.warning_level === 'heavy' ? 'text-destructive' : 'text-amber-600'}`}
            />
            <span className={data.warning_level === 'heavy' ? 'text-destructive' : 'text-amber-700'}>
              {WARNING_COPY[data.warning_level]}
            </span>
          </div>
        )}

        <div className="grid grid-cols-1 gap-2 border-t border-border/60 pt-3 text-sm sm:grid-cols-2">
          <div>
            <div className="text-xs text-muted-foreground">If invested in your portfolio since then</div>
            <div className="tabular-nums">
              {formatUsd(data.hypothetical_value_portfolio_usd)}{' '}
              <span className={signColor(data.missed_earnings_portfolio_usd)}>
                ({data.missed_earnings_portfolio_usd >= 0 ? '+' : ''}
                {formatUsd(data.missed_earnings_portfolio_usd)})
              </span>
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">If invested in {benchmarkName} since then</div>
            <div className="tabular-nums">
              {formatUsd(data.hypothetical_value_benchmark_usd)}{' '}
              <span className={signColor(data.missed_earnings_benchmark_usd)}>
                ({data.missed_earnings_benchmark_usd >= 0 ? '+' : ''}
                {formatUsd(data.missed_earnings_benchmark_usd)})
              </span>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
