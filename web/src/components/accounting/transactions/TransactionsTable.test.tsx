import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TransactionsTable } from '@/components/accounting/transactions/TransactionsTable'
import type { Account, Category, Posting, Tag, TransferLink, TransferRule } from '@/types/accounting'

function makeAccount(accountId: string, kind: Account['kind'], name: string): Account {
  return { account_id: accountId, kind, name, institution: 'Test Bank', currency: 'USD', closed: false } as Account
}

const ACCOUNTS: Record<string, Account> = {
  checking: makeAccount('checking', 'checking', 'Everyday'),
  savings: makeAccount('savings', 'savings', 'Rainy Day'),
  'uncategorized:expense': makeAccount('uncategorized:expense', 'expense_payee', 'Uncategorized expense'),
  'uncategorized:income': makeAccount('uncategorized:income', 'income_source', 'Uncategorized income'),
}

function makePosting(overrides: Partial<Posting> & Pick<Posting, 'posting_id' | 'transaction_id'>): Posting {
  return {
    account_id: 'checking',
    posted_at: '2026-01-15T00:00:00',
    amount: -25,
    currency: 'USD',
    description: 'Corner Store',
    category_id: null,
    subcategory_id: null,
    tag_ids: [],
    pending_source: null,
    pending_selected: true,
    is_linked_transfer: false,
    ...overrides,
  } as Posting
}

/** One expense: a real leg on the checking account, its counterparty still a placeholder. */
function expense(id: string, description: string, extra: Partial<Posting> = {}): Posting[] {
  return [
    makePosting({ posting_id: id, transaction_id: `t:${id}`, description, ...extra }),
    makePosting({
      posting_id: `${id}:other`,
      transaction_id: `t:${id}`,
      account_id: 'uncategorized:expense',
      amount: 25,
      description,
    }),
  ]
}

function renderTable(postings: Posting[], options: { transferLinks?: TransferLink[]; rules?: TransferRule[] } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }
  return render(
    <TransactionsTable
      storageKey={`test.filters.${Math.random()}`}
      postings={postings}
      accounts={ACCOUNTS}
      categories={{} as Record<string, Category>}
      tags={{} as Record<string, Tag>}
      rules={options.rules ?? []}
      transferLinks={options.transferLinks ?? []}
      onlyUncategorized={false}
    />,
    { wrapper: Wrapper },
  )
}

function renderedDescriptions(): string[] {
  return screen
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell')[3]?.textContent ?? '')
}

