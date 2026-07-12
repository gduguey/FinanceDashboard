import type { Posting } from '@/types/accounting'

// Every month that actually has a transaction, newest first — picking a
// month with nothing in it is a dead end, so there's no reason to offer one.
export function availableMonths(postings: Posting[]): string[] {
  const months = new Set(postings.map((posting) => posting.posted_at.slice(0, 7)))
  return [...months].sort().reverse()
}
