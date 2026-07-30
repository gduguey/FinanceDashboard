import { needsCategorizing } from '@/components/accounting/transactionCategorization'
import { FILTER_ALL as ALL, matchesFilter, matchesMultiFilter } from '@/lib/filters'
import type { Posting } from '@/types/accounting'

/** The two placeholder counterparties every fresh install seeds — never rendered as a row of their own. */
export const PLACEHOLDER_ACCOUNT_IDS = new Set(['uncategorized:expense', 'uncategorized:income'])

/** The category filter's entry for "no category at all", which is not a category id. */
export const UNCATEGORIZED = '__uncategorized__'
/** The subcategory filter's entry for "no subcategory", likewise. */
export const NO_SUBCATEGORY = '__no_subcategory__'
/** The pending filter's entry for "not a suggestion" — a posting whose `pending_source` is null. */
export const CONFIRMED = '__confirmed__'

export const PENDING_OPTIONS = [
  { id: 'ai', name: 'AI pending' },
  { id: 'pattern', name: 'Pattern pending' },
  { id: CONFIRMED, name: 'Confirmed' },
]

// The four ways a transaction's transfer status can currently read, spanning
// every mechanism the transactions table's own badges/columns already show —
// never mutually exclusive with each other except pairwise (a posting is
// either mid-transfer via a rule/manually, or a plain non-transfer;
// "excluded" is a separate historical fact that can be true alongside either).
export const TRANSFER_FLAG_OPTIONS = [
  { id: 'rule', name: 'Transfer flagged by rule' },
  { id: 'excluded', name: 'Excluded from transfer rule' },
  { id: 'manual', name: 'Transfer manually added' },
  { id: 'none', name: 'Non transfer' },
]

export const DATE_MODE_MONTH = 'month'
export const DATE_MODE_RANGE = 'range'
export const ALL_MONTHS = '__all_months__'

export interface FilterState {
  /** The search box's current term. Never persisted — see `PersistedFilters`. */
  search: string
  accountFilter: string
  accountExclude: boolean
  // Multi-select, unlike accountFilter/incomeExpenseFilter/categorizedFilter
  // (each still single-value, an "all-or-one" choice that doesn't benefit
  // from picking several) — an empty array means "no restriction", the
  // same meaning `ALL` carries for those.
  categoryFilter: string[]
  categoryExclude: boolean
  subcategoryFilter: string[]
  subcategoryExclude: boolean
  tagFilter: string[]
  tagExclude: boolean
  dateMode: typeof DATE_MODE_MONTH | typeof DATE_MODE_RANGE
  month: string
  startDate: string
  endDate: string
  pendingFilter: string[]
  pendingExclude: boolean
  transferFlagFilter: string[]
  transferFlagExclude: boolean
  incomeExpenseFilter: string
  categorizedFilter: string
}

/**
 * Everything in the filter bar that survives a reload.
 *
 * The search term is deliberately not part of it. `usePersistedState`'s setter
 * runs `JSON.stringify` plus `localStorage.setItem` and notifies every
 * subscriber synchronously, so keeping the term there meant a serialize, a
 * disk write and a re-render of the whole table on every keystroke — and a
 * half-typed query is not a preference worth restoring anyway. The term lives
 * in component state and reaches the filter through `withSearch`.
 */
export type PersistedFilters = Omit<FilterState, 'search'>

/**
 * The filter bar as it reads before anything has been picked.
 *
 * @returns A fresh, fully-populated set of persisted filters.
 */
export function defaultFilterState(): PersistedFilters {
  return {
    accountFilter: ALL,
    accountExclude: false,
    categoryFilter: [],
    categoryExclude: false,
    subcategoryFilter: [],
    subcategoryExclude: false,
    tagFilter: [],
    tagExclude: false,
    // Unscoped by default, same as the old plain from/to range — the month
    // picker is there for when narrowing down is useful, not a forced default.
    dateMode: DATE_MODE_MONTH,
    month: ALL_MONTHS,
    startDate: '',
    endDate: '',
    pendingFilter: [],
    pendingExclude: false,
    transferFlagFilter: [],
    transferFlagExclude: false,
    incomeExpenseFilter: ALL,
    categorizedFilter: ALL,
  }
}

