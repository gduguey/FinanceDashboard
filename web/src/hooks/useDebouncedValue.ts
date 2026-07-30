import { useEffect, useState } from 'react'

/**
 * The value as it was `delay` ms after it last changed.
 *
 * For a value that is cheap to type into and expensive to act on — a search
 * box over a list long enough that filtering it per keystroke is what the
 * keystroke is waiting for. The caller keeps rendering the live value in the
 * input, so typing stays immediate; only the work downstream of it waits.
 *
 * Each change restarts the timer, so a burst of typing settles once rather
 * than once per character.
 *
 * @param value - The live value.
 * @param delay - How long the value has to hold still, in milliseconds.
 * @returns The last value that held still for `delay`. Equal to `value` on the
 *   first render, so nothing renders empty while the first timer runs.
 */
export function useDebouncedValue<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value)

  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])

  return settled
}
