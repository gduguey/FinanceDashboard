import type { Category, Posting } from '@/types/accounting'

const SPLIT_LEG_PATTERN = /^(.+):split:\d+$/

// A split leg's own id encodes the original posting it came from — used to
// offer "undo split" on a leg row instead of "split" (splitting a leg
// further isn't supported; undo and re-split from scratch instead).
export function splitOriginalId(postingId: string): string | null {
  return SPLIT_LEG_PATTERN.exec(postingId)?.[1] ?? null
}

export function categoriesWithSubcategories(categories: Record<string, Category>): Set<string> {
  const withSubcategories = new Set<string>()
  for (const category of Object.values(categories)) {
    if (category.parent_category_id != null) withSubcategories.add(category.parent_category_id)
  }
  return withSubcategories
}

// A category with subcategories isn't "categorized" until one of them is
// picked too — otherwise a row would leave "Needs categorizing" the
// instant a category is chosen, before there's ever a chance to also pick
// a subcategory for it.
//
// A posting that isn't a real income/expense leg (i.e. an internal
// transfer between two of your own accounts) is never categorizable at
// all, so it never needs categorizing. And a posting still carrying an
// unconfirmed AI/pattern suggestion (`pending_source !== null`) stays in
// "Needs categorizing" even though it already has a category/subcategory
// filled in — it isn't truly categorized until the suggestion is validated.
export function needsCategorizing(
  posting: Posting,
  withSubcategories: Set<string>,
  isRealIncomeExpense: boolean,
): boolean {
  if (!isRealIncomeExpense) return false
  if (posting.pending_source != null) return true
  if (posting.category_id == null) return true
  return withSubcategories.has(posting.category_id) && posting.subcategory_id == null
}
