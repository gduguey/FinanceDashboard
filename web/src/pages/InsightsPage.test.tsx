import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { accountingApi } from '@/lib/accountingApi'
import { InsightsPage } from '@/pages/InsightsPage'
import { emptyStore } from '@/test/fixtures'

// The whole client is stubbed rather than the calls under test, so "which
// requests does this page make" is an assertion the test can make at all.
vi.mock('@/lib/accountingApi', () => ({
  accountingApi: {
    store: vi.fn(),
    postings: vi.fn(),
    postingsPage: vi.fn(),
    postingMonths: vi.fn(),
    categoryTotals: vi.fn(),
    monthlyIncomeExpense: vi.fn(),
    spendCurve: vi.fn(),
    currencies: vi.fn(),
  },
}))

const MONTHS = ['2026-03', '2026-02', '2025-11']

function renderInsightsPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }
  return render(<InsightsPage />, { wrapper: Wrapper })
}

describe('InsightsPage', () => {
  beforeEach(() => {
    vi.mocked(accountingApi.store).mockResolvedValue(emptyStore())
    vi.mocked(accountingApi.postingMonths).mockResolvedValue(MONTHS)
    vi.mocked(accountingApi.categoryTotals).mockResolvedValue([])
    vi.mocked(accountingApi.monthlyIncomeExpense).mockResolvedValue([])
    vi.mocked(accountingApi.spendCurve).mockResolvedValue([])
    vi.mocked(accountingApi.postings).mockResolvedValue([])
    vi.mocked(accountingApi.currencies).mockResolvedValue([])
  })

  it('offers the months the server reports in its period bar', async () => {
    renderInsightsPage()
    await waitFor(() => expect(accountingApi.postingMonths).toHaveBeenCalled())

    await userEvent.click((await screen.findAllByRole('combobox', { name: /month/i }))[0])
    for (const label of ['March 2026', 'February 2026', 'November 2025']) {
      expect(await screen.findByRole('option', { name: label })).toBeInTheDocument()
    }
  })

  it('never fetches the whole ledger', async () => {
    renderInsightsPage()
    await waitFor(() => expect(accountingApi.postingMonths).toHaveBeenCalled())
    await waitFor(() => expect(accountingApi.categoryTotals).toHaveBeenCalled())

    expect(accountingApi.postings).not.toHaveBeenCalled()
  })
})
