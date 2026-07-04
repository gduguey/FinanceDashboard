import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { formatPercent } from '@/lib/format'
import { useRisk } from '@/hooks/usePortfolioData'

// NEW_TASKS.md 6.7: for buy-and-hold DCA the real risk is behavioral
// (panicking at a dip), and this is the number that calibrates it.
export function RiskStat() {
  const { data, isLoading } = useRisk()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xs font-normal text-muted-foreground">Largest peak-to-trough so far</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <Skeleton className="h-7 w-24" />
        ) : (
          <div className="text-2xl font-semibold tracking-tight tabular-nums text-foreground">
            {formatPercent(data.max_drawdown_pct)}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
