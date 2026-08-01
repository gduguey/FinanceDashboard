import { useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'

// Every accounting query key hangs off this one root, so a cache reset for
// the whole feature is still expressible — but never as the invalidation a
// write performs. See `useInvalidateAccounting`.
const ROOT = 'accounting'

// The stable head of each query key, split out from `keys` below because two
// different things need it: the key builders (which append their arguments to
// it) and `FAMILY_PREFIXES` (which invalidates every variant at once by
// prefix). Declaring it once is what stops a key and the invalidation meant to
// sweep it drifting apart.
const prefixes = {
  store: [ROOT, 'store'],
  currencies: [ROOT, 'currencies'],
  llmUsage: [ROOT, 'llm-usage'],
  llmSettings: [ROOT, 'settings', 'llm'],
  supportedImportKinds: [ROOT, 'supported-import-kinds'],
  syncStatus: [ROOT, 'sync-status'],
  currentExchangeRate: [ROOT, 'exchange-rate', 'current'],
  exchangeRateHistory: [ROOT, 'exchange-rate', 'history'],
  postings: [ROOT, 'postings'],
  transferSuggestions: [ROOT, 'transfer-suggestions'],
  duplicateSuggestions: [ROOT, 'duplicate-suggestions'],
  dismissedSuggestions: [ROOT, 'dismissed-suggestions'],
  netWorth: [ROOT, 'net-worth'],
  netWorthHistory: [ROOT, 'net-worth-history'],
  netWorthHistoryByAccount: [ROOT, 'net-worth-history-by-account'],
  categoryTotals: [ROOT, 'category-totals'],
  monthlyIncomeExpense: [ROOT, 'monthly-income-expense'],
  spendCurve: [ROOT, 'spend-curve'],
  budgetComparison: [ROOT, 'budget-comparison'],
  suggestedBudgetAmount: [ROOT, 'suggested-budget-amount'],
  interestSummary: [ROOT, 'interest-summary'],
  goalsSummary: [ROOT, 'goals-summary'],
  simulatorProject: [ROOT, 'simulator-project'],
} as const

/**
 * The head every cached page of `GET /postings` hangs off.
 *
 * Its own level under `postings` rather than the bare prefix, and that is
 * load-bearing: `useSetPostingOverride` paints *every* cached page at once,
 * so it needs a prefix that matches pages and only pages. Matching
 * `prefixes.postings` would hand a page-shaped updater to the count query,
 * whose cached value is a number.
 */
export const POSTINGS_PAGE_PREFIX = [...prefixes.postings, 'page'] as const

/**
 * The cache key for every accounting query, in one place.
 *
 * Exported rather than module-private, which is what it used to be. A hook
 * author who could not name the key for "the thing my write changed" reached
 * for the whole-feature prefix instead, and 56 of 65 mutations ended up
 * invalidating everything — so the missing export was the cause, not an
 * incidental detail.
 *
 * A key built from arguments always extends its own entry in `prefixes`, so
 * invalidating that prefix sweeps every argument variant. Four of them nest:
 * `postingCount`, `postingMonths` and `postingsPage` sit under `postings`,
 * and `llmVerify` under `llmSettings`. All are deliberate — the count, the
 * month list and every cached page are derived from the same rows, and a
 * verify result is only meaningful for the key currently stored — so a prefix
 * invalidation of the parent is supposed to take the children with it.
 */
export const keys = {
  store: prefixes.store,
  currencies: prefixes.currencies,
  llmUsage: prefixes.llmUsage,
  llmSettings: prefixes.llmSettings,
  llmVerify: (provider: 'gemini' | 'mistral') => [...prefixes.llmSettings, 'verify', provider],
  supportedImportKinds: prefixes.supportedImportKinds,
  syncStatus: prefixes.syncStatus,
  currentExchangeRate: (currency: string) => [...prefixes.currentExchangeRate, currency],
  exchangeRateHistory: (currency: string) => [...prefixes.exchangeRateHistory, currency],
  postingCount: [...prefixes.postings, 'count'],
  postingMonths: [...prefixes.postings, 'months'],
  postingsPage: (query: object) => [...POSTINGS_PAGE_PREFIX, query],
  transactionLegs: (transactionIds: readonly string[]) => [...prefixes.postings, 'legs', transactionIds],
  transferSuggestions: prefixes.transferSuggestions,
  duplicateSuggestions: prefixes.duplicateSuggestions,
  dismissedSuggestions: prefixes.dismissedSuggestions,
  netWorth: (asOf?: string, displayCurrency?: string) => [...prefixes.netWorth, asOf ?? {}, displayCurrency ?? {}],
  netWorthHistory: (start: string, end: string, intervalDays?: number, displayCurrency?: string) => [
    ...prefixes.netWorthHistory,
    start,
    end,
    intervalDays ?? {},
    displayCurrency ?? {},
  ],
  netWorthHistoryByAccount: (start: string, end: string, intervalDays?: number, displayCurrency?: string) => [
    ...prefixes.netWorthHistoryByAccount,
    start,
    end,
    intervalDays ?? {},
    displayCurrency ?? {},
  ],
  categoryTotals: (start: string, end: string, accountIds?: string[], tagId?: string, displayCurrency?: string) => [
    ...prefixes.categoryTotals,
    start,
    end,
    accountIds ?? [],
    tagId ?? {},
    displayCurrency ?? {},
  ],
  monthlyIncomeExpense: (start: string, end: string, displayCurrency?: string) => [
    ...prefixes.monthlyIncomeExpense,
    start,
    end,
    displayCurrency ?? {},
  ],
  spendCurve: (month: string, lookbackMonths?: number, displayCurrency?: string) => [
    ...prefixes.spendCurve,
    month,
    lookbackMonths ?? {},
    displayCurrency ?? {},
  ],
  budgetComparison: (month: string, displayCurrency?: string) => [
    ...prefixes.budgetComparison,
    month,
    displayCurrency ?? {},
  ],
  suggestedBudgetAmount: (
    categoryId: string,
    month: string,
    lookbackMonths?: number,
    subcategoryId?: string,
    displayCurrency?: string,
  ) => [
    ...prefixes.suggestedBudgetAmount,
    categoryId,
    month,
    lookbackMonths ?? {},
    subcategoryId ?? {},
    displayCurrency ?? {},
  ],
  interestSummary: (asOf?: string) => [...prefixes.interestSummary, asOf ?? {}],
  goalsSummary: (asOf?: string, displayCurrency?: string) => [
    ...prefixes.goalsSummary,
    asOf ?? {},
    displayCurrency ?? {},
  ],
  simulatorProject: (
    initialCapital: number,
    monthlyContribution: number,
    horizonYears: number,
    annualRatePct: number,
    compoundingFrequency: string,
  ) => [
    ...prefixes.simulatorProject,
    initialCapital,
    monthlyContribution,
    horizonYears,
    annualRatePct,
    compoundingFrequency,
  ],
}

/**
 * What a write changed, named for the write rather than for the cache.
 *
 * A mutation declares one or more of these instead of naming query keys, so a
 * call site reads as a claim about the domain — "this changed the store and
 * the goals" — that a reviewer can check against the endpoint. Adding a query
 * to an existing family updates every mutation that already declared it.
 *
 * - `store` — the bootstrap composite: accounts, categories, tags, rules,
 *   budgets, goals, patterns, links, assets, scenarios, automations.
 * - `ledger` — the resolved posting list and everything computed from it.
 *   Broad because it genuinely is: change one posting's category and the
 *   income statement, the budget comparison and net worth all move with it.
 * - `suggestions` — the transfer/duplicate/dismissed lists. Their own family
 *   because dismissing a suggestion changes them and nothing else.
 * - `budgets` — the budget comparison and the suggested amount, for a write
 *   that sets a target without touching a posting.
 * - `netWorth` — balances now and over time, the interest summary, and the
 *   goals summary that reads unallocated money off them.
 * - `goals` — the goals summary alone, for a write to a goal or contribution
 *   that reallocates already-counted money rather than changing a balance.
 * - `llm` — provider usage and whether a key is stored.
 * - `sync` — when a statement was last imported.
 */
export type AccountingFamily = 'store' | 'ledger' | 'suggestions' | 'budgets' | 'netWorth' | 'goals' | 'llm' | 'sync'

const NET_WORTH_PREFIXES: readonly (readonly string[])[] = [
  prefixes.netWorth,
  prefixes.netWorthHistory,
  prefixes.netWorthHistoryByAccount,
  prefixes.interestSummary,
  prefixes.goalsSummary,
]

const SUGGESTION_PREFIXES: readonly (readonly string[])[] = [
  prefixes.transferSuggestions,
  prefixes.duplicateSuggestions,
  prefixes.dismissedSuggestions,
]

const BUDGET_PREFIXES: readonly (readonly string[])[] = [prefixes.budgetComparison, prefixes.suggestedBudgetAmount]

const FAMILY_PREFIXES: Record<AccountingFamily, readonly (readonly string[])[]> = {
  store: [prefixes.store],
  ledger: [
    prefixes.postings,
    prefixes.categoryTotals,
    prefixes.monthlyIncomeExpense,
    prefixes.spendCurve,
    ...BUDGET_PREFIXES,
    ...NET_WORTH_PREFIXES,
    ...SUGGESTION_PREFIXES,
  ],
  suggestions: SUGGESTION_PREFIXES,
  budgets: BUDGET_PREFIXES,
  netWorth: NET_WORTH_PREFIXES,
  goals: [prefixes.goalsSummary],
  llm: [prefixes.llmUsage, prefixes.llmSettings],
  sync: [prefixes.syncStatus],
}

/**
 * Every query prefix the named families cover, deduplicated.
 *
 * Exported for the test that pins each mutation's declared families to the
 * queries they actually sweep — the assertion that keeps this vocabulary
 * honest as queries are added.
 *
 * @param families - What the write changed.
 * @returns One entry per distinct prefix, in no particular order.
 */
export function invalidatedPrefixes(families: readonly AccountingFamily[]): (readonly string[])[] {
  const seen = new Map<string, readonly string[]>()
  for (const family of families) {
    for (const prefix of FAMILY_PREFIXES[family]) seen.set(prefix.join('/'), prefix)
  }
  return [...seen.values()]
}

/**
 * Invalidate exactly the queries a write can have changed.
 *
 * Replaces a shared helper that invalidated the bare `['accounting']` prefix,
 * which every cached query in the feature sits under: a category click on the
 * Transactions page refetched the whole persisted store and the LLM usage
 * counters alongside the posting list, and the currency list and the
 * supported-import-kinds list — neither of which any write can change — were
 * refetched on every mutation in the app.
 *
 * @returns A function taking the families the write changed. Awaitable, so a
 *   caller that needs the refetch to have landed can wait for it; nothing does
 *   today, and `onSuccess` does not need to.
 */
export function useInvalidateAccounting() {
  const queryClient = useQueryClient()
  return useCallback(
    (...families: AccountingFamily[]) =>
      Promise.all(
        invalidatedPrefixes(families).map((queryKey) => queryClient.invalidateQueries({ queryKey: [...queryKey] })),
      ),
    [queryClient],
  )
}
