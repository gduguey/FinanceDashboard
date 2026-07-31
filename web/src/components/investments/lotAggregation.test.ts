import { describe, expect, it } from 'vitest'
import { aggregateClosedLotsByDay, aggregateOpenLotsByDay } from '@/components/investments/lotAggregation'
import type { ClosedLot, OpenLot } from '@/types/portfolio'

function makeOpenLot(overrides: Partial<OpenLot> & Pick<OpenLot, 'lot_id'>): OpenLot {
  return {
    symbol: 'VOO',
    opened_at: '2026-01-01T00:00:00',
    shares: 1,
    cost_per_share: 100,
    dividends_received: 0,
    current_price: 100,
    days_held: 10,
    raw_return_pct: 0,
    annualized_return_pct: null,
    ...overrides,
  }
}

function makeClosedLot(overrides: Partial<ClosedLot> & Pick<ClosedLot, 'lot_id'>): ClosedLot {
  return {
    symbol: 'VOO',
    opened_at: '2026-01-01T00:00:00',
    closed_at: '2026-02-01T00:00:00',
    shares: 1,
    cost_per_share: 100,
    exit_price: 110,
    realized_gain: 10,
    term: 'SHORT',
    closed_by_event_id: 'evt-1',
    dividends_received: 0,
    days_held: 31,
    total_return_pct: 10,
    excess_return_vs_hysa_pct: 2,
    ...overrides,
  }
}

describe('aggregateOpenLotsByDay', () => {
  it('leaves a day with a single lot untouched', () => {
    const lot = makeOpenLot({ lot_id: 'a' })
    expect(aggregateOpenLotsByDay([lot])).toEqual([lot])
  })

  it('merges same-symbol same-day lots into one weighted row', () => {
    const lots = [
      makeOpenLot({ lot_id: 'a', shares: 1, cost_per_share: 100, dividends_received: 1 }),
      makeOpenLot({ lot_id: 'b', shares: 3, cost_per_share: 100, dividends_received: 2 }),
    ]
    const [merged] = aggregateOpenLotsByDay(lots)
    expect(merged.lot_id).toBe('a+b')
    expect(merged.shares).toBe(4)
    expect(merged.dividends_received).toBe(3)
    expect(merged.cost_per_share).toBe(100)
  })

  it('keeps lots on different days separate', () => {
    const lots = [
      makeOpenLot({ lot_id: 'a', opened_at: '2026-01-01T00:00:00' }),
      makeOpenLot({ lot_id: 'b', opened_at: '2026-01-02T00:00:00' }),
    ]
    expect(aggregateOpenLotsByDay(lots)).toHaveLength(2)
  })

  it('only annualizes a merged lot once its (share-weighted) days held reaches a year', () => {
    const under = aggregateOpenLotsByDay([
      makeOpenLot({ lot_id: 'a', shares: 1, cost_per_share: 100, current_price: 110, days_held: 300 }),
      makeOpenLot({ lot_id: 'b', shares: 1, cost_per_share: 100, current_price: 110, days_held: 300 }),
    ])
    expect(under[0].annualized_return_pct).toBeNull()
  })
})

describe('aggregateClosedLotsByDay', () => {
  it('leaves a day with a single lot untouched', () => {
    const lot = makeClosedLot({ lot_id: 'a' })
    expect(aggregateClosedLotsByDay([lot])).toEqual([lot])
  })

  it('merges same-symbol same-day lots, attributing closed_by_event_id to the largest leg', () => {
    const lots = [
      makeClosedLot({ lot_id: 'a', shares: 1, closed_by_event_id: 'small' }),
      makeClosedLot({ lot_id: 'b', shares: 5, closed_by_event_id: 'big' }),
    ]
    const [merged] = aggregateClosedLotsByDay(lots)
    expect(merged.lot_id).toBe('a+b')
    expect(merged.shares).toBe(6)
    expect(merged.closed_by_event_id).toBe('big')
  })

  // Regression: ClosedLotRow.days_held/excess_return_vs_hysa_pct can be null (see the
  // OpenAPI-generated type) — a merge must not let one null leg turn the
  // whole weighted average into NaN.
  it('treats a null days_held/excess_return_vs_hysa_pct leg as contributing zero to the weighted average', () => {
    const lots = [
      makeClosedLot({ lot_id: 'a', shares: 1, days_held: null, excess_return_vs_hysa_pct: null }),
      makeClosedLot({ lot_id: 'b', shares: 1, days_held: 30, excess_return_vs_hysa_pct: 4 }),
    ]
    const [merged] = aggregateClosedLotsByDay(lots)
    expect(merged.days_held).toBe(15)
    expect(merged.excess_return_vs_hysa_pct).toBe(2)
  })
})
