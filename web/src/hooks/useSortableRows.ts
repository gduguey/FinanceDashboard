import { useMemo, useState } from 'react'

interface SortState<T> {
  key: keyof T
  desc: boolean
}

// Shared click-to-sort/click-again-to-reverse behavior for every table on
// the dashboard. Nulls always sort last, regardless of direction, so a
// missing "annualized return" doesn't end up looking like the top or
// bottom performer.
export function useSortableRows<T extends object>(rows: T[] | undefined, initialKey: keyof T) {
  const [sort, setSort] = useState<SortState<T>>({ key: initialKey, desc: true })

  const sorted = useMemo(() => {
    if (!rows) return []
    return [...rows].sort((a, b) => {
      const x = a[sort.key]
      const y = b[sort.key]
      if (x === null || x === undefined) return y === null || y === undefined ? 0 : 1
      if (y === null || y === undefined) return -1
      const cmp = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))
      return sort.desc ? -cmp : cmp
    })
  }, [rows, sort])

  function toggleSort(key: keyof T) {
    setSort((prev) => (prev.key === key ? { key, desc: !prev.desc } : { key, desc: true }))
  }

  return { sorted, sort, toggleSort }
}
