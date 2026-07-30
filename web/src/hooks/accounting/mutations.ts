import { useMutation, useQueryClient } from '@tanstack/react-query'
import { type AccountingFamily, keys, useInvalidateAccounting } from '@/hooks/accounting/keys'
import type { AccountingStore } from '@/types/accounting'

/**
 * A write, plus the families it changed.
 *
 * The default shape: fire the request, then invalidate exactly what it can
 * have moved. Sixty-odd hooks spelled this out by hand, which is how the
 * invalidation drifted to the whole-feature prefix in most of them.
 *
 * `TVariables` defaults to `void` rather than being inferred as `unknown`: a
 * request taking no argument offers TypeScript no inference candidate at that
 * position, and `unknown` would make `mutate()` a compile error at the call
 * sites that legitimately pass nothing.
 *
 * @param mutationFn - The request.
 * @param changes - What the write moved. See `AccountingFamily`.
 * @returns A React Query mutation.
 */
export function useAccountingMutation<TData, TVariables = void>({
  mutationFn,
  changes,
}: {
  mutationFn: (variables: TVariables) => Promise<TData>
  changes: readonly AccountingFamily[]
}) {
  const invalidate = useInvalidateAccounting()
  return useMutation({ mutationFn, onSuccess: () => invalidate(...changes) })
}

/**
 * A write that paints the cached store before the server confirms it.
 *
 * `edit` is applied as a function of whatever is in the cache at the moment
 * the mutation fires, never as a spread of the snapshot taken beside it. That
 * distinction is the point of this factory: nine hooks used to read the store
 * once into `previous` and write `{ ...previous, … }` back, which silently
 * reverted any other optimistic write still in flight — a category patch fired
 * during an unsettled rule patch put the rule back. Two hooks had already been
 * written the correct way with a comment explaining why, and the other nine
 * had not; a factory is what stops that being a per-hook decision.
 *
 * The rollback in `onError` does restore the snapshot wholesale. That is the
 * ordinary React Query rollback and it only runs when the request failed,
 * where losing a concurrent optimistic paint is the safe direction — the
 * `onSuccess` invalidation refetches the truth immediately afterwards either
 * way.
 *
 * Deliberately not offered for a create. A created row has no id until the
 * server answers, so painting one means inventing a key the reconciling
 * refetch then has to remove — see the repo's own convention that optimistic
 * updates are for updates and deletes.
 *
 * @param mutationFn - The request.
 * @param changes - What the write moved. See `AccountingFamily`.
 * @param edit - The store as it should look the instant the user acts, given
 *   the store as it is now and the mutation's own variables.
 * @returns A React Query mutation.
 */
export function useOptimisticStoreMutation<TData, TVariables = void>({
  mutationFn,
  changes,
  edit,
}: {
  mutationFn: (variables: TVariables) => Promise<TData>
  changes: readonly AccountingFamily[]
  edit: (store: AccountingStore, variables: TVariables) => AccountingStore
}) {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn,
    onMutate: async (variables: TVariables) => {
      await queryClient.cancelQueries({ queryKey: keys.store })
      const previous = queryClient.getQueryData<AccountingStore>(keys.store)
      queryClient.setQueryData<AccountingStore>(keys.store, (current) => (current ? edit(current, variables) : current))
      return { previous }
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(keys.store, context.previous)
    },
    onSuccess: () => invalidate(...changes),
  })
}

/**
 * A request that persists nothing — a rename preview, a match preview, a
 * paystub reconciliation.
 *
 * A mutation because it is fired on demand rather than on render, not because
 * it writes. Named so the absent invalidation reads as a decision instead of
 * an omission.
 *
 * @param mutationFn - The request.
 * @returns A React Query mutation that invalidates nothing.
 */
export function usePreviewMutation<TData, TVariables = void>(mutationFn: (variables: TVariables) => Promise<TData>) {
  return useMutation({ mutationFn })
}

/**
 * Replace one entry of a store map, merging a partial patch into it.
 *
 * @param entries - The map as it is now.
 * @param id - Which entry to patch.
 * @param patch - The fields to overwrite.
 * @returns A new map; the original is untouched.
 */
export function patchEntry<T>(entries: Record<string, T>, id: string, patch: Partial<T>): Record<string, T> {
  return { ...entries, [id]: { ...entries[id], ...patch } }
}

/**
 * Drop one entry from a store map.
 *
 * @param entries - The map as it is now.
 * @param id - Which entry to drop.
 * @returns A new map; the original is untouched.
 */
export function withoutEntry<T>(entries: Record<string, T>, id: string): Record<string, T> {
  const remaining = { ...entries }
  delete remaining[id]
  return remaining
}
