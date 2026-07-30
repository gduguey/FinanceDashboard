import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useReorderWithdrawalAutomations } from '@/hooks/accounting/goals'
import { keys } from '@/hooks/accounting/keys'
import { useSetPostingOverride } from '@/hooks/accounting/postings'
import { useSetBudget } from '@/hooks/accounting/taxonomy'
import { emptyStore } from '@/test/fixtures'
import type { AccountingStore, Budget, GoalAutomation, Posting } from '@/types/accounting'

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
    beforeEach(() => {
      queryClient.setQueryData(keys.postings, [posting('p1'), posting('p2')])
    })

    function postings() {
      return queryClient.getQueryData<Posting[]>(keys.postings) ?? []
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

  it('puts a posting row back when the request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{"detail":"nope"}', { status: 500 })),
    )
    queryClient.setQueryData(keys.postings, [posting('p1')])
    const { result } = renderHook(() => useSetPostingOverride(), { wrapper })

    await act(async () => {
      await result.current.mutateAsync({ postingId: 'p1', override: { category_id: 'food' } }).catch(() => {})
    })

    await waitFor(() => expect(queryClient.getQueryData<Posting[]>(keys.postings)?.[0].category_id).toBeNull())
  })
})
