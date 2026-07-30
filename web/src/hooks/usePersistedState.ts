import { useCallback, useEffect, useState } from 'react'

// Plain `localStorage` alone only notifies *other* tabs (the native
// `storage` event never fires in the document that made the change), so two
// components on the same page reading the same key — e.g. the currency
// toggle in the header and the page below it — would drift out of sync
// until a full reload. This adds an in-memory pub/sub so every mounted
// instance for a given key re-renders the moment any of them calls `set`.
const listeners = new Map<string, Set<() => void>>()

function notify(key: string) {
  for (const listener of listeners.get(key) ?? []) listener()
}

function read<T>(key: string, initial: T): T {
  const stored = localStorage.getItem(key)
  if (stored === null) return initial
  try {
    return JSON.parse(stored) as T
  } catch {
    return initial
  }
}

export function usePersistedState<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => read(key, initial))

  useEffect(() => {
    const listener = () => setValue(read(key, initial))
    // The set is held in a local rather than fetched back out of the map on
    // each use. `Map.get` is typed `T | undefined` however confidently a
    // `has` on the line above says otherwise — TypeScript has no way to tie
    // the two calls together — so reading it back always needs something to
    // discharge the `undefined`. Keeping the reference means there is no
    // `undefined` to discharge in the first place, and the cleanup below
    // removes the listener from the very set it was added to rather than
    // from whatever the map happens to hold by then.
    const keyListeners = listeners.get(key) ?? new Set<() => void>()
    listeners.set(key, keyListeners)
    keyListeners.add(listener)
    return () => {
      keyListeners.delete(listener)
    }
    // `initial` is only ever used for the very first read — re-subscribing
    // whenever a caller passes a fresh literal/object would tear down and
    // rebuild the listener on every render for no reason.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const set = useCallback(
    (next: T) => {
      localStorage.setItem(key, JSON.stringify(next))
      notify(key)
    },
    [key],
  )

  return [value, set] as const
}
