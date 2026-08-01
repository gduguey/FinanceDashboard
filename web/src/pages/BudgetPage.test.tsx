import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { accountingApi } from '@/lib/accountingApi'
import { BudgetPage } from '@/pages/BudgetPage'
import { emptyStore } from '@/test/fixtures'

// The whole client is stubbed rather than the two calls under test, so
// "which requests does this page make" is an assertion the test can make at
// all — the point of C7 is a request that must *not* happen.
vi.mock('@/lib/accountingApi', () => ({
  accountingApi: {
    store: vi.fn(),
    postings: vi.fn(),
    postingMonths: vi.fn(),
    categoryTotals: vi.fn(),
    suggestedBudgetAmount: vi.fn(),
    netWorth: vi.fn(),
    currencies: vi.fn(),
  },
}))

const MONTHS = ['2026-03', '2026-02', '2025-11']

function renderBudgetPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }
  return render(<BudgetPage />, { wrapper: Wrapper })
}

describe('BudgetPage’s month picker (C7)', () => {
  beforeEach(() => {
    vi.mocked(accountingApi.store).mockResolvedValue(emptyStore())
    vi.mocked(accountingApi.postingMonths).mockResolvedValue(MONTHS)
    vi.mocked(accountingApi.categoryTotals).mockResolvedValue([])
    vi.mocked(accountingApi.postings).mockResolvedValue([])
    vi.mocked(accountingApi.currencies).mockResolvedValue([])
  })

  it('offers the months the server reports', async () => {
    renderBudgetPage()
    await waitFor(() => expect(accountingApi.postingMonths).toHaveBeenCalled())

    await userEvent.click(screen.getByRole('combobox', { name: /month/i }))
    for (const label of ['March 2026', 'February 2026', 'November 2025']) {
      expect(await screen.findByRole('option', { name: label })).toBeInTheDocument()
    }
  })

  it('never fetches the ledger to learn them', async () => {
    renderBudgetPage()
    await waitFor(() => expect(accountingApi.postingMonths).toHaveBeenCalled())
    expect(accountingApi.postings).not.toHaveBeenCalled()
  })
})
