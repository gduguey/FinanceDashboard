import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ClampedRateNote } from '@/components/shared/ClampedRateNote'
import { accountingApi } from '@/lib/accountingApi'

vi.mock('@/lib/accountingApi', () => ({ accountingApi: { exchangeRateCoverage: vi.fn() } }))

function renderNote(start: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return render(<ClampedRateNote start={start} displayCurrency="USD" />, { wrapper: Wrapper })
}

const NOTE = /oldest exchange rate on file/

describe('ClampedRateNote (A5)', () => {
  beforeEach(() => {
    vi.mocked(accountingApi.exchangeRateCoverage).mockResolvedValue({
      earliest: '2024-08-01',
      latest: '2026-08-01',
    })
  })

  it('names the date before which figures were converted at a clamped rate', async () => {
    renderNote('2019-01-01')

    expect(await screen.findByText(NOTE)).toBeInTheDocument()
    expect(screen.getByText(/Aug 1, 2024/)).toBeInTheDocument()
  })

  it('says nothing when the window starts inside the cached history', async () => {
    renderNote('2025-01-01')

    await waitFor(() => expect(accountingApi.exchangeRateCoverage).toHaveBeenCalled())
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument()
  })

  it('says nothing when no conversion happens at all', async () => {
    // `earliest: null` is the server saying the display currency and every
    // currency this user holds are the base currency, so every rate is 1.0 on
    // every day and no figure was clamped. A single-currency user must never
    // see this note, however far back they look.
    vi.mocked(accountingApi.exchangeRateCoverage).mockResolvedValue({ earliest: null, latest: null })

    renderNote('2019-01-01')

    await waitFor(() => expect(accountingApi.exchangeRateCoverage).toHaveBeenCalled())
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument()
  })
})