/**
 * Turn whatever `localStorage` holds under the filter key into a usable `FilterState`.
 *
 * A filter bar persisted before `categoryFilter` became multi-select left a
 * plain string there, and a string has `.includes` too — just
 * substring-checking rather than membership, so a stale value silently
 * filtered by the wrong rule instead of failing.
 * `subcategoryFilter`/`tagFilter`/`pendingFilter`/`transferFlagFilter` each
 * carry the same risk, having gone single-to-multi the same way, and an older
 * persisted state is simply missing the newer keys.
 *
 * Every field is coerced here rather than at each of the dozen read sites that
 * used to guard four of them and default three more inline.
 *
 * @param stored - What was read back from persisted state, of unknown vintage.
 *   A `search` term left behind by a version that persisted one is dropped.
 * @returns A complete set of persisted filters, every array really an array.
 */
export function normalizeFilterState(stored: Partial<PersistedFilters> | null | undefined): PersistedFilters {
  const defaults = defaultFilterState()
  if (!stored) return defaults
  const list = (value: unknown, fallback: string[]) => (Array.isArray(value) ? (value as string[]) : fallback)
  return {
    ...defaults,
    ...stored,
    categoryFilter: list(stored.categoryFilter, defaults.categoryFilter),
    subcategoryFilter: list(stored.subcategoryFilter, defaults.subcategoryFilter),
    tagFilter: list(stored.tagFilter, defaults.tagFilter),
    pendingFilter: list(stored.pendingFilter, defaults.pendingFilter),
    transferFlagFilter: list(stored.transferFlagFilter, defaults.transferFlagFilter),
    pendingExclude: stored.pendingExclude ?? defaults.pendingExclude,
    incomeExpenseFilter: stored.incomeExpenseFilter ?? defaults.incomeExpenseFilter,
    categorizedFilter: stored.categorizedFilter ?? defaults.categorizedFilter,
  }
}

/**
 * The persisted filters plus the term currently in the search box.
 *
 * @param persisted - What survives a reload.
 * @param search - The search term, debounced by the caller.
 * @returns The complete filter the table is showing.
 */
export function withSearch(persisted: PersistedFilters, search: string): FilterState {
  return { ...persisted, search }
}

/**
 * How many filters are currently narrowing the table, for the bar's own badge.
 *
 * A multi-select counts once per picked value; the date row counts once
 * however many of its two bounds are set.
 *
 * @param filters - The current filter state.
 * @returns The badge's number. Zero hides the "Reset filters" button.
 */
export function activeFilterCount(filters: PersistedFilters): number {
  let count = 0
  if (filters.accountFilter !== ALL) count++
  count +=
    filters.categoryFilter.length +
    filters.subcategoryFilter.length +
    filters.tagFilter.length +
    filters.pendingFilter.length +
    filters.transferFlagFilter.length
  if (filters.dateMode === DATE_MODE_MONTH ? filters.month !== ALL_MONTHS : filters.startDate || filters.endDate) {
    count++
  }
  if (filters.incomeExpenseFilter !== ALL) count++
  if (filters.categorizedFilter !== ALL) count++
  return count
}

/**
 * Every `TRANSFER_FLAG_OPTIONS` id that applies to this posting right now.
 *
 * A plain array rather than one classification, since "excluded from a rule"
 * is a historical fact that can be true alongside either "non transfer"
 * (nothing else currently flags it) or another rule flagging it.
 *
 * @param posting - The resolved posting row.
 * @param excludedTransactionIds - Every transaction excluded from at least one rule.
 * @returns The ids that apply, in `rule`, `manual`, `excluded`, `none` order.
 */
export function transferFlagsForPosting(posting: Posting, excludedTransactionIds: Set<string>): string[] {
  const flags: string[] = []
  if (
    posting.resolved_by_transfer_rule_id != null ||
    (posting.is_linked_transfer && posting.transfer_link_source === 'rule')
  ) {
    flags.push('rule')
  }
  if (
    posting.manual_transfer_override_posting_id != null ||
    (posting.is_linked_transfer && posting.transfer_link_source === 'manual')
  ) {
    flags.push('manual')
  }
  // `excluded` is a historical fact, not a transfer classification, so decide
  // "is this a transfer" before adding it — otherwise an excluded-but-otherwise
  // -untransferred posting would be denied its `none` flag and drop out of the
  // "Non transfer" filter (see this function's own doc comment).
  const isTransfer = flags.length > 0
  if (excludedTransactionIds.has(posting.transaction_id)) flags.push('excluded')
  if (!isTransfer) flags.push('none')
  return flags
}

/**
 * The facts about the whole ledger a filter pass needs, which no single posting carries.
 *
 * Each is computed from the *unscoped* posting list and the store, never from
 * an already-filtered subset — an account filter can drop one leg of a pair,
 * which would make its sibling look virtual or not incorrectly.
 */
