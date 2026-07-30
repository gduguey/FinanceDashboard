import { describe, expect, it } from 'vitest'
import { moveItem, sameMembers, sameOrder } from '@/lib/reorder'

const key = (s: string) => s

describe('moveItem', () => {
  it('moves an item down', () => {
    expect(moveItem(['a', 'b', 'c', 'd'], 0, 2)).toEqual(['b', 'c', 'a', 'd'])
  })

  it('moves an item up', () => {
    expect(moveItem(['a', 'b', 'c', 'd'], 3, 1)).toEqual(['a', 'd', 'b', 'c'])
  })

  it('moves an item to the end', () => {
    expect(moveItem(['a', 'b', 'c'], 0, 2)).toEqual(['b', 'c', 'a'])
  })

  it('leaves the list alone when the item does not move', () => {
    const items = ['a', 'b', 'c']
    expect(moveItem(items, 1, 1)).toBe(items)
  })

  it('leaves the list alone for an out-of-range index', () => {
    const items = ['a', 'b', 'c']
    expect(moveItem(items, 0, 9)).toBe(items)
    expect(moveItem(items, -1, 1)).toBe(items)
  })

  it('does not mutate the input', () => {
    const items = ['a', 'b', 'c']
    moveItem(items, 0, 2)
    expect(items).toEqual(['a', 'b', 'c'])
  })

  it('composes across a multi-step drag the way hovering does', () => {
    // A drag from index 0 to index 3 arrives as a sequence of one-step hovers.
    // Applying them in turn must land where a single 0 -> 3 move would.
    let order = ['a', 'b', 'c', 'd']
    for (const [from, to] of [
      [0, 1],
      [1, 2],
      [2, 3],
    ]) {
      order = moveItem(order, from, to)
    }
    expect(order).toEqual(moveItem(['a', 'b', 'c', 'd'], 0, 3))
  })
})

describe('sameOrder', () => {
  it('is true for the same keys in the same positions', () => {
    expect(sameOrder(['a', 'b'], ['a', 'b'], key)).toBe(true)
  })

  it('is false when two items swap', () => {
    expect(sameOrder(['a', 'b'], ['b', 'a'], key)).toBe(false)
  })

  it('is false when the lengths differ', () => {
    expect(sameOrder(['a'], ['a', 'b'], key)).toBe(false)
  })

  it('compares by key, not by identity', () => {
    const left = [{ id: 'a' }, { id: 'b' }]
    const right = [{ id: 'a' }, { id: 'b' }]
    expect(sameOrder(left, right, (item) => item.id)).toBe(true)
  })
})

describe('sameMembers', () => {
  const keyOf = (item: { id: string }) => item.id
  const a = { id: 'a' }
  const b = { id: 'b' }
  const c = { id: 'c' }

  it('is true for the same items in a different order', () => {
    expect(sameMembers([a, b, c], [c, a, b], keyOf)).toBe(true)
  })

  it('is false once an item is added', () => {
    expect(sameMembers([a, b], [a, b, c], keyOf)).toBe(false)
  })

  it('is false once an item is removed', () => {
    expect(sameMembers([a, b, c], [a, b], keyOf)).toBe(false)
  })

  it('is false when one item is swapped for another', () => {
    expect(sameMembers([a, b], [a, c], keyOf)).toBe(false)
  })

  it('is true for two empty lists', () => {
    expect(sameMembers([], [], keyOf)).toBe(true)
  })
})
