import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TransactionsTable } from '@/components/accounting/transactions/TransactionsTable'
import { accountingApi } from '@/lib/accountingApi'
import type {
  Account,
  Category,
  Posting,
  PostingPage,
  PostingPageCounts,
  Tag,
  TransferLink,
  TransferRule,
} from '@/types/accounting'

// The client is stubbed wholesale so the table's own requests are observable.
// Which query it sends is now the behaviour under test — the filter is no
// longer applied here at all, so "does the right set come back" has moved
// entirely into "is the right query sent".
vi.mock('@/lib/accountingApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/accountingApi')>()),
  accountingApi: {
    postingsPage: vi.fn(),
    postingMonths: vi.fn(),
    llmUsage: vi.fn(),
    putPostingOverride: vi.fn(),
    validatePending: vi.fn(),
    patternSuggestCategoryBulk: vi.fn(),
    matchingPostingIds: vi.fn(),
    aiSuggestCategory: vi.fn(),
  },
}))

// The app's `<Toaster />` lives in `App.tsx`, above anything this test
// renders, so the toast is asserted at the call rather than in the DOM.
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

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
    is_real_income_expense: false,
    is_excluded_from_rule: false,
    linked_leg: null,
    ...overrides,
  } as Posting
}

/** One expense: a real leg on the checking account, its counterparty still a placeholder. */
function expense(id: string, description: string, extra: Partial<Posting> = {}): Posting[] {
  return [
    makePosting({
      posting_id: id,
      transaction_id: `t:${id}`,
      description,
      is_real_income_expense: true,
      ...extra,
    }),
    makePosting({
      posting_id: `${id}:other`,
      transaction_id: `t:${id}`,
      account_id: 'uncategorized:expense',
      amount: 25,
      description,
    }),
  ]
}

const PLACEHOLDERS = new Set(['uncategorized:expense', 'uncategorized:income'])

/**
 * Build the page the server would answer with for these rows.
 *
 * The counts mirror what `projection.filtered_page` computes — rendered rows
 * only, placeholders excluded — so a component reading a count the wrong way
 * round shows up here rather than agreeing with a fixture that made the same
 * mistake.
 */
function pageOf(postings: Posting[], counts: Partial<PostingPageCounts> = {}, window = {}): PostingPage {
  const rendered = postings.filter((posting) => !PLACEHOLDERS.has(posting.account_id))
  const pending = rendered.filter((posting) => posting.pending_source !== null)
  return {
    items: postings,
    window_unit: 'transaction',
    total: new Set(rendered.map((posting) => posting.transaction_id)).size,
    limit: 200,
    offset: 0,
    counts: {
      matched_transactions: new Set(rendered.map((posting) => posting.transaction_id)).size,
      matched_postings: rendered.length,
      needs_categorizing: rendered.filter((posting) => posting.is_real_income_expense && !posting.category_id).length,
      pending: pending.length,
      pending_selected: pending.filter((posting) => posting.pending_selected).length,
      ...counts,
    },
    ...window,
  } as PostingPage
}

