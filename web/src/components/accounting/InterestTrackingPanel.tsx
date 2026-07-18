import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { NumberInput } from '@/components/ui/number-input'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useAccountingStore, useInterestSummary, useUpdateAccount } from '@/hooks/useAccountingData'
import { useSortableRows } from '@/hooks/useSortableRows'
import { formatCurrency, signColor } from '@/lib/format'

// Savings/vault accounts have no "return" of their own the way an
// investment does — this is the read-only counterpart for them: realized
// interest so far this year, the rate it's earning, how that compares to
// the HYSA benchmark `trades` already tracks, and a flat one-year
// projection at today's rate (compounding/contribution modeling is what
// the Simulator page is for).
export function InterestTrackingPanel() {
  const { data: rows, isLoading } = useInterestSummary()
  const { data: store } = useAccountingStore()
  const updateAccount = useUpdateAccount()
  const { sorted, sort, toggleSort } = useSortableRows(rows, 'account_name')

  if (isLoading) return <Skeleton className="h-48 w-full" />
  if (!rows?.length) return null

  // `apy_pct` has no automated source of truth anymore (the statement-PDF
  // import that used to refresh it on every sync is retired for new
  // imports — see `importers.sofi.statement_pdf`) — this is the only
  // place left to set it, so it persists straight onto the account's own
  // `meta`, the same field the old importer used to write.
  function commitApy(accountId: string, value: string) {
    const account = store?.accounts[accountId]
    if (!account) return
    const parsed = Number.parseFloat(value)
    if (!Number.isFinite(parsed)) return
    updateAccount.mutate({
      accountId,
      update: {
        name: account.name,
        institution: account.institution,
        kind: account.kind,
        currency: account.currency,
        meta: { ...account.meta, apy_pct: String(parsed) },
      },
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Interest-bearing accounts</CardTitle>
        <CardDescription>
          Realized interest this year, current APY vs. the HYSA benchmark, and a one-year projection at today's rate —
          APY is entered by hand below and persists per account.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <SortableTableHead
                active={sort.key === 'account_name'}
                desc={sort.desc}
                onClick={() => toggleSort('account_name')}
              >
                Account
              </SortableTableHead>
              <SortableTableHead
                align="right"
                active={sort.key === 'current_balance'}
                desc={sort.desc}
                onClick={() => toggleSort('current_balance')}
              >
                Balance
              </SortableTableHead>
              <SortableTableHead
                align="right"
                active={sort.key === 'apy_pct'}
                desc={sort.desc}
                onClick={() => toggleSort('apy_pct')}
              >
                APY
              </SortableTableHead>
              <TableHead className="text-right">vs. benchmark</TableHead>
              <SortableTableHead
                align="right"
                active={sort.key === 'interest_earned_this_year'}
                desc={sort.desc}
                onClick={() => toggleSort('interest_earned_this_year')}
              >
                Earned this year
              </SortableTableHead>
              <SortableTableHead
                align="right"
                active={sort.key === 'projected_next_12_months'}
                desc={sort.desc}
                onClick={() => toggleSort('projected_next_12_months')}
              >
                Projected next 12mo
              </SortableTableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((row) => {
              const delta = row.benchmark_apy_pct === null ? null : row.apy_pct - row.benchmark_apy_pct
              return (
                <TableRow key={row.account_id}>
                  <TableCell className="font-medium">{row.account_name}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(row.current_balance, row.currency)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    <div className="flex items-center justify-end gap-1">
                      <NumberInput
                        step={0.01}
                        className="h-7 w-20 text-right text-xs"
                        value={row.apy_pct}
                        onCommit={(pct) => commitApy(row.account_id, String(pct ?? ''))}
                      />
                      <span className="text-muted-foreground">%</span>
                    </div>
                  </TableCell>
                  <TableCell
                    className={`text-right tabular-nums ${delta === null ? 'text-muted-foreground' : signColor(delta)}`}
                  >
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