describe('TransactionsTable', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () => new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      ),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows one row per real leg and none for the placeholder counterparties', () => {
    renderTable([
      ...expense('a', 'Corner Store', { posted_at: '2026-01-10T00:00:00' }),
      ...expense('b', 'Rent', { posted_at: '2026-02-01T00:00:00' }),
    ])

    expect(screen.getByText('2 transactions')).toBeInTheDocument()
    // Newest first, the order the page window itself is cut in.
    expect(renderedDescriptions()).toEqual(['Rent', 'Corner Store'])
  })

  it('says so when nothing has been imported', () => {
    renderTable([])

    expect(screen.getByText(/import a statement to start/)).toBeInTheDocument()
  })

  it('narrows to the rows matching what is typed in the search box', async () => {
    const user = userEvent.setup()
    renderTable([...expense('a', 'Corner Store'), ...expense('b', 'Rent')])

    await user.type(screen.getByPlaceholderText('Search description…'), 'rent')

    expect(renderedDescriptions()).toEqual(['Rent'])
    expect(screen.getByText('1 transaction')).toBeInTheDocument()
  })

  it('offers to reset once a filter is narrowing the table', async () => {
    const user = userEvent.setup()
    renderTable([...expense('a', 'Corner Store'), ...expense('b', 'Rent')])
    expect(screen.queryByRole('button', { name: /reset filters/i })).not.toBeInTheDocument()

    await user.type(screen.getByPlaceholderText('Search description…'), 'rent')

    // Typing in the search box alone is not one of the counted filters, so the
    // reset button stays hidden until a picker is used.
    expect(screen.queryByRole('button', { name: /reset filters/i })).not.toBeInTheDocument()
  })

  describe('the pending-suggestion checkboxes', () => {
    const postings = [
      ...expense('a', 'Corner Store', { pending_source: 'ai', pending_selected: true }),
      ...expense('b', 'Rent', { pending_source: 'pattern', pending_selected: false }),
      ...expense('c', 'Salary'),
    ]

    it('offers a select-all only when something in view is pending', () => {
      renderTable(postings)

      expect(screen.getByLabelText('Select all pending suggestions in view')).toBeInTheDocument()
    })

    it('hides the select-all when nothing in view is pending', () => {
      renderTable(expense('c', 'Salary'))

      expect(screen.queryByLabelText('Select all pending suggestions in view')).not.toBeInTheDocument()
    })

    it('reads as indeterminate when only some pending rows are kept', () => {
      renderTable(postings)
      const selectAll = screen.getByLabelText<HTMLInputElement>('Select all pending suggestions in view')

      expect(selectAll.indeterminate).toBe(true)
      expect(selectAll.checked).toBe(false)
    })

    it('reads as checked once every pending row is kept', () => {
      renderTable([
        ...expense('a', 'Corner Store', { pending_source: 'ai', pending_selected: true }),
        ...expense('b', 'Rent', { pending_source: 'pattern', pending_selected: true }),
      ])
      const selectAll = screen.getByLabelText<HTMLInputElement>('Select all pending suggestions in view')

      expect(selectAll.indeterminate).toBe(false)
      expect(selectAll.checked).toBe(true)
    })

    it('counts the kept suggestions on the validate button', () => {
      renderTable(postings)

      expect(screen.getByRole('button', { name: 'Validate selection (1/2)' })).toBeInTheDocument()
    })

    it('gives a row with no suggestion no checkbox to keep', () => {
      renderTable(expense('c', 'Salary'))

      expect(screen.queryByLabelText('Keep this suggestion')).not.toBeInTheDocument()
    })
  })

  it('badges a linked transaction with the account on the other side', () => {
    const link = { link_id: 'l1', transaction_id_a: 't:a', transaction_id_b: 't:b' } as TransferLink
    const postings = [
      makePosting({
        posting_id: 'a',
        transaction_id: 't:a',
        account_id: 'checking',
        amount: -100,
        description: 'Moved out',
        is_linked_transfer: true,
        linked_transaction_id: 't:b',
        transfer_link_source: 'manual',
      }),
      makePosting({
        posting_id: 'b',
        transaction_id: 't:b',
        account_id: 'savings',
        amount: 100,
        description: 'Moved in',
        is_linked_transfer: true,
        linked_transaction_id: 't:a',
        transfer_link_source: 'manual',
      }),
    ]

    renderTable(postings, { transferLinks: [link] })

    expect(screen.getByRole('button', { name: /Transfer to Rainy Day/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Transfer from Everyday/ })).toBeInTheDocument()
  })

  it('opens the transfer detail from the badge', async () => {
    const user = userEvent.setup()
    const link = { link_id: 'l1', transaction_id_a: 't:a', transaction_id_b: 't:b' } as TransferLink
    const postings = [
      makePosting({
        posting_id: 'a',
        transaction_id: 't:a',
        amount: -100,
        description: 'Moved out',
        is_linked_transfer: true,
        linked_transaction_id: 't:b',
        transfer_link_source: 'manual',
      }),
      makePosting({
        posting_id: 'b',
        transaction_id: 't:b',
        account_id: 'savings',
        amount: 100,
        description: 'Moved in',
        is_linked_transfer: true,
        linked_transaction_id: 't:a',
        transfer_link_source: 'manual',
      }),
    ]
    renderTable(postings, { transferLinks: [link] })

    await user.click(screen.getByRole('button', { name: /Transfer to Rainy Day/ }))

    expect(screen.getByRole('dialog')).toHaveTextContent('This transfer goes from Everyday to Rainy Day')
    expect(screen.getByRole('button', { name: 'Unmark as transfer' })).toBeInTheDocument()
  })

  describe('picking a transfer partner', () => {
    const postings = [
      ...expense('a', 'Card payment', { amount: -100 }),
      makePosting({
        posting_id: 'match',
        transaction_id: 't:match',
        account_id: 'savings',
        amount: 100,
        description: 'Incoming',
      }),
      makePosting({
        posting_id: 'other',
        transaction_id: 't:match',
        account_id: 'uncategorized:income',
        amount: -100,
        description: 'Incoming',
      }),
    ]

    // With no safe direct-repoint account to offer as the alternative, the
    // two-choice dialog is skipped and the click goes straight into picking.
    it('asks for the matching amount once a pick starts', async () => {
      const user = userEvent.setup()
      renderTable(postings)

      await user.click(screen.getAllByRole('button', { name: 'Mark as transfer' })[0])

      expect(screen.getByText(/Pick the transaction that matches/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    })

    it('marks the row that cannot match with why', async () => {
      const user = userEvent.setup()
      renderTable([...postings, ...expense('mismatch', 'Nowhere near', { amount: -3 })])

      await user.click(screen.getAllByRole('button', { name: 'Mark as transfer' })[0])

      const row = screen.getByText('Nowhere near').closest('tr')
      expect(row).toHaveAttribute('title', expect.stringContaining("Amounts don't match"))
    })

    it('leaves pick mode on Cancel', async () => {
      const user = userEvent.setup()
      renderTable(postings)
      await user.click(screen.getAllByRole('button', { name: 'Mark as transfer' })[0])

      await user.click(screen.getByRole('button', { name: 'Cancel' }))

      expect(screen.queryByText(/Pick the transaction that matches/)).not.toBeInTheDocument()
    })
  })
})
