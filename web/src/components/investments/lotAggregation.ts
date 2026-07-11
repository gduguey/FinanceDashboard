import type { ClosedLot, OpenLot } from '@/types/portfolio'

export function aggregateOpenLotsByDay(lots: OpenLot[]): OpenLot[] {
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
      annualized_return_pct: daysHeld >= 365 ? ((1 + rawReturnPct / 100) ** (365 / daysHeld) - 1) * 100 : null,
    }
  })
}

export function aggregateClosedLotsByDay(lots: ClosedLot[]): ClosedLot[] {
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
    const daysHeld = group.reduce((sum, l) => sum + l.shares * (l.days_held ?? 0), 0) / shares
    const totalReturnPct = ((realizedGain + dividendsReceived) / costBasis) * 100
    const alphaVsHysaPct =
      group.reduce((sum, l) => sum + l.shares * l.cost_per_share * (l.alpha_vs_hysa_pct ?? 0), 0) / costBasis
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
      closed_by_event_id: largest.closed_by_event_id,
      dividends_received: dividendsReceived,
      days_held: daysHeld,
      total_return_pct: totalReturnPct,
      alpha_vs_hysa_pct: alphaVsHysaPct,
    }
  })
}
