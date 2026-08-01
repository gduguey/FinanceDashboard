import { FILTER_ALL as ALL } from '@/lib/filters'
import type { PostingFilters } from '@/types/accounting'

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
//
// Typed against the wire's own union rather than as bare strings: these ids
// travel to the server as `transfer_flags`, so a fifth option added here
// without a matching one in `api_models.TransferFlag` should not typecheck.
export const TRANSFER_FLAG_OPTIONS: { id: TransferFlag; name: string }[] = [
  { id: 'rule', name: 'Transfer flagged by rule' },
  { id: 'excluded', name: 'Excluded from transfer rule' },
  { id: 'manual', name: 'Transfer manually added' },
  { id: 'none', name: 'Non transfer' },
]

/** One entry of the "Filter transfers" multi-select, as the server names them. */
export type TransferFlag = NonNullable<PostingFilters['transfer_flags']>[number]

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
  // Plain strings, not the wire's own unions: these come back out of
  // `localStorage`, where a value written by an older version of the app can
  // be anything at all. `toPostingFilters` is where they are narrowed, so an
  // unrecognized value is dropped at the boundary rather than sent as a
  // predicate the server does not have.
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
 * The filter that restricts nothing — every predicate at its "no restriction" value.
 *
 * `PostingFilters` makes each multi-select's `_exclude` flag and the search
 * term required, so a caller naming two or three predicates still has to say
 * what the other eighteen mean. Spreading this says it once. The insights
 * drilldown is the second caller of `GET /postings` and the reason it exists:
 * it names a window, an account, a tag and a category, and nothing else.
 *
 * @returns A fully-populated filter matching every posting.
 */
export function noPostingFilters(): Required<PostingFilters> {
  return {
    search: '',
    account: null,
    account_exclude: false,
    categories: [],
    categories_exclude: false,
    subcategories: [],
    subcategories_exclude: false,
    tags: [],
    tags_exclude: false,
    month: null,
    start: null,
    end: null,
    pending: [],
    pending_exclude: false,
    transfer_flags: [],
    transfer_flags_exclude: false,
    income_expense: null,
    categorized: null,
    needs_categorizing: false,
  }
}

/**
 * Translate the filter bar into the query `GET /postings` and both bulk actions take.
 *
 * The one place the screen's vocabulary and the wire's meet. The bar carries
 * sentinels for "no restriction" (`FILTER_ALL`, `ALL_MONTHS`, an empty
 * multi-select) and a `dateMode` toggle picking which of two date controls is
 * live; the wire says the same things by omission. Everything else is a
 * rename.
 *
 * The predicates themselves are no longer here at all. Thirteen of them used
 * to run in the browser over a resident ledger, and the server now evaluates
 * the identical set over `accounting.resolved_postings` — see
 * `accounting.repositories.projection.filter_predicates`, which is written in
 * this function's own order so the two can be read side by side. Two
 * implementations of one filter in front of one screen is exactly what C1
 * existed to remove, so this returns the query and nothing evaluates it here.
 *
 * @param filters - The bar's current state, already normalized.
 * @param onlyUncategorized - Whether the "Needs categorizing" tab is the open one.
 * @returns The filter as the server reads it.
 */
export function toPostingFilters(
  filters: FilterState,
  { onlyUncategorized }: { onlyUncategorized: boolean },
): Required<PostingFilters> {
  const byMonth = filters.dateMode === DATE_MODE_MONTH
  return {
    search: filters.search,
    account: filters.accountFilter === ALL ? null : filters.accountFilter,
    account_exclude: filters.accountExclude,
    categories: filters.categoryFilter,
    categories_exclude: filters.categoryExclude,
    subcategories: filters.subcategoryFilter,
    subcategories_exclude: filters.subcategoryExclude,
    tags: filters.tagFilter,
    tags_exclude: filters.tagExclude,
    month: byMonth && filters.month !== ALL_MONTHS ? filters.month : null,
    start: byMonth ? null : filters.startDate || null,
    end: byMonth ? null : filters.endDate || null,
    pending: filters.pendingFilter,
    pending_exclude: filters.pendingExclude,
    transfer_flags: filters.transferFlagFilter.filter(isTransferFlag),
    transfer_flags_exclude: filters.transferFlagExclude,
    income_expense: oneOf(filters.incomeExpenseFilter, INCOME_EXPENSE_VALUES),
    categorized: oneOf(filters.categorizedFilter, CATEGORIZED_VALUES),
    needs_categorizing: onlyUncategorized,
  }
}

const TRANSFER_FLAG_IDS: readonly TransferFlag[] = ['rule', 'excluded', 'manual', 'none']
const INCOME_EXPENSE_VALUES = ['income', 'expense'] as const
const CATEGORIZED_VALUES = ['categorized', 'uncategorized'] as const

/** Whether a persisted multi-select value is still one the server knows. */
function isTransferFlag(value: string): value is TransferFlag {
  return (TRANSFER_FLAG_IDS as readonly string[]).includes(value)
}

/**
 * Narrow one persisted single-select to the wire's own union, or to "no restriction".
 *
 * `FILTER_ALL` and any value a past version of the app persisted both come
 * back as `null`, which is what the server reads as unrestricted. Dropping an
 * unrecognized value is the safe direction: sending it would be a predicate
 * the server rejects, or worse, silently matches nothing.
 *
 * @param value - What the filter bar currently holds.
 * @param allowed - The values the wire accepts.
 * @returns One of `allowed`, or `null`.
 */
function oneOf<T extends string>(value: string, allowed: readonly T[]): T | null {
  return (allowed as readonly string[]).includes(value) ? (value as T) : null
}
