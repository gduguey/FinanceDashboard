import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { accountingApi } from '@/lib/accountingApi'
import { RulesPage } from '@/pages/RulesPage'
import { emptyStore } from '@/test/fixtures'
import type { Account, LinkedLeg, TransferLink, TransferRule } from '@/types/accounting'

vi.mock('@/lib/accountingApi', () => ({
  accountingApi: {
    store: vi.fn(),
    postings: vi.fn(),
    transactionLegs: vi.fn(),
    transferSuggestions: vi.fn(),
    currencies: vi.fn(),
  },
}))

function account(accountId: string, name: string): Account {
  return {
    account_id: accountId,
    kind: 'checking',
    name,
    institution: 'Bank',
    currency: 'USD',
    closed: false,
  } as Account
}

function leg(transactionId: string, accountId: string, description: string, amount: number): LinkedLeg {
  return {
    transaction_id: transactionId,
    account_id: accountId,
    description,
    posted_at: '2026-02-01T00:00:00',
    amount,
    currency: 'USD',
  }
}

const ACCOUNTS = { checking: account('checking', 'Everyday'), savings: account('savings', 'Rainy Day') }

const LINK = { link_id: 'l1', transaction_id_a: 't:out', transaction_id_b: 't:in', rule_id: null } as TransferLink
const RULE = {
  rule_id: 'r1',
  description_contains: 'GYM',
  priority: 1,
  active: true,
  version: 1,
  excluded_transaction_ids: ['t:excluded'],
} as TransferRule

function renderRulesPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }
  return render(<RulesPage />, { wrapper: Wrapper })
}

describe('RulesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(accountingApi.store).mockResolvedValue(
      emptyStore({ accounts: ACCOUNTS, transfer_links: [LINK], transfer_rules: [RULE] }),
    )
    vi.mocked(accountingApi.transactionLegs).mockResolvedValue({
      't:out': leg('t:out', 'checking', 'Moved out', -100),
      't:in': leg('t:in', 'savings', 'Moved in', 100),
      't:excluded': leg('t:excluded', 'checking', 'Gym, not a transfer', -40),
    })
    vi.mocked(accountingApi.transferSuggestions).mockResolvedValue([])
    vi.mocked(accountingApi.currencies).mockResolvedValue([])
  })

  // The union of what the tabs need, asked for once. Previously the page read
  // the whole ledger and indexed it, which is the fetch C1 set out to remove.
  it('asks only for the transactions its tabs actually name', async () => {
    renderRulesPage()

    await waitFor(() => expect(accountingApi.transactionLegs).toHaveBeenCalled())
    expect(vi.mocked(accountingApi.transactionLegs).mock.calls[0][0]).toEqual(['t:excluded', 't:in', 't:out'])
  })

  it('never fetches the ledger', async () => {
    renderRulesPage()

    await waitFor(() => expect(accountingApi.transactionLegs).toHaveBeenCalled())
    expect(accountingApi.postings).not.toHaveBeenCalled()
  })

  // A fresh install has no links and no exclusions, so there is nothing to
  // look up and no reason to ask.
  it('asks for nothing when no rule or link names a transaction', async () => {
    vi.mocked(accountingApi.store).mockResolvedValue(emptyStore({ accounts: ACCOUNTS }))
    renderRulesPage()

    await waitFor(() => expect(screen.getByRole('tab', { name: 'Rules' })).toBeInTheDocument())
    expect(accountingApi.transactionLegs).not.toHaveBeenCalled()
  })

  it('renders a manual pair from the legs the server returned', async () => {
    renderRulesPage()

    await waitFor(() => expect(accountingApi.transactionLegs).toHaveBeenCalled())
    const manual = screen.getByRole('tab', { name: /Manually added transfers/ })
    manual.click()

    expect(await screen.findByText('Moved out')).toBeInTheDocument()
    expect(screen.getByText('Moved in')).toBeInTheDocument()
  })
})
