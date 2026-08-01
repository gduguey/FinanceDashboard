import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useReorderWithdrawalAutomations } from '@/hooks/accounting/goals'
import { keys } from '@/hooks/accounting/keys'
import { useSetPostingOverride } from '@/hooks/accounting/postings'
import { useSetBudget } from '@/hooks/accounting/taxonomy'
import { emptyStore } from '@/test/fixtures'
import type { AccountingStore, Budget, GoalAutomation, Posting, PostingPage } from '@/types/accounting'

function posting(id: string, fields: Partial<Posting> = {}): Posting {
  return {
    posting_id: id,
    transaction_id: `t:${id}`,
    account_id: 'checking',
    posted_at: '2024-06-01T00:00:00',
    amount: -12,
    currency: 'USD',
    category_id: null,
    subcategory_id: null,
    tag_ids: [],
    description: 'Coffee',
    pending_source: 'ai',
    pending_selected: true,
    ...fields,
  } as Posting
}

function page(items: Posting[]): PostingPage {
  return {
    items,
    window_unit: 'transaction',
    total: items.length,
    limit: 200,
    offset: 0,
    counts: {
      matched_transactions: items.length,
      matched_postings: items.length,
      needs_categorizing: 0,
      pending: 0,
      pending_selected: 0,
    },
  } as PostingPage
}

function budget(id: string, fields: Partial<Budget>): Budget {
  return { budget_id: id, month: null, category_id: 'food', subcategory_id: null, amount: 100, ...fields } as Budget
}

function automation(id: string, priority: number): GoalAutomation {
  return { automation_id: id, priority, direction: 'withdrawal' } as GoalAutomation
}

