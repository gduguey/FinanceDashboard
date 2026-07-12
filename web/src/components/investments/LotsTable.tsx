import { useState } from 'react'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { useSortableRows } from '@/hooks/useSortableRows'
import { formatDate, formatPercent, formatUsd, signColor } from '@/lib/format'
import { useLots, useTaxSettings } from '@/hooks/usePortfolioData'
import type { ClosedLot, OpenLot, SymbolRollup } from '@/types/portfolio'

function aggregateOpenLotsByDay(lots: OpenLot[]): OpenLot[] {
  const groups = new Map<string, OpenLot[]>()
  for (const lot of lots) {
    const key = `${lot.symbol}|${lot.opened_at.slice(0, 10)}`
    groups.set(key, [...(groups.get(key) ?? []), lot])
  }
  return [...groups.values()].map((group) => {
    if (group.length === 1) return group[0]
    const shares = group.reduce((sum, l) => sum + l.shares, 0)
    const costBasis = group.reduce((sum, l) => sum + l.shares * l.cost_per_share, 0)
    const dividendsReceived = group.reduce((sum, l) => sum + l.dividends_received, 0)
    const currentPrice = group[0].current_price
    const daysHeld = group[0].days_held
    const rawReturnPct = ((shares * currentPrice + dividendsReceived) / costBasis - 1) * 100
    return {
      lot_id: group.map((l) => l.lot_id).join('+'),
      symbol: group[0].symbol,
      opened_at: group[0].opened_at,
      shares,
      cost_per_share: costBasis / shares,
      dividends_received: dividendsReceived,
      current_price: currentPrice,
      days_held: daysHeld,
      raw_return_pct: rawReturnPct,
      annualized_return_pct:
        daysHeld >= 365 ? ((1 + rawReturnPct / 100) ** (365 / daysHeld) - 1) * 100 : null,
    }
  })
}

function aggregateClosedLotsByDay(lots: ClosedLot[]): ClosedLot[] {
  const groups = new Map<string, ClosedLot[]>()
  for (const lot of lots) {
    const key = `${lot.symbol}|${lot.closed_at.slice(0, 10)}`
    groups.set(key, [...(groups.get(key) ?? []), lot])
  }
  return [...groups.values()].map((group) => {
    if (group.length === 1) return group[0]
    const shares = group.reduce((sum, l) => sum + l.shares, 0)
    const costBasis = group.reduce((sum, l) => sum + l.shares * l.cost_per_share, 0)
    const realizedGain = group.reduce((sum, l) => sum + l.realized_gain, 0)
    const dividendsReceived = group.reduce((sum, l) => sum + l.dividends_received, 0)
    const daysHeld = group.reduce((sum, l) => sum + l.shares * l.days_held, 0) / shares
    const totalReturnPct = ((realizedGain + dividendsReceived) / costBasis) * 100
    const alphaVsHysaPct = group.reduce((sum, l) => sum + l.shares * l.cost_per_share * l.alpha_vs_hysa_pct, 0) / costBasis
    const largest = group.reduce((a, b) => (b.shares > a.shares ? b : a))
    return {
      lot_id: group.map((l) => l.lot_id).join('+'),
      symbol: group[0].symbol,
      opened_at: group[0].opened_at,
      closed_at: group[0].closed_at,
      shares,
      cost_per_share: costBasis / shares,
      exit_price: group.reduce((sum, l) => sum + l.shares * l.exit_price, 0) / shares,
      realized_gain: realizedGain,
      term: largest.term,
      dividends_received: dividendsReceived,
      days_held: daysHeld,
      total_return_pct: totalReturnPct,
      alpha_vs_hysa_pct: alphaVsHysaPct,
    }
  })
}

