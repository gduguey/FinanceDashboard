import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatDate, formatPercent, formatUsd, signColor } from '@/lib/format'
import { useLots } from '@/hooks/usePortfolioData'
import type { ClosedLot, OpenLot, SymbolRollup } from '@/types/portfolio'

// NEW_TASKS.md 6.6: sold positions must not vanish from the trade table —
// that's self-inflicted survivorship bias — so open and closed lots get
// their own tabs, plus a per-symbol rollup that doesn't exist anywhere else.
export function LotsTable() {
  const { data, isLoading, isError } = useLots()

  return (
    <Card>
      <CardHeader>
        <CardTitle>Lots</CardTitle>
        <CardDescription>Open and closed positions, plus a per-symbol summary</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : isError || !data ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No lots yet — hit Sync to pull trade history.
          </p>
        ) : (
          <div className="space-y-6">
            <Tabs defaultValue="open">
              <TabsList>
                <TabsTrigger value="open">Open lots ({data.open_lots.length})</TabsTrigger>
                <TabsTrigger value="closed">Closed lots ({data.closed_lots.length})</TabsTrigger>
              </TabsList>
              <TabsContent value="open">
                <OpenLotsTable lots={data.open_lots} />
              </TabsContent>
              <TabsContent value="closed">
                <ClosedLotsTable lots={data.closed_lots} />
              </TabsContent>
            </Tabs>
            <SymbolRollupTable rows={data.symbol_rollup} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function OpenLotsTable({ lots }: { lots: OpenLot[] }) {
  if (!lots.length) return <p className="py-6 text-center text-sm text-muted-foreground">No open lots</p>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Symbol</TableHead>
          <TableHead>Opened</TableHead>
          <TableHead className="text-right">Shares</TableHead>
          <TableHead className="text-right">Cost</TableHead>
          <TableHead className="text-right">Current</TableHead>
          <TableHead className="text-right">Days held</TableHead>
          <TableHead className="text-right">Return</TableHead>
          <TableHead className="text-right">Annualized</TableHead>
          <TableHead className="text-right">Dividends</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {lots.map((lot) => (
          <TableRow key={lot.lot_id}>
            <TableCell className="font-medium">{lot.symbol}</TableCell>
            <TableCell>{formatDate(lot.opened_at.slice(0, 10))}</TableCell>
            <TableCell className="text-right tabular-nums">{lot.shares}</TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(lot.cost_per_share)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(lot.current_price)}</TableCell>
            <TableCell className="text-right tabular-nums">{lot.days_held}</TableCell>
            <TableCell className={`text-right tabular-nums ${signColor(lot.raw_return_pct)}`}>
              {formatPercent(lot.raw_return_pct)}
            </TableCell>
            <TableCell className={`text-right tabular-nums ${signColor(lot.annualized_return_pct)}`}>
              {lot.annualized_return_pct === null ? '—' : formatPercent(lot.annualized_return_pct)}
            </TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(lot.dividends_received)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function ClosedLotsTable({ lots }: { lots: ClosedLot[] }) {
  if (!lots.length) return <p className="py-6 text-center text-sm text-muted-foreground">No closed lots</p>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Symbol</TableHead>
          <TableHead>Exit date</TableHead>
          <TableHead className="text-right">Shares</TableHead>
          <TableHead className="text-right">Exit price</TableHead>
          <TableHead className="text-right">Realized $</TableHead>
          <TableHead>Term</TableHead>
          <TableHead className="text-right">Return</TableHead>
          <TableHead className="text-right">Alpha vs. HYSA</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {lots.map((lot) => (
          <TableRow key={lot.lot_id}>
            <TableCell className="font-medium">{lot.symbol}</TableCell>
            <TableCell>{formatDate(lot.closed_at.slice(0, 10))}</TableCell>
            <TableCell className="text-right tabular-nums">{lot.shares}</TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(lot.exit_price)}</TableCell>
            <TableCell className={`text-right tabular-nums ${signColor(lot.realized_gain)}`}>
              {formatUsd(lot.realized_gain)}
            </TableCell>
            <TableCell>
              <Badge variant="outline">{lot.term}</Badge>
            </TableCell>
            <TableCell className={`text-right tabular-nums ${signColor(lot.total_return_pct)}`}>
              {formatPercent(lot.total_return_pct)}
            </TableCell>
            <TableCell className={`text-right tabular-nums ${signColor(lot.alpha_vs_hysa_pct)}`}>
              {formatPercent(lot.alpha_vs_hysa_pct)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function SymbolRollupTable({ rows }: { rows: SymbolRollup[] }) {
  if (!rows.length) return null
  return (
    <div>
      <h4 className="mb-2 text-sm font-medium text-muted-foreground">Per-symbol rollup</h4>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Symbol</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Invested</TableHead>
            <TableHead className="text-right">Current value</TableHead>
            <TableHead className="text-right">Dividends</TableHead>
            <TableHead className="text-right">Realized</TableHead>
            <TableHead className="text-right">Unrealized</TableHead>
            <TableHead className="text-right">XIRR</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.symbol}>
              <TableCell className="font-medium">{row.symbol}</TableCell>
              <TableCell>
                <Badge variant={row.status === 'open' ? 'default' : 'outline'}>{row.status}</Badge>
              </TableCell>
              <TableCell className="text-right tabular-nums">{formatUsd(row.invested)}</TableCell>
              <TableCell className="text-right tabular-nums">{formatUsd(row.current_value)}</TableCell>
              <TableCell className="text-right tabular-nums">{formatUsd(row.dividends_received)}</TableCell>
              <TableCell className={`text-right tabular-nums ${signColor(row.realized_gain)}`}>
                {formatUsd(row.realized_gain)}
              </TableCell>
              <TableCell className={`text-right tabular-nums ${signColor(row.unrealized_gain)}`}>
                {formatUsd(row.unrealized_gain)}
              </TableCell>
              <TableCell className={`text-right tabular-nums ${signColor(row.xirr)}`}>
                {formatPercent(row.xirr * 100)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
