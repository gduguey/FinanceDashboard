import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatCurrency, signColor } from '@/lib/format'
import { useInterestSummary } from '@/hooks/useAccountingData'

// Savings/vault accounts have no "return" of their own the way an
// investment does — this is the read-only counterpart for them: realized
// interest so far this year, the rate it's earning, how that compares to
// the HYSA benchmark `trades` already tracks, and a flat one-year
// projection at today's rate (compounding/contribution modeling is what
// the Simulator page is for).
export function InterestTrackingPanel() {
  const { data: rows, isLoading } = useInterestSummary()

  if (isLoading) return <Skeleton className="h-48 w-full" />
  if (!rows?.length) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Interest-bearing accounts</CardTitle>
        <CardDescription>Realized interest this year, current APY vs. the HYSA benchmark, and a one-year projection at today's rate</CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Account</TableHead>
              <TableHead className="text-right">Balance</TableHead>
              <TableHead className="text-right">APY</TableHead>
              <TableHead className="text-right">vs. benchmark</TableHead>
              <TableHead className="text-right">Earned this year</TableHead>
              <TableHead className="text-right">Projected next 12mo</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const delta = row.benchmark_apy_pct === null ? null : row.apy_pct - row.benchmark_apy_pct
              return (
                <TableRow key={row.account_id}>
                  <TableCell className="font-medium">{row.account_name}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatCurrency(row.current_balance, row.currency)}</TableCell>
                  <TableCell className="text-right tabular-nums">{row.apy_pct.toFixed(2)}%</TableCell>
                  <TableCell className={`text-right tabular-nums ${delta === null ? 'text-muted-foreground' : signColor(delta)}`}>
                    {delta === null ? '—' : `${delta >= 0 ? '+' : ''}${delta.toFixed(2)} pts`}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(row.interest_earned_this_year, row.currency)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {formatCurrency(row.projected_next_12_months, row.currency)}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}