describe('optimistic paints', () => {
  let queryClient: QueryClient

  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () => new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      ),
    )
    queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }

  function store() {
    return queryClient.getQueryData<AccountingStore>(keys.store)
  }

  describe('useSetPostingOverride', () => {
    // Two cached pages of the same collection, which is the shape the cutover
    // produced: the table holds one page and the cache holds every page
    // visited under this filter and sort. `p1` is on both, because a
    // categorizing click has to paint every copy of the row it changed.
    const FIRST_PAGE = { sort: 'posted_at', offset: 0 }
    const SECOND_PAGE = { sort: 'posted_at', offset: 200 }

    beforeEach(() => {
      queryClient.setQueryData(keys.postingsPage(FIRST_PAGE), page([posting('p1'), posting('p2')]))
      queryClient.setQueryData(keys.postingsPage(SECOND_PAGE), page([posting('p1'), posting('p3')]))
    })

    function postings(window: object = FIRST_PAGE) {
      return queryClient.getQueryData<PostingPage>(keys.postingsPage(window))?.items ?? []
    }

    it('shows the picked category on the row that was clicked', async () => {
      const { result } = renderHook(() => useSetPostingOverride(), { wrapper })

      await act(async () => {
        await result.current.mutateAsync({
          postingId: 'p1',
          override: { category_id: 'food', subcategory_id: null },
        })
      })

      expect(postings()[0].category_id).toBe('food')
      expect(postings()[1].category_id).toBeNull()
    })

    // Painting only the page on screen would leave a page the user pages back
    // to still showing the old category until something refetched it.
    it('paints the same row on every cached page it appears on', async () => {
      const { result } = renderHook(() => useSetPostingOverride(), { wrapper })

      await act(async () => {
        await result.current.mutateAsync({ postingId: 'p1', override: { category_id: 'food' } })
      })

      expect(postings(SECOND_PAGE)[0].category_id).toBe('food')
      expect(postings(SECOND_PAGE)[1].category_id).toBeNull()
    })

    // The count is a fact about the whole filter that only the server can
    // compute — guessing it here would be a second copy of
    // `projection._needs_categorizing`, which is the duplication C1 removed.
    it('leaves the page counts for the refetch to correct', async () => {
      queryClient.setQueryData(keys.postingsPage(FIRST_PAGE), {
        ...page([posting('p1'), posting('p2')]),
        counts: {
          matched_transactions: 2,
          matched_postings: 2,
          needs_categorizing: 2,
          pending: 2,
          pending_selected: 2,
        },
      })
      const { result } = renderHook(() => useSetPostingOverride(), { wrapper })

      await act(async () => {
        await result.current.mutateAsync({ postingId: 'p1', override: { category_id: 'food' } })
      })

      const cached = queryClient.getQueryData<PostingPage>(keys.postingsPage(FIRST_PAGE))
      expect(cached?.counts.needs_categorizing).toBe(2)
    })

    it('leaves a field the override did not mention alone', async () => {
      const { result } = renderHook(() => useSetPostingOverride(), { wrapper })

      await act(async () => {
        await result.current.mutateAsync({ postingId: 'p1', override: { pending_selected: false } })
      })

      expect(postings()[0].pending_selected).toBe(false)
      expect(postings()[0].description).toBe('Coffee')
    })

    // Repointing a placeholder leg is resolved server-side across both legs of
    // the transaction; painting it here would guess at a transfer badge.
    it('waits for the server on an account repoint', async () => {
      const { result } = renderHook(() => useSetPostingOverride(), { wrapper })

      await act(async () => {
        await result.current.mutateAsync({ postingId: 'p1', override: { account_id: 'savings' } })
      })

      expect(postings()[0].account_id).toBe('checking')
    })
  })

  describe('useSetBudget', () => {
    it('shows a retyped amount on the cell it belongs to', async () => {
      queryClient.setQueryData(
        keys.store,
        emptyStore({ budgets: [budget('b1', { amount: 100 }), budget('b2', { category_id: 'rent', amount: 900 })] }),
      )
      const { result } = renderHook(() => useSetBudget(), { wrapper })

      await act(async () => {
        await result.current.mutateAsync({
          month: null,
          category_id: 'food',
          subcategory_id: null,
          amount: 150,
          currency: 'USD',
        })
      })

      expect(store()?.budgets.map((entry) => entry.amount)).toEqual([150, 900])
    })

    // A cell with no target yet has no server-derived `budget_id` to paint
    // under, so the first amount typed into it appears on the refetch.
    it('adds nothing for a cell that has no target yet', async () => {
      queryClient.setQueryData(keys.store, emptyStore({ budgets: [budget('b1', { amount: 100 })] }))
      const { result } = renderHook(() => useSetBudget(), { wrapper })

      await act(async () => {
        await result.current.mutateAsync({
          month: '2024-06',
          category_id: 'food',
          subcategory_id: null,
          amount: 150,
          currency: 'USD',
        })
      })

      expect(store()?.budgets).toHaveLength(1)
      expect(store()?.budgets[0].amount).toBe(100)
    })
  })

  describe('useReorderWithdrawalAutomations', () => {
    it('moves the dragged row into place by rewriting the priorities it renders in', async () => {
      queryClient.setQueryData(
        keys.store,
        emptyStore({ goal_automations: [automation('a', 0), automation('b', 1), automation('c', 2)] }),
      )
      const { result } = renderHook(() => useReorderWithdrawalAutomations(), { wrapper })

      await act(async () => {
        await result.current.mutateAsync(['c', 'a', 'b'])
      })

      const byPriority = [...(store()?.goal_automations ?? [])].sort((x, y) => x.priority - y.priority)
      expect(byPriority.map((entry) => entry.automation_id)).toEqual(['c', 'a', 'b'])
    })

    it('leaves an automation the request did not name where it was', async () => {
      queryClient.setQueryData(
        keys.store,
        emptyStore({
          goal_automations: [
            automation('a', 0),
            { ...automation('contrib', 1), direction: 'contribution' } as GoalAutomation,
            automation('b', 2),
          ],
        }),
      )
      const { result } = renderHook(() => useReorderWithdrawalAutomations(), { wrapper })

      await act(async () => {
        await result.current.mutateAsync(['b', 'a'])
      })

      const contribution = store()?.goal_automations.find((entry) => entry.automation_id === 'contrib')
      expect(contribution?.priority).toBe(1)
      const withdrawals = (store()?.goal_automations ?? [])
        .filter((entry) => entry.direction === 'withdrawal')
        .sort((x, y) => x.priority - y.priority)
      expect(withdrawals.map((entry) => entry.automation_id)).toEqual(['b', 'a'])
    })
  })

  it('puts every painted posting row back when the request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{"detail":"nope"}', { status: 500 })),
    )
    const first = { sort: 'posted_at', offset: 0 }
    const second = { sort: 'posted_at', offset: 200 }
    queryClient.setQueryData(keys.postingsPage(first), page([posting('p1')]))
    queryClient.setQueryData(keys.postingsPage(second), page([posting('p1')]))
    const { result } = renderHook(() => useSetPostingOverride(), { wrapper })

    await act(async () => {
      await result.current.mutateAsync({ postingId: 'p1', override: { category_id: 'food' } }).catch(() => {})
    })

    // Both pages roll back, not just the one the mutation happened to be
    // fired from — the snapshot is every page the prefix matched.
    for (const window of [first, second]) {
      await waitFor(() =>
        expect(queryClient.getQueryData<PostingPage>(keys.postingsPage(window))?.items[0].category_id).toBeNull(),
      )
    }
  })
})
