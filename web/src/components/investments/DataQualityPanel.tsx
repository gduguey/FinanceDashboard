import { ExportButtons } from '@/components/shared/ExportButtons'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { useDataQuality } from '@/hooks/usePortfolioData'
import { useSortableRows } from '@/hooks/useSortableRows'
import { api } from '@/lib/api'
import { downloadCsv, downloadJson, exportStamp } from '@/lib/download'
import { formatDate } from '@/lib/format'

// A monitoring tool you can't trust is worse than none — surface the last
// price sync per symbol, and make the ledger exportable so your financial
// history never lives only in this local cache. The same export also
// lives on Settings' own Export tab, for anyone who'd rather find every
// export in one place than hunt for it next to the data it happens to sit near.
export function DataQualityPanel() {
  const { data, isLoading, isError, error } = useDataQuality()
  const { sorted, sort, toggleSort } = useSortableRows(data, 'symbol')

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
          <p className="py-4 text-center text-sm text-muted-foreground">{error?.message || 'No data yet'}</p>
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
        <div className="mt-4">
          <ExportButtons
            onJson={async () => downloadJson(await api.ledgerExport(), `investments-ledger-${exportStamp()}.json`)}
            onCsv={async () => downloadCsv(await api.ledgerExport(), `investments-ledger-${exportStamp()}.csv`)}
          />
        </div>
      </CardContent>
    </Card>
  )
}
