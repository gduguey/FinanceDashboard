/**
 * Move one item within a list, returning a new list.
 *
 * The list is the order; nothing carries an explicit rank. Both reorder
 * endpoints read priority off the position of each id in the submitted array,
 * so "drag to reorder" and "priority" have no way to drift apart.
 *
 * @param items The current order.
 * @param from Index being dragged.
 * @param to Index it is being dropped onto.
 * @returns A new array, or `items` unchanged when the move is a no-op or
 *   either index is out of range.
 */
export function moveItem<T>(items: T[], from: number, to: number): T[] {
  if (from === to) return items
  if (from < 0 || to < 0 || from >= items.length || to >= items.length) return items
  const next = [...items]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}

/**
 * Whether two lists hold the same keys in the same positions.
 *
 * Used to tell when the server has caught up with a drag that was already
 * painted locally, so the local copy can be dropped without the row order
 * visibly snapping back and forth.
 */
export function sameOrder<T>(a: T[], b: T[], keyOf: (item: T) => string): boolean {
  if (a.length !== b.length) return false
  return a.every((item, index) => keyOf(item) === keyOf(b[index]))
}

/**
 * Whether two lists hold the same items, in any order.
 *
 * The weaker companion to `sameOrder`, for deciding whether a pending local
 * order is still *about* the list it was taken from. A create or a delete
 * landing while a reorder is unresolved changes the membership, and no
 * permutation of the old list will ever equal the new one — so a caller
 * waiting on `sameOrder` alone would wait forever, rendering rows that no
 * longer exist.
 *
 * @param a - One list.
 * @param b - The other.
 * @param keyOf - How to identify an item.
 * @returns `true` when both hold exactly the same keys.
 */
export function sameMembers<T>(a: T[], b: T[], keyOf: (item: T) => string): boolean {
  if (a.length !== b.length) return false
  const keys = new Set(a.map(keyOf))
  return b.every((item) => keys.has(keyOf(item)))
}
