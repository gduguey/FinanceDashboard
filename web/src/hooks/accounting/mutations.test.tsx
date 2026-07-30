import { QueryClient, QueryClientProvider, useMutation } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { keys } from '@/hooks/accounting/keys'
import { patchEntry, useOptimisticStoreMutation, withoutEntry } from '@/hooks/accounting/mutations'
import { emptyStore } from '@/test/fixtures'
import type { AccountingStore, Tag } from '@/types/accounting'

function tag(id: string, name: string): Tag {
  return { tag_id: id, name } as Tag
}

/** A promise the test resolves by hand, so a request can be held in flight. */
function deferred<T>() {
  let settle!: (value: T) => void
  let fail!: (reason: Error) => void
  const promise = new Promise<T>((resolve, reject) => {
    settle = resolve
    fail = reject
  })
  return { promise, settle, fail }
}

describe('useOptimisticStoreMutation', () => {
  let queryClient: QueryClient

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    queryClient.setQueryData(keys.store, emptyStore({ tags: { a: tag('a', 'Alpha'), b: tag('b', 'Beta') } }))
  })

  function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }

  function store() {
    return queryClient.getQueryData<AccountingStore>(keys.store)
  }

  it('paints the store before the request answers', async () => {
    const request = deferred<void>()
    const { result } = renderHook(
      () =>
        useOptimisticStoreMutation({
          mutationFn: () => request.promise,
          changes: ['store'],
          edit: (current) => ({ ...current, tags: withoutEntry(current.tags, 'a') }),
        }),
      { wrapper },
    )

    act(() => {
      result.current.mutate()
    })
    await waitFor(() => expect(store()?.tags).not.toHaveProperty('a'))

    expect(Object.keys(store()?.tags ?? {})).toEqual(['b'])
    act(() => {
      request.settle()
    })
  })

  it('puts the store back when the request fails', async () => {
    const request = deferred<void>()
    const { result } = renderHook(
      () =>
        useOptimisticStoreMutation({
          mutationFn: () => request.promise,
          changes: ['store'],
          edit: (current) => ({ ...current, tags: withoutEntry(current.tags, 'a') }),
        }),
      { wrapper },
    )

    act(() => {
      result.current.mutate()
    })
    await waitFor(() => expect(store()?.tags).not.toHaveProperty('a'))
    await act(async () => {
      request.fail(new Error('nope'))
      await request.promise.catch(() => {})
    })

    await waitFor(() => expect(Object.keys(store()?.tags ?? {}).sort()).toEqual(['a', 'b']))
  })

  // The reason `edit` takes the current store rather than closing over a
  // snapshot: two optimistic writes overlapping must both survive. Nine hooks
  // used to spread a snapshot, which is the shape that loses one of them.
  it('keeps a second edit made while the first is still in flight', async () => {
    const slow = deferred<void>()
    const quick = deferred<void>()
    const { result: remove } = renderHook(
      () =>
        useOptimisticStoreMutation({
          mutationFn: () => slow.promise,
          changes: ['store'],
          edit: (current) => ({ ...current, tags: withoutEntry(current.tags, 'a') }),
        }),
      { wrapper },
    )
    const { result: rename } = renderHook(
      () =>
        useOptimisticStoreMutation({
          mutationFn: () => quick.promise,
          changes: ['store'],
          edit: (current) => ({ ...current, tags: patchEntry(current.tags, 'b', { name: 'Renamed' }) }),
        }),
      { wrapper },
    )

    act(() => {
      remove.current.mutate()
    })
    await waitFor(() => expect(store()?.tags).not.toHaveProperty('a'))
    act(() => {
      rename.current.mutate()
    })
    await waitFor(() => expect(store()?.tags.b?.name).toBe('Renamed'))

    expect(store()?.tags).not.toHaveProperty('a')
    act(() => {
      slow.settle()
      quick.settle()
    })
  })

  it('leaves the cache empty rather than seeding a store that was never read', async () => {
    queryClient.removeQueries({ queryKey: keys.store })
    const edit = vi.fn((current: AccountingStore) => current)
    const { result } = renderHook(
      () => useOptimisticStoreMutation({ mutationFn: async () => undefined, changes: ['store'], edit }),
      { wrapper },
    )

    await act(async () => {
      await result.current.mutateAsync()
    })

    expect(edit).not.toHaveBeenCalled()
    expect(store()).toBeUndefined()
  })

  // `GoalsPage` lists `mutateAsync` in the dependency array of the effect that
  // catches up its recurring-addition and withdrawal automations, and that
  // effect must run once per mount and not once per render — it fires two
  // write requests every time it runs. The array is only correct because
  // react-query hands back the same `mutateAsync` reference on every render
  // (it binds the function to a `MutationObserver` that lives in a `useState`
  // initializer). That is a property of the library rather than of our code,
  // so it is pinned here: if an upgrade ever makes the reference unstable,
  // this fails loudly instead of the page quietly looping on two endpoints.
  it('hands back the same mutateAsync reference across re-renders', () => {
    const { result, rerender } = renderHook(() => useMutation({ mutationFn: async () => undefined }), { wrapper })
    const first = result.current.mutateAsync

    rerender()
    rerender()

    expect(result.current.mutateAsync).toBe(first)
  })
})
