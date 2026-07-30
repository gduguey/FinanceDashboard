import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'

describe('useDebouncedValue', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('reports the initial value straight away', () => {
    const { result } = renderHook(() => useDebouncedValue('coffee', 200))

    expect(result.current).toBe('coffee')
  })

  it('holds the previous value until the delay has passed', () => {
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 200), {
      initialProps: { value: 'a' },
    })

    rerender({ value: 'ab' })

    expect(result.current).toBe('a')
    act(() => {
      vi.advanceTimersByTime(200)
    })
    expect(result.current).toBe('ab')
  })

  it('settles once for a burst of changes rather than once per change', () => {
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 200), {
      initialProps: { value: '' },
    })

    for (const value of ['r', 're', 'ren', 'rent']) {
      rerender({ value })
      act(() => {
        vi.advanceTimersByTime(50)
      })
    }

    expect(result.current).toBe('')
    act(() => {
      vi.advanceTimersByTime(200)
    })
    expect(result.current).toBe('rent')
  })
})
