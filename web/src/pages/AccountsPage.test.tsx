import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { accountingApi } from '@/lib/accountingApi'
import { AccountsPage } from '@/pages/AccountsPage'
import { emptyStore } from '@/test/fixtures'
import type { Account } from '@/types/accounting'

// The whole client is stubbed rather than the calls under test, so "which
// requests does this page make" is an assertion the test can make at all —
// the point here is a request that must *not* happen any more.
vi.mock('@/lib/accountingApi', () => ({
  accountingApi: {
    store: vi.fn(),
    postingsExport: vi.fn(),
    netWorth: vi.fn(),
    currencies: vi.fn(),
  },
}))

function account(overrides: Partial<Account> & Pick<Account, 'account_id' | 'name'>): Account {
  return {
    kind: 'checking',
    institution: 'Chase',
    currency: 'USD',
    last_four: null,
    parent_account_id: null,
    broker_connection_id: null,
    meta: {},
    closed: false,
    ...overrides,
  }
}

const USED = account({ account_id: 'chase:checking', name: 'Everyday' })
const UNUSED = account({ account_id: 'chase:savings', name: 'Rainy day' })

function renderAccountsPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }
  return render(<AccountsPage />, { wrapper: Wrapper })
}

describe('AccountsPage’s kind/currency lock', () => {
  beforeEach(() => {
    vi.mocked(accountingApi.store).mockResolvedValue(
      emptyStore({
        accounts: { [USED.account_id]: USED, [UNUSED.account_id]: UNUSED },
        account_ids_with_postings: [USED.account_id],
      }),
    )
    vi.mocked(accountingApi.postingsExport).mockResolvedValue([])
    vi.mocked(accountingApi.netWorth).mockResolvedValue({
      as_of: '2026-01-01',
      display_currency: 'USD',
      assets: 0,
      liabilities: 0,
      other_assets_total: 0,
      net_worth: 0,
      accounts: [],
      other_assets: [],
    })
    vi.mocked(accountingApi.currencies).mockResolvedValue([])
  })

  it('locks the accounts the store says have postings, and only those', async () => {
    renderAccountsPage()

    expect(await screen.findByRole('button', { name: 'Delete Rainy day' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete Everyday' })).not.toBeInTheDocument()
  })

  it('never fetches the ledger to learn which those are', async () => {
    renderAccountsPage()
    await waitFor(() => expect(accountingApi.store).toHaveBeenCalled())
    await screen.findByRole('button', { name: 'Delete Rainy day' })

    expect(accountingApi.postingsExport).not.toHaveBeenCalled()
  })
})
