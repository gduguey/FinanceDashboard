import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CategoryDrilldownPie } from '@/components/accounting/CategoryDrilldownPie'
import { accountingApi } from '@/lib/accountingApi'
import { noPostingFilters } from '@/lib/transactionFilters'
import type { Account, CategoryTotalRow, Posting, PostingPage } from '@/types/accounting'

vi.mock('@/lib/accountingApi', () => ({
  accountingApi: {
    postings: vi.fn(),
    postingsPage: vi.fn(),
  },
}))

// The ring's own figure, as `GET /income-statement/category-totals` returns
// it: already converted into the display currency. The rows below are
// deliberately *not* USD and deliberately do not sum to it — that gap is the
// bug this drilldown used to render.
const GROCERIES: CategoryTotalRow = {
  classification: 'expense',
  category_id: 'expense:food',
  category_name: 'Food',
  subcategory_id: 'expense:food:groceries',
  subcategory_name: 'Groceries',
  color: '#dc2626',
  category_color: '#dc2626',
  amount: 300,
}

const ACCOUNTS: Record<string, Account> = {
  'chase:checking': {
    account_id: 'chase:checking',
    name: 'Everyday',
    kind: 'checking',
    institution: 'Chase',
    currency: 'USD',
    last_four: null,
    parent_account_id: null,
    broker_connection_id: null,
    meta: {},
    closed: false,
  },
}

function posting(overrides: Partial<Posting> & Pick<Posting, 'posting_id'>): Posting {
  return {
    transaction_id: `t-${overrides.posting_id}`,
    account_id: 'chase:checking',
    posted_at: '2026-03-04T00:00:00',
    amount: -100,
    currency: 'EUR',
    description: 'Supermarket',
    category_id: 'expense:food',
    subcategory_id: 'expense:food:groceries',
    pending_selected: true,
    is_linked_transfer: false,
    is_real_income_expense: true,
    is_excluded_from_rule: false,
    ...overrides,
  }
}

function page(items: Posting[], total: number, offset = 0): PostingPage {
  return {
    items,
    window_unit: 'transaction',
    total,
    limit: 50,
    offset,
    counts: {
      matched_transactions: total,
      matched_postings: total,
      needs_categorizing: 0,
      pending: 0,
      pending_selected: 0,
    },
  }
}

const SCOPE = {
  ...noPostingFilters(),
  start: '2026-03-01',
  end: '2026-03-31',
  account: 'chase:checking',
  tags: ['tag:travel'],
}

function renderPie(totals: CategoryTotalRow[] = [GROCERIES]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return render(
    <CategoryDrilldownPie
      categoryTotals={totals}
      scope={SCOPE}
      accounts={ACCOUNTS}
      isLoading={false}
      displayCurrency="USD"
    />,
    { wrapper: Wrapper },
  )
}

// Recharts renders nothing measurable under jsdom, so the drilldown is driven
// through the legend — the same `handleSliceClick` the pie's own cells call,
// and the only place a slice's name is readable without hovering it anyway.
async function drillIntoGroceries() {
  await userEvent.click(await screen.findByRole('button', { name: /Expense/ }))
  await userEvent.click(await screen.findByRole('button', { name: /Food/ }))
  await userEvent.click(await screen.findByRole('button', { name: /Groceries/ }))
}