// Sold positions must not vanish from the trade table — that's
// self-inflicted survivorship bias — so open and closed lots get their
// own tabs, plus a per-symbol rollup that doesn't exist anywhere else.
export function LotsTable() {
  const { data, isLoading, isError } = useLots()
  const { data: taxSettings } = useTaxSettings()
  const [aggregateByDay, setAggregateByDay] = useState(false)
  const taxEnabled = taxSettings?.tax_enabled ?? false

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
              <div className="flex flex-wrap items-center justify-between gap-3">
                <TabsList>
                  <TabsTrigger value="open">Open lots ({data.open_lots.length})</TabsTrigger>
                  <TabsTrigger value="closed">Closed lots ({data.closed_lots.length})</TabsTrigger>
                </TabsList>
                <label className="flex items-center gap-2 text-sm text-muted-foreground">
                  Aggregate same symbol/day
                  <Switch size="sm" checked={aggregateByDay} onCheckedChange={setAggregateByDay} />
                </label>
              </div>
              <TabsContent value="open">
                <OpenLotsTable
                  lots={aggregateByDay ? aggregateOpenLotsByDay(data.open_lots) : data.open_lots}
                  taxEnabled={taxEnabled}
                />
              </TabsContent>
              <TabsContent value="closed">
                <ClosedLotsTable lots={aggregateByDay ? aggregateClosedLotsByDay(data.closed_lots) : data.closed_lots} />
              </TabsContent>
            </Tabs>
            <SymbolRollupTable rows={data.symbol_rollup} taxEnabled={taxEnabled} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function OpenLotsTable({ lots, taxEnabled }: { lots: OpenLot[]; taxEnabled: boolean }) {
  const { sorted, sort, toggleSort } = useSortableRows(lots, 'opened_at')
  if (!lots.length) return <p className="py-6 text-center text-sm text-muted-foreground">No open lots</p>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <SortableTableHead active={sort.key === 'symbol'} desc={sort.desc} onClick={() => toggleSort('symbol')}>
            Symbol
          </SortableTableHead>
          <SortableTableHead active={sort.key === 'opened_at'} desc={sort.desc} onClick={() => toggleSort('opened_at')}>
            Opened
          </SortableTableHead>
          <SortableTableHead align="right" active={sort.key === 'shares'} desc={sort.desc} onClick={() => toggleSort('shares')}>
            Shares
          </SortableTableHead>
          <SortableTableHead
            align="right"
            active={sort.key === 'cost_per_share'}
            desc={sort.desc}
            onClick={() => toggleSort('cost_per_share')}
          >
            Cost
          </SortableTableHead>
          <SortableTableHead
            align="right"
            active={sort.key === 'current_price'}
            desc={sort.desc}
            onClick={() => toggleSort('current_price')}
          >
            Current
          </SortableTableHead>
          <SortableTableHead
            align="right"
            active={sort.key === 'days_held'}
            desc={sort.desc}
            onClick={() => toggleSort('days_held')}
          >
            Days held
          </SortableTableHead>
          <SortableTableHead
            align="right"
            active={sort.key === 'raw_return_pct'}
            desc={sort.desc}
            onClick={() => toggleSort('raw_return_pct')}
          >
            Return <InfoTooltip term="lotReturn" />
          </SortableTableHead>
          <SortableTableHead
            align="right"
            active={sort.key === 'annualized_return_pct'}
            desc={sort.desc}
            onClick={() => toggleSort('annualized_return_pct')}
          >
            Annualized <InfoTooltip term="annualizedReturn" />
          </SortableTableHead>
          <SortableTableHead
            align="right"
            active={sort.key === 'dividends_received'}
            desc={sort.desc}
            onClick={() => toggleSort('dividends_received')}
          >
            {taxEnabled ? 'Dividends (net)' : 'Dividends (gross)'}
          </SortableTableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.map((lot) => (
          <TableRow key={lot.lot_id}>
            <TableCell className="font-medium">{lot.symbol}</TableCell>
            <TableCell>{formatDate(lot.opened_at.slice(0, 10))}</TableCell>
            <TableCell className="text-right tabular-nums">{lot.shares.toFixed(4)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(lot.cost_per_share)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(lot.current_price)}</TableCell>
            <TableCell className="text-right tabular-nums">{Math.round(lot.days_held)}</TableCell>
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
  const { sorted, sort, toggleSort } = useSortableRows(lots, 'closed_at')
  if (!lots.length) return <p className="py-6 text-center text-sm text-muted-foreground">No closed lots</p>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <SortableTableHead active={sort.key === 'symbol'} desc={sort.desc} onClick={() => toggleSort('symbol')}>
            Symbol
          </SortableTableHead>
          <SortableTableHead active={sort.key === 'closed_at'} desc={sort.desc} onClick={() => toggleSort('closed_at')}>
            Exit date
          </SortableTableHead>
          <SortableTableHead align="right" active={sort.key === 'shares'} desc={sort.desc} onClick={() => toggleSort('shares')}>
            Shares
          </SortableTableHead>
          <SortableTableHead
            align="right"
            active={sort.key === 'exit_price'}
            desc={sort.desc}
            onClick={() => toggleSort('exit_price')}
          >
            Exit price
          </SortableTableHead>
          <SortableTableHead
            align="right"
            active={sort.key === 'realized_gain'}
            desc={sort.desc}
            onClick={() => toggleSort('realized_gain')}
          >
            Realized $ <InfoTooltip term="realizedGain" />
          </SortableTableHead>
          <SortableTableHead active={sort.key === 'term'} desc={sort.desc} onClick={() => toggleSort('term')}>
            Term <InfoTooltip term="lotTerm" />
          </SortableTableHead>
          <SortableTableHead
            align="right"
            active={sort.key === 'total_return_pct'}
            desc={sort.desc}
            onClick={() => toggleSort('total_return_pct')}
          >
            Return <InfoTooltip term="lotReturn" />
          </SortableTableHead>
          <SortableTableHead
            align="right"
            active={sort.key === 'alpha_vs_hysa_pct'}
            desc={sort.desc}
            onClick={() => toggleSort('alpha_vs_hysa_pct')}
          >
            Alpha vs. HYSA <InfoTooltip term="alphaVsHysa" />
          </SortableTableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.map((lot) => (
          <TableRow key={lot.lot_id}>
            <TableCell className="font-medium">{lot.symbol}</TableCell>
            <TableCell>{formatDate(lot.closed_at.slice(0, 10))}</TableCell>
            <TableCell className="text-right tabular-nums">{lot.shares.toFixed(4)}</TableCell>
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

function SymbolRollupTable({ rows, taxEnabled }: { rows: SymbolRollup[]; taxEnabled: boolean }) {
  const { sorted, sort, toggleSort } = useSortableRows(rows, 'current_value')
  if (!rows.length) return null
  return (
    <div>
      <h4 className="mb-2 text-sm font-medium text-muted-foreground">Per-symbol rollup</h4>
      <Table>
        <TableHeader>
          <TableRow>
            <SortableTableHead active={sort.key === 'symbol'} desc={sort.desc} onClick={() => toggleSort('symbol')}>
              Symbol
            </SortableTableHead>
            <SortableTableHead active={sort.key === 'status'} desc={sort.desc} onClick={() => toggleSort('status')}>
              Status
            </SortableTableHead>
            <SortableTableHead
              align="right"
              active={sort.key === 'invested'}
              desc={sort.desc}
              onClick={() => toggleSort('invested')}
            >
              Invested
            </SortableTableHead>
            <SortableTableHead
              align="right"
              active={sort.key === 'current_value'}
              desc={sort.desc}
              onClick={() => toggleSort('current_value')}
            >
              Current value
            </SortableTableHead>
            <SortableTableHead
              align="right"
              active={sort.key === 'dividends_received'}
              desc={sort.desc}
              onClick={() => toggleSort('dividends_received')}
            >
              {taxEnabled ? 'Dividends (net)' : 'Dividends (gross)'}
            </SortableTableHead>
            <SortableTableHead
              align="right"
              active={sort.key === 'realized_gain'}
              desc={sort.desc}
              onClick={() => toggleSort('realized_gain')}
            >
              Realized <InfoTooltip term="realizedGain" />
            </SortableTableHead>
            <SortableTableHead
              align="right"
              active={sort.key === 'unrealized_gain'}
              desc={sort.desc}
              onClick={() => toggleSort('unrealized_gain')}
            >
              Unrealized <InfoTooltip term="unrealizedGain" />
            </SortableTableHead>
            <SortableTableHead align="right" active={sort.key === 'xirr'} desc={sort.desc} onClick={() => toggleSort('xirr')}>
              XIRR <InfoTooltip term="xirr" />
            </SortableTableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((row) => (
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