export interface FilterContext {
  /** The "Needs categorizing" tab, which restricts to postings that still want a category. */
  onlyUncategorized: boolean
  /** Category ids that have subcategories, so picking the parent alone is not yet categorized. */
  withSubcategories: Set<string>
  /** Postings that are real income or expense rather than an internal transfer. */
  realIncomeExpensePostingIds: Set<string>
  /** Every transaction excluded from at least one transfer rule. */
  excludedTransactionIds: Set<string>
}

/**
 * Whether one posting survives the filter bar.
 *
 * Conjunctive: every clause below has to pass. Written as one function rather
 * than the thirteen chained `.filter()` passes it replaces, so a posting is
 * visited once and each clause can be read next to the control that sets it.
 *
 * @param posting - The resolved posting row.
 * @param filters - The current filter state, already normalized.
 * @param context - Facts about the whole ledger. See `FilterContext`.
 * @returns `true` when the row should be shown.
 */
export function matchesFilters(posting: Posting, filters: FilterState, context: FilterContext): boolean {
  const isRealIncomeExpense = context.realIncomeExpensePostingIds.has(posting.posting_id)

  if (PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id)) return false
  if (context.onlyUncategorized && !needsCategorizing(posting, context.withSubcategories, isRealIncomeExpense)) {
    return false
  }
  if (!posting.description.toLowerCase().includes(filters.search.toLowerCase())) return false
  if (!matchesFilter(posting.account_id === filters.accountFilter, filters.accountFilter, filters.accountExclude)) {
    return false
  }
  if (
    !matchesMultiFilter(
      filters.categoryFilter.includes(posting.category_id ?? UNCATEGORIZED),
      filters.categoryFilter.length,
      filters.categoryExclude,
    )
  ) {
    return false
  }
  if (
    !matchesMultiFilter(
      filters.subcategoryFilter.includes(posting.subcategory_id ?? NO_SUBCATEGORY),
      filters.subcategoryFilter.length,
      filters.subcategoryExclude,
    )
  ) {
    return false
  }
  if (
    !matchesMultiFilter(
      filters.tagFilter.some((tagId) => (posting.tag_ids ?? []).includes(tagId)),
      filters.tagFilter.length,
      filters.tagExclude,
    )
  ) {
    return false
  }
  if (!withinDateFilter(posting, filters)) return false
  if (
    !matchesMultiFilter(
      filters.pendingFilter.includes(posting.pending_source ?? CONFIRMED),
      filters.pendingFilter.length,
      filters.pendingExclude,
    )
  ) {
    return false
  }
  if (filters.transferFlagFilter.length > 0) {
    const flags = transferFlagsForPosting(posting, context.excludedTransactionIds)
    if (
      !matchesMultiFilter(
        filters.transferFlagFilter.some((flag) => flags.includes(flag)),
        filters.transferFlagFilter.length,
        filters.transferFlagExclude,
      )
    ) {
      return false
    }
  }
  if (filters.incomeExpenseFilter !== ALL) {
    if (!isRealIncomeExpense) return false
    if (filters.incomeExpenseFilter === 'income' ? posting.amount < 0 : posting.amount >= 0) return false
  }
  if (filters.categorizedFilter === 'categorized' && posting.category_id == null) return false
  if (filters.categorizedFilter === 'uncategorized' && posting.category_id != null) return false
  return true
}

/**
 * Whether one posting falls inside the date row's current bound.
 *
 * @param posting - The resolved posting row.
 * @param filters - The current filter state.
 * @returns `true` when the row's date is in range, or nothing is bounding it.
 */
function withinDateFilter(posting: Posting, filters: FilterState): boolean {
  if (filters.dateMode === DATE_MODE_MONTH) {
    return filters.month === ALL_MONTHS || posting.posted_at.slice(0, 7) === filters.month
  }
  if (filters.startDate && posting.posted_at.slice(0, 10) < filters.startDate) return false
  if (filters.endDate && posting.posted_at.slice(0, 10) > filters.endDate) return false
  return true
}

/**
 * Every posting the filter bar currently admits, in the order given.
 *
 * @param postings - The full, unscoped posting list.
 * @param filters - The current filter state, already normalized.
 * @param context - Facts about the whole ledger. See `FilterContext`.
 * @returns The subset to render.
 */
export function filterPostings(postings: Posting[], filters: FilterState, context: FilterContext): Posting[] {
  return postings.filter((posting) => matchesFilters(posting, filters, context))
}