describe('the insights drilldown', () => {
  beforeEach(() => {
    vi.mocked(accountingApi.postingsPage).mockResolvedValue(page([posting({ posting_id: 'p1' })], 120))
  })

  it('asks the server for the slice rather than scoping a resident ledger', async () => {
    renderPie()
    await drillIntoGroceries()

    await screen.findByText(/Supermarket/)
    expect(accountingApi.postingsPage).toHaveBeenCalledWith(
      expect.objectContaining({
        start: '2026-03-01',
        end: '2026-03-31',
        account: 'chase:checking',
        tags: ['tag:travel'],
        categories: ['expense:food'],
        subcategories: ['expense:food:groceries'],
        income_expense: 'expense',
        limit: 50,
        offset: 0,
      }),
    )
  })

  it('never fetches the whole ledger', async () => {
    renderPie()
    await drillIntoGroceries()
    await screen.findByText(/Supermarket/)

    expect(accountingApi.postings).not.toHaveBeenCalled()
  })

  // The regression this block exists for. `total` was `sum(|amount|)` over the
  // rows on screen — each in its own native currency — printed as the display
  // currency. Two EUR rows of 100 rendered "$200.00" under a ring slice
  // reading $300.00, and no type or test disagreed.
  it('reports the ring slice’s converted total, not the sum of native amounts', async () => {
    vi.mocked(accountingApi.postingsPage).mockResolvedValue(
      page([posting({ posting_id: 'p1' }), posting({ posting_id: 'p2' })], 2),
    )
    renderPie()
    await drillIntoGroceries()

    expect(await screen.findByText(/2 rows — total \$300\.00/)).toBeInTheDocument()
  })

  it('withholds a percentage from a row that is not in the display currency', async () => {
    vi.mocked(accountingApi.postingsPage).mockResolvedValue(
      page([posting({ posting_id: 'p1', currency: 'EUR' }), posting({ posting_id: 'p2', currency: 'USD' })], 2),
    )
    renderPie()
    await drillIntoGroceries()

    await screen.findByText(/2 rows/)
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.getByText('33.3%')).toBeInTheDocument()
  })

  // `GET /postings` windows by transaction and returns every leg of each one:
  // the placeholder counterparty always, and — for a split — a leg filed under
  // a different subcategory entirely. Listing either under this heading is a
  // wrong row, not an extra one.
  it('lists only the legs the slice is about, out of a page cut by transaction', async () => {
    vi.mocked(accountingApi.postingsPage).mockResolvedValue(
      page(
        [
          posting({ posting_id: 'p1', description: 'Supermarket', category_id: 'expense:food' }),
          posting({
            posting_id: 'p1:split:1',
            description: 'Wine with the shop',
            category_id: 'expense:food',
            subcategory_id: 'expense:food:dining',
          }),
          posting({ posting_id: 'p1:counterparty', description: 'Supermarket', account_id: 'uncategorized:expense' }),
        ],
        1,
      ),
    )
    renderPie()
    await drillIntoGroceries()

    expect(await screen.findByText('Supermarket')).toBeInTheDocument()
    expect(screen.queryByText('Wine with the shop')).not.toBeInTheDocument()
    expect(screen.queryByText('uncategorized:expense')).not.toBeInTheDocument()
  })

  it('pages the drilldown instead of listing every matching row', async () => {
    renderPie()
    await drillIntoGroceries()

    expect(await screen.findByText('1–50 of 120 transactions')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Next/ }))

    expect(accountingApi.postingsPage).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 }))
  })

  it('re-sorts by asking the server and returns to the first page', async () => {
    renderPie()
    await drillIntoGroceries()
    await screen.findByText('1–50 of 120 transactions')
    await userEvent.click(screen.getByRole('button', { name: /Next/ }))

    await userEvent.click(screen.getByRole('button', { name: 'Description' }))

    expect(accountingApi.postingsPage).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort: 'description', descending: true, offset: 0 }),
    )
  })

  it('spells “uncategorized” with the wire’s sentinel and narrows by no subcategory', async () => {
    vi.mocked(accountingApi.postingsPage).mockResolvedValue(
      page([posting({ posting_id: 'p1', category_id: null, subcategory_id: null })], 1),
    )
    renderPie([
      {
        ...GROCERIES,
        category_id: 'uncategorized:expense-category',
        category_name: 'Uncategorized',
        subcategory_id: null,
        subcategory_name: null,
      },
    ])
    await userEvent.click(await screen.findByRole('button', { name: /Expense/ }))
    await userEvent.click(await screen.findByRole('button', { name: /^Uncategorized/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Uncategorized \(other\)/ }))

    await screen.findByText(/Supermarket/)
    expect(accountingApi.postingsPage).toHaveBeenCalledWith(
      expect.objectContaining({ categories: ['__uncategorized__'], subcategories: [] }),
    )
  })
})