function renderTable(
  postings: Posting[],
  options: { transferLinks?: TransferLink[]; rules?: TransferRule[]; page?: PostingPage } = {},
) {
  vi.mocked(accountingApi.postingsPage).mockResolvedValue(options.page ?? pageOf(postings))
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

/** The query behind the most recent `GET /postings`, which is what the table is actually showing. */
function lastQuery() {
  const calls = vi.mocked(accountingApi.postingsPage).mock.calls
  return calls[calls.length - 1][0]
}

async function rendered(descriptions: string[]) {
  await waitFor(() => expect(renderedDescriptions()).toEqual(descriptions))
}

describe('TransactionsTable', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    vi.mocked(accountingApi.postingMonths).mockResolvedValue(['2026-02', '2026-01'])
    vi.mocked(accountingApi.llmUsage).mockResolvedValue({ providers: [] } as never)
    vi.mocked(accountingApi.putPostingOverride).mockResolvedValue({} as never)
  })

  it('shows one row per real leg and none for the placeholder counterparties', async () => {
    renderTable([
      ...expense('a', 'Corner Store', { posted_at: '2026-01-10T00:00:00' }),
      ...expense('b', 'Rent', { posted_at: '2026-02-01T00:00:00' }),
    ])

    // The order is the server's — the page arrives sorted and nothing re-sorts it.
    await rendered(['Corner Store', 'Rent'])
    expect(screen.getByText('2 rows')).toBeInTheDocument()
  })

  it('says so when nothing has been imported', async () => {
    renderTable([])

    expect(await screen.findByText(/import a statement to start/)).toBeInTheDocument()
  })

  describe('what it asks the server for', () => {
    it('sends the default query with nothing restricting it', async () => {
      renderTable(expense('a', 'Corner Store'))
      await rendered(['Corner Store'])

      expect(lastQuery()).toMatchObject({
        search: '',
        account: null,
        categories: [],
        month: null,
        needs_categorizing: false,
        sort: 'posted_at',
        descending: true,
        limit: 200,
        offset: 0,
      })
    })

    // The filter is not applied here any more, so this is the whole of the
    // assertion that a search narrows the table: the term reaches the server.
    it('re-requests with the search term rather than filtering what it holds', async () => {
      const user = userEvent.setup()
      renderTable(expense('a', 'Corner Store'))
      await rendered(['Corner Store'])

      await user.type(screen.getByPlaceholderText('Search description…'), 'rent')

      await waitFor(() => expect(lastQuery().search).toBe('rent'))
    })

    it('re-requests when a column heading changes the sort', async () => {
      const user = userEvent.setup()
      renderTable(expense('a', 'Corner Store'))
      await rendered(['Corner Store'])

      await user.click(screen.getByRole('button', { name: /Amount/ }))

      await waitFor(() => expect(lastQuery()).toMatchObject({ sort: 'amount', descending: true }))
      await user.click(screen.getByRole('button', { name: /Amount/ }))
      await waitFor(() => expect(lastQuery()).toMatchObject({ sort: 'amount', descending: false }))
    })

    it('reads its month options off the months endpoint, not off the rows', async () => {
      renderTable(expense('a', 'Corner Store', { posted_at: '2026-01-10T00:00:00' }))
      await rendered(['Corner Store'])

      expect(accountingApi.postingMonths).toHaveBeenCalled()
    })
  })

  describe('paging', () => {
    it('always says which slice of the collection is on screen', async () => {
      const postings = expense('a', 'Corner Store')
      renderTable(postings, { page: { ...pageOf(postings), total: 640, limit: 200, offset: 0 } })

      expect(await screen.findByText('1–200 of 640 transactions')).toBeInTheDocument()
    })

    it('asks for the next window rather than assuming it already holds it', async () => {
      const user = userEvent.setup()
      const postings = expense('a', 'Corner Store')
      renderTable(postings, { page: { ...pageOf(postings), total: 640, limit: 200, offset: 0 } })
      await rendered(['Corner Store'])

      await user.click(screen.getByRole('button', { name: /Next/ }))

      await waitFor(() => expect(lastQuery().offset).toBe(200))
    })

    // A filter narrowing the collection below the current offset would
    // otherwise land on an empty window that reads as "nothing matches".
    it('returns to the first page when the query changes', async () => {
      const user = userEvent.setup()
      const postings = expense('a', 'Corner Store')
      renderTable(postings, { page: { ...pageOf(postings), total: 640, limit: 200, offset: 0 } })
      await rendered(['Corner Store'])
      await user.click(screen.getByRole('button', { name: /Next/ }))
      await waitFor(() => expect(lastQuery().offset).toBe(200))

      await user.type(screen.getByPlaceholderText('Search description…'), 'rent')

      await waitFor(() => expect(lastQuery()).toMatchObject({ search: 'rent', offset: 0 }))
    })

    it('never renders an empty table as "nothing matches" when the read failed', async () => {
      vi.mocked(accountingApi.postingsPage).mockRejectedValue(new Error('nope'))
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TransactionsTable
              storageKey="test.filters.error"
              accounts={ACCOUNTS}
              categories={{} as Record<string, Category>}
              tags={{} as Record<string, Tag>}
              rules={[]}
              transferLinks={[]}
              onlyUncategorized={false}
            />
          </MemoryRouter>
        </QueryClientProvider>,
      )

      expect(await screen.findByText(/would otherwise be missing rows/)).toBeInTheDocument()
      expect(screen.queryByText(/No transactions match/)).not.toBeInTheDocument()
    })
  })

  // The setter behind the persisted filters runs `JSON.stringify` plus
  // `localStorage.setItem` and notifies every subscriber, synchronously, per
  // call — which is why a half-typed query does not go through it.
  it('never writes the search term to persisted state', async () => {
    const user = userEvent.setup()
    renderTable(expense('a', 'Corner Store'))
    await rendered(['Corner Store'])
    const setItem = vi.spyOn(Storage.prototype, 'setItem')

    await user.type(screen.getByPlaceholderText('Search description…'), 'rent')

    expect(setItem).not.toHaveBeenCalled()
    setItem.mockRestore()
  })

  describe('the pending-suggestion checkboxes', () => {
    const postings = [
      ...expense('a', 'Corner Store', { pending_source: 'ai', pending_selected: true }),
      ...expense('b', 'Rent', { pending_source: 'pattern', pending_selected: false }),
      ...expense('c', 'Salary'),
    ]

    it('offers a select-all only when something on the page is pending', async () => {
      renderTable(postings)

      expect(await screen.findByLabelText('Select all pending suggestions on this page')).toBeInTheDocument()
    })

    it('hides the select-all when nothing on the page is pending', async () => {
      renderTable(expense('c', 'Salary'))
      await rendered(['Salary'])

      expect(screen.queryByLabelText('Select all pending suggestions on this page')).not.toBeInTheDocument()
    })

    it('reads as indeterminate when only some pending rows are kept', async () => {
      renderTable(postings)
      const selectAll = await screen.findByLabelText<HTMLInputElement>('Select all pending suggestions on this page')

      expect(selectAll.indeterminate).toBe(true)
      expect(selectAll.checked).toBe(false)
    })

    it('reads as checked once every pending row is kept', async () => {
      renderTable([
        ...expense('a', 'Corner Store', { pending_source: 'ai', pending_selected: true }),
        ...expense('b', 'Rent', { pending_source: 'pattern', pending_selected: true }),
      ])
      const selectAll = await screen.findByLabelText<HTMLInputElement>('Select all pending suggestions on this page')

      expect(selectAll.indeterminate).toBe(false)
      expect(selectAll.checked).toBe(true)
    })

    // The button acts on the filter, not on the page, so its count has to come
    // from the server's own tally of the filter. Counting the rendered rows
    // would label a click that resolves hundreds with the size of one page.
    it('counts the whole filter on the validate button, not the page', async () => {
      const page = pageOf(postings, { pending: 57, pending_selected: 40 })
      renderTable(postings, { page })

      expect(await screen.findByRole('button', { name: 'Validate matching (40/57)' })).toBeInTheDocument()
    })

    it('gives a row with no suggestion no checkbox to keep', async () => {
      renderTable(expense('c', 'Salary'))
      await rendered(['Salary'])

      expect(screen.queryByLabelText('Keep this suggestion')).not.toBeInTheDocument()
    })
  })

  describe('the bulk actions', () => {
    const postings = [
      ...expense('a', 'Corner Store', { pending_source: 'ai', pending_selected: true }),
      ...expense('b', 'Rent'),
    ]

    it('validates by filter and separates the set it ran over from what it changed', async () => {
      const user = userEvent.setup()
      vi.mocked(accountingApi.validatePending).mockResolvedValue({ matched: 57, accepted: 40, reverted: 17 })
      renderTable(postings, { page: pageOf(postings, { pending: 57, pending_selected: 40 }) })
      await rendered(['Corner Store', 'Rent'])

      await user.click(screen.getByRole('button', { name: /Validate matching/ }))

      await waitFor(() => expect(accountingApi.validatePending).toHaveBeenCalled())
      const sent = vi.mocked(accountingApi.validatePending).mock.calls[0][0]
      expect(sent).toMatchObject({ search: '', categories: [] })
      // The window never travels with a bulk action — it is not scoped to a page.
      expect(sent).not.toHaveProperty('limit')
      expect(sent).not.toHaveProperty('offset')
      // Reports the server's own `matched`, not the page's row count — the
      // whole reason that field is on the response. And says "checked", not
      // "resolved": `matched` is the set the action ran over, and a matching
      // row carrying no pending suggestion is skipped, so 57 is not 57
      // suggestions — 40 and 17 are what moved.
      await waitFor(() =>
        expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('Checked 57 matching rows')),
      )
    })

    // Without this the server widens the set from "the rows this button
    // counts" to "every row the filter bar matches", categorized ones
    // included — a bulk categorizer silently acting on far more than it said.
    it('narrows the pattern suggester to the rows it counted', async () => {
      const user = userEvent.setup()
      vi.mocked(accountingApi.patternSuggestCategoryBulk).mockResolvedValue({ matched: 2, applied: 1 })
      renderTable(postings)
      await rendered(['Corner Store', 'Rent'])

      await user.click(screen.getByRole('button', { name: /Run pattern suggestions/ }))

      await waitFor(() => expect(accountingApi.patternSuggestCategoryBulk).toHaveBeenCalled())
      expect(vi.mocked(accountingApi.patternSuggestCategoryBulk).mock.calls[0][0]).toMatchObject({
        needs_categorizing: true,
      })
    })

    // The one bulk action that is genuinely page-bounded, and the reason its
    // button says so. It is a loop of one request per posting, each carrying
    // the category the suggestion is locked against — which only a loaded row
    // knows — so an id the filter matched on another page is skipped. The
    // button used to be labelled with the filter-wide count and promise a
    // number it could not reach.
    it('runs the AI suggester over the page’s matching rows and says so on the button', async () => {
      const user = userEvent.setup()
      vi.mocked(accountingApi.matchingPostingIds).mockResolvedValue(['a', 'somewhere-on-another-page'])
      vi.mocked(accountingApi.aiSuggestCategory).mockResolvedValue({} as never)
      vi.mocked(accountingApi.llmUsage).mockResolvedValue({ gemini: { configured: true, is_limited: false } } as never)
      renderTable(postings, { page: pageOf(postings, { needs_categorizing: 900 }) })
      await rendered(['Corner Store', 'Rent'])

      const button = await screen.findByRole('button', { name: /AI suggest/ })
      expect(button).toHaveTextContent('AI suggest on this page')
      expect(button).not.toHaveTextContent('900')

      await user.click(button)

      await waitFor(() => expect(accountingApi.aiSuggestCategory).toHaveBeenCalled())
      const suggested = vi.mocked(accountingApi.aiSuggestCategory).mock.calls.map(([postingId]) => postingId)
      expect(suggested).toEqual(['a'])
    })
  })

  it('badges a linked transaction from the partner leg the server joined on', async () => {
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
        linked_leg: {
          transaction_id: 't:b',
          account_id: 'savings',
          description: 'Moved in',
          posted_at: '2026-01-15T00:00:00',
          amount: 100,
          currency: 'USD',
        },
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
        linked_leg: {
          transaction_id: 't:a',
          account_id: 'checking',
          description: 'Moved out',
          posted_at: '2026-01-15T00:00:00',
          amount: -100,
          currency: 'USD',
        },
      }),
    ]

    renderTable(postings, { transferLinks: [link] })

    expect(await screen.findByRole('button', { name: /Transfer to Rainy Day/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Transfer from Everyday/ })).toBeInTheDocument()
  })

  // The badge used to be built by scanning the resident ledger for the
  // partner's transaction, so a partner on another page (or hidden by an
  // account filter) left a genuinely-linked row unbadged. `linked_leg` is
  // joined onto the row itself for exactly this case.
  it('badges a linked row whose partner is not on the page', async () => {
    const link = { link_id: 'l1', transaction_id_a: 't:a', transaction_id_b: 't:elsewhere' } as TransferLink
    const postings = [
      makePosting({
        posting_id: 'a',
        transaction_id: 't:a',
        amount: -100,
        description: 'Moved out',
        is_linked_transfer: true,
        linked_transaction_id: 't:elsewhere',
        transfer_link_source: 'manual',
        linked_leg: {
          transaction_id: 't:elsewhere',
          account_id: 'savings',
          description: 'Moved in',
          posted_at: '2025-11-02T00:00:00',
          amount: 100,
          currency: 'USD',
        },
      }),
    ]

    renderTable(postings, { transferLinks: [link] })

    expect(await screen.findByRole('button', { name: /Transfer to Rainy Day/ })).toBeInTheDocument()
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
        linked_leg: {
          transaction_id: 't:b',
          account_id: 'savings',
          description: 'Moved in',
          posted_at: '2026-01-15T00:00:00',
          amount: 100,
          currency: 'USD',
        },
      }),
    ]
    renderTable(postings, { transferLinks: [link] })

    await user.click(await screen.findByRole('button', { name: /Transfer to Rainy Day/ }))

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
    it('asks for the matching amount once a pick starts, and says the search is page-scoped', async () => {
      const user = userEvent.setup()
      renderTable(postings)
      await rendered(['Card payment', 'Incoming'])

      await user.click(screen.getAllByRole('button', { name: /Mark .* as a transfer/ })[0])

      expect(screen.getByText(/Pick the transaction that matches/)).toBeInTheDocument()
      // The scope narrowed from the whole ledger to one page, so it is stated
      // rather than left for the user to infer from an absent match.
      expect(screen.getByText(/Only this page is scored/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    })

    it('marks the row that cannot match with why', async () => {
      const user = userEvent.setup()
      const withMismatch = [...postings, ...expense('mismatch', 'Nowhere near', { amount: -3 })]
      renderTable(withMismatch)
      await rendered(['Card payment', 'Incoming', 'Nowhere near'])

      await user.click(screen.getAllByRole('button', { name: /Mark .* as a transfer/ })[0])

      const row = screen.getByText('Nowhere near').closest('tr')
      expect(row).toHaveAttribute('title', expect.stringContaining("Amounts don't match"))
    })

    it('leaves pick mode on Cancel', async () => {
      const user = userEvent.setup()
      renderTable(postings)
      await rendered(['Card payment', 'Incoming'])
      await user.click(screen.getAllByRole('button', { name: /Mark .* as a transfer/ })[0])

      await user.click(screen.getByRole('button', { name: 'Cancel' }))

      expect(screen.queryByText(/Pick the transaction that matches/)).not.toBeInTheDocument()
    })
  })

  // This table is where the app packs the most icon-only controls into the
  // least space — a row can carry mark-as-transfer, AI-suggest, split/undo,
  // a per-tag remove and a rule-exclusion, all of them a bare glyph. Every
  // one of those was shipped nameless at some point, which is a screen
  // reader hearing "button, button, button" down a column of thousands.
  //
  // Asserted generically rather than label by label on purpose. A test that
  // pinned each expected string would pass a brand-new unnamed button
  // straight through, which is the exact regression worth catching; this one
  // fails the moment any button reachable in this table has nothing to
  // announce, including buttons nobody has written yet.
  describe('accessible names', () => {
    it('gives every button in a fully-loaded row something to announce', async () => {
      renderTable(
        [
          // Uncategorized and tagged: renders mark-as-transfer, AI-suggest,
          // split, and one remove button per tag.
          ...expense('a', 'Corner Store', { tag_ids: ['travel'] }),
          // A split leg — its id is what makes the row offer undo-split
          // instead of split (see `splitOriginalId`).
          ...expense('b:split:1', 'Rent, half'),
          // Resolved by a rule, which adds the exclude-from-rule control.
          ...expense('c', 'Gym', { resolved_by_transfer_rule_id: 'rule-1' }),
        ],
        { rules: [{ rule_id: 'rule-1', description_contains: 'GYM' } as TransferRule] },
      )
      await rendered(['Corner Store', 'Rent, half', 'Gym'])

      const buttons = screen.getAllByRole('button')
      // Guards the assertion below against quietly passing on an empty
      // table: if the fixtures ever stop rendering rows, this fails loudly
      // instead of vacuously succeeding over zero buttons.
      expect(buttons.length).toBeGreaterThan(5)
      for (const button of buttons) {
        // `expect.soft` so one nameless button doesn't hide the rest —
        // the failure output lists every offender in a single run.
        expect.soft(button, `button with no accessible name: ${button.outerHTML}`).toHaveAccessibleName()
      }
    })
  })
})
