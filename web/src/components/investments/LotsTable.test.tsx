import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it } from 'vitest'
import { LotsTable } from '@/components/investments/LotsTable'
import type { ClosedLot, LotsTable as LotsTableData } from '@/types/portfolio'

const CLOSED_LOT: ClosedLot = {
  lot_id: 'lot-1',
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
}

const LOTS: LotsTableData = { open_lots: [], closed_lots: [CLOSED_LOT], symbol_rollup: [] }

describe('LotsTable closed lots', () => {
  let queryClient: QueryClient

  function renderWithTax(taxEnabled: boolean) {
    // `staleTime: Infinity` so the seeded cache is the whole data source:
    // without it the hooks refetch in the background, the fetch fails with
    // no server behind jsdom, and the component renders its error state.
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Number.POSITIVE_INFINITY } },
    })
    queryClient.setQueryData(['portfolio', 'lots'], LOTS)
    queryClient.setQueryData(['portfolio', 'settings', 'tax'], { tax_enabled: taxEnabled })
    function Wrapper({ children }: { children: ReactNode }) {
      return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    }
    return render(<LotsTable />, { wrapper: Wrapper })
  }

  beforeEach(() => {
    queryClient = new QueryClient()
  })

  // A3e: this column was headed "Alpha vs. HYSA", which it is not — it is
  // one lot's return minus what savings would have paid over the same
  // window, with no risk adjustment anywhere in it.
  it('heads the column with what it computes rather than "alpha"', async () => {
    renderWithTax(false)
    await userEvent.click(screen.getByRole('tab', { name: /closed lots/i }))

    expect(screen.getByRole('columnheader', { name: /excess return vs\. hysa/i })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: /alpha/i })).not.toBeInTheDocument()
  })

  // The HYSA leg goes through `hysa_rate_lookup`, which switches to an
  // after-tax rate when tax is on — so the column means something different
  // then, and says so, exactly as the overview card already did.
  it('marks the column after-tax when the tax toggle is on', async () => {
    renderWithTax(true)
    await userEvent.click(screen.getByRole('tab', { name: /closed lots/i }))

    expect(screen.getByRole('columnheader', { name: /excess return vs\. hysa \(after tax\)/i })).toBeInTheDocument()
  })
})
