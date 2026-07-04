import { Download } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { SortableTableHead } from '@/components/investments/SortableTableHead'
import { useSortableRows } from '@/hooks/useSortableRows'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { useDataQuality } from '@/hooks/usePortfolioData'

// NEW_TASKS.md 6.9: a monitoring tool you can't trust is worse than none —
// surface the last price sync per symbol, and make the ledger exportable
// so your financial history never lives only in this local cache.
export function DataQualityPanel() {
  const { data, isLoading, isError } = useDataQuality()
  const { sorted, sort, toggleSort } = useSortableRows(data, 'symbol')

  async function exportLedger() {
    const ledger = await api.ledgerExport()
    const blob = new Blob([JSON.stringify(ledger, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `ledger-${new Date().toISOString().slice(0, 10)}.json`
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Data quality</CardTitle>
        <CardDescription>Last price sync per symbol; export your full ledger any time</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : isError || !data ? (
          <p className="py-4 text-center text-sm text-muted-foreground">No data yet</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <SortableTableHead active={sort.key === 'symbol'} desc={sort.desc} onClick={() => toggleSort('symbol')}>
                  Symbol
                </SortableTableHead>
                <SortableTableHead
                  align="right"
                  active={sort.key === 'last_price_date'}
                  desc={sort.desc}
                  onClick={() => toggleSort('last_price_date')}
                >
                  Last price sync
                </SortableTableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((row) => (
                <TableRow key={row.symbol}>
                  <TableCell className="font-medium">{row.symbol}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {row.last_price_date ? formatDate(row.last_price_date) : 'never'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <Button variant="outline" size="sm" onClick={exportLedger} className="mt-4">
          <Download />
          Export ledger
        </Button>
      </CardContent>
    </Card>
  )
}
