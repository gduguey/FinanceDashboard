import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { accountingApi, type AccountUpdate, type ImportAccountInfo } from '@/lib/accountingApi'
import type {
  Account,
  Budget,
  Category,
  CategoryPattern,
  CurrencyCode,
  GeneralBudget,
  Goal,
  GoalContribution,
  ManualOverride,
  OpeningBalance,
  OtherAsset,
  PostingSplitLeg,
  RecurringAddition,
  SimulatorScenario,
  Tag,
  TransferRule,
  WithdrawalPriorityEntry,
} from '@/types/accounting'

const BASE_CURRENCY: CurrencyCode = 'USD'

const keys = {
  store: ['accounting', 'store'],
  currencies: ['accounting', 'currencies'],
  llmUsage: ['accounting', 'llm-usage'],
  supportedImportKinds: ['accounting', 'supported-import-kinds'],
  currentExchangeRate: (currency: string) => ['accounting', 'exchange-rate', 'current', currency],
  exchangeRateHistory: (currency: string) => ['accounting', 'exchange-rate', 'history', currency],
  postings: ['accounting', 'postings'],
  transferSuggestions: ['accounting', 'transfer-suggestions'],
  netWorth: (asOf?: string, displayCurrency?: string) => ['accounting', 'net-worth', asOf ?? {}, displayCurrency ?? {}],
  netWorthHistory: (start: string, end: string, intervalDays?: number, displayCurrency?: string) => [
    'accounting',
    'net-worth-history',
    start,
    end,
    intervalDays ?? {},
    displayCurrency ?? {},
  ],
  netWorthHistoryByAccount: (start: string, end: string, intervalDays?: number, displayCurrency?: string) => [
    'accounting',
    'net-worth-history-by-account',
    start,
    end,
    intervalDays ?? {},
    displayCurrency ?? {},
  ],
  categoryTotals: (start: string, end: string, accountIds?: string[], tagId?: string, displayCurrency?: string) => [
    'accounting',
    'category-totals',
    start,
    end,
    accountIds ?? [],
    tagId ?? {},
    displayCurrency ?? {},
  ],
  monthlyIncomeExpense: (start: string, end: string, displayCurrency?: string) => [
    'accounting',
    'monthly-income-expense',
    start,
    end,
    displayCurrency ?? {},
  ],
  spendCurve: (month: string, lookbackMonths?: number, displayCurrency?: string) => [
    'accounting',
    'spend-curve',
    month,
    lookbackMonths ?? {},
    displayCurrency ?? {},
  ],
  budgetComparison: (month: string, displayCurrency?: string) => [
    'accounting',
    'budget-comparison',
    month,
    displayCurrency ?? {},
  ],
  suggestedBudgetAmount: (categoryId: string, month: string, lookbackMonths?: number, displayCurrency?: string) => [
    'accounting',
    'suggested-budget-amount',
    categoryId,
    month,
    lookbackMonths ?? {},
    displayCurrency ?? {},
  ],
  interestSummary: (asOf?: string) => ['accounting', 'interest-summary', asOf ?? {}],
  simulatorProject: (
    initialCapital: number,
    monthlyContribution: number,
    horizonYears: number,
    annualRatePct: number,
    compoundingFrequency: string,
  ) => [
    'accounting',
    'simulator-project',
    initialCapital,
    monthlyContribution,
    horizonYears,
    annualRatePct,
    compoundingFrequency,
  ],
}

function useInvalidateAccounting() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ['accounting'] })
}

export const useAccountingStore = () => useQuery({ queryKey: keys.store, queryFn: accountingApi.store })

export const useCurrencies = () => useQuery({ queryKey: keys.currencies, queryFn: accountingApi.currencies })

export const useLlmUsage = () => useQuery({ queryKey: keys.llmUsage, queryFn: accountingApi.llmUsage })

export const useSupportedImportKinds = () =>
  useQuery({ queryKey: keys.supportedImportKinds, queryFn: accountingApi.supportedImportKinds })

export const useCurrentExchangeRate = (currency: string) =>
  useQuery({
    queryKey: keys.currentExchangeRate(currency),
    queryFn: () => accountingApi.currentExchangeRate(currency),
    retry: false,
  })

export const useExchangeRateHistory = (currency: string) =>
  useQuery({
    queryKey: keys.exchangeRateHistory(currency),
    queryFn: () => accountingApi.exchangeRateHistory(currency),
    retry: false,
  })

// A client-side rates-to-base table, for the one place this app converts
// currencies outside a backend response — mixing accounts and other
// assets into one allocation pie. Every non-base `CurrencyCode` needs its
// own smoothed rate synced first; a currency with none just contributes
// no rate (see `convertCurrency`, which would then leave its amounts
// unconverted rather than throwing mid-render).
export function useRatesToBase(nonBaseCurrencies: CurrencyCode[]) {
  const results = useQueries({
    queries: nonBaseCurrencies.map((code) => ({
      queryKey: keys.currentExchangeRate(code),
      queryFn: () => accountingApi.currentExchangeRate(code),
      retry: false,
    })),
  })
  const ratesToBase: Record<string, number> = { [BASE_CURRENCY]: 1 }
  for (const result of results) {
    if (result.data) ratesToBase[result.data.currency] = result.data.rate_to_base
  }
  return ratesToBase
}

export const usePostings = () => useQuery({ queryKey: keys.postings, queryFn: accountingApi.postings })

export const useTransferSuggestions = (windowDays?: number) =>
  useQuery({
    queryKey: [...keys.transferSuggestions, windowDays ?? {}],
    queryFn: () => accountingApi.transferSuggestions(windowDays),
  })

export const useNetWorth = (asOf?: string, displayCurrency?: string) =>
  useQuery({ queryKey: keys.netWorth(asOf, displayCurrency), queryFn: () => accountingApi.netWorth(asOf, displayCurrency) })

export const useNetWorthHistory = (start: string, end: string, intervalDays?: number, displayCurrency?: string) =>
  useQuery({
    queryKey: keys.netWorthHistory(start, end, intervalDays, displayCurrency),
    queryFn: () => accountingApi.netWorthHistory(start, end, intervalDays, displayCurrency),
  })

export const useNetWorthHistoryByAccount = (
  start: string,
  end: string,
  intervalDays?: number,
  displayCurrency?: string,
  enabled = true,
) =>
  useQuery({
    queryKey: keys.netWorthHistoryByAccount(start, end, intervalDays, displayCurrency),
    queryFn: () => accountingApi.netWorthHistoryByAccount(start, end, intervalDays, displayCurrency),
    enabled,
  })

export const useCategoryTotals = (
  start: string,
  end: string,
  accountIds?: string[],
  tagId?: string,
  displayCurrency?: string,
) =>
  useQuery({
    queryKey: keys.categoryTotals(start, end, accountIds, tagId, displayCurrency),
    queryFn: () => accountingApi.categoryTotals(start, end, accountIds, tagId, displayCurrency),
  })

export const useMonthlyIncomeExpense = (start: string, end: string, displayCurrency?: string) =>
  useQuery({
    queryKey: keys.monthlyIncomeExpense(start, end, displayCurrency),
    queryFn: () => accountingApi.monthlyIncomeExpense(start, end, displayCurrency),
  })

export const useSpendCurve = (month: string, lookbackMonths?: number, displayCurrency?: string) =>
  useQuery({
    queryKey: keys.spendCurve(month, lookbackMonths, displayCurrency),
    queryFn: () => accountingApi.spendCurve(month, lookbackMonths, displayCurrency),
  })

export const useBudgetComparison = (month: string, displayCurrency?: string) =>
  useQuery({
    queryKey: keys.budgetComparison(month, displayCurrency),
    queryFn: () => accountingApi.budgetComparison(month, displayCurrency),
  })

export const useSuggestedBudgetAmount = (categoryId: string, month: string, lookbackMonths?: number, displayCurrency?: string) =>
  useQuery({
    queryKey: keys.suggestedBudgetAmount(categoryId, month, lookbackMonths, displayCurrency),
    queryFn: () => accountingApi.suggestedBudgetAmount(categoryId, month, lookbackMonths, displayCurrency),
  })

export const useInterestSummary = (asOf?: string) =>
  useQuery({ queryKey: keys.interestSummary(asOf), queryFn: () => accountingApi.interestSummary(asOf) })

export const useSimulatorProjection = (
  initialCapital: number,
  monthlyContribution: number,
  horizonYears: number,
  annualRatePct: number,
  compoundingFrequency: SimulatorScenario['compounding_frequency'],
) =>
  useQuery({
    queryKey: keys.simulatorProject(initialCapital, monthlyContribution, horizonYears, annualRatePct, compoundingFrequency),
    queryFn: () =>
      accountingApi.simulatorProject(initialCapital, monthlyContribution, horizonYears, annualRatePct, compoundingFrequency),
  })

export function useSetSimulatorScenarios() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (scenarios: SimulatorScenario[]) => accountingApi.putSimulatorScenarios(scenarios),
    onSuccess: invalidate,
  })
}

export function useSyncExchangeRates() {
  const invalidate = useInvalidateAccounting()
  return useMutation({ mutationFn: accountingApi.syncExchangeRates, onSuccess: invalidate })
}

export function useSetCategories() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (categories: Record<string, Category>) => accountingApi.putCategories(categories),
    onSuccess: invalidate,
  })
}

export function useSetTags() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (tags: Record<string, Tag>) => accountingApi.putTags(tags),
    onSuccess: invalidate,
  })
}

export function useSetTransferRules() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (rules: TransferRule[]) => accountingApi.putTransferRules(rules),
    onSuccess: invalidate,
  })
}

export function useSetOtherAssets() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (otherAssets: OtherAsset[]) => accountingApi.putOtherAssets(otherAssets),
    onSuccess: invalidate,
  })
}

export function useSetBudgets() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (budgets: Budget[]) => accountingApi.putBudgets(budgets),
    onSuccess: invalidate,
  })
}

export function useSetGeneralBudgets() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (generalBudgets: Record<string, GeneralBudget>) => accountingApi.putGeneralBudgets(generalBudgets),
    onSuccess: invalidate,
  })
}

export function useCreateAccount() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (account: Account) => accountingApi.postAccount(account),
    onSuccess: invalidate,
  })
}

export function useUpdateAccount() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ accountId, update }: { accountId: string; update: AccountUpdate }) =>
      accountingApi.putAccount(accountId, update),
    onSuccess: invalidate,
  })
}

export function useDeleteAccount() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (accountId: string) => accountingApi.deleteAccount(accountId),
    onSuccess: invalidate,
  })
}

export function useSetOpeningBalance() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ accountId, openingBalance }: { accountId: string; openingBalance: OpeningBalance }) =>
      accountingApi.putOpeningBalance(accountId, openingBalance),
    onSuccess: invalidate,
  })
}

export function useDeleteOpeningBalance() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (accountId: string) => accountingApi.deleteOpeningBalance(accountId),
    onSuccess: invalidate,
  })
}

export function useImportCsv() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ file, info }: { file: File; info: ImportAccountInfo }) => accountingApi.importCsv(file, info),
    onSuccess: invalidate,
  })
}

// Read-only — reconciliation applies nothing, so no cache invalidation on success.
export function useImportPaystub() {
  return useMutation({ mutationFn: (file: File) => accountingApi.importPaystub(file) })
}

export function useRebuildLedger() {
  const invalidate = useInvalidateAccounting()
  return useMutation({ mutationFn: accountingApi.rebuild, onSuccess: invalidate })
}

export function useSetPostingOverride() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ postingId, override }: { postingId: string; override: ManualOverride }) =>
      accountingApi.putPostingOverride(postingId, override),
    onSuccess: invalidate,
  })
}

export function useSetPostingSplit() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ postingId, legs }: { postingId: string; legs: PostingSplitLeg[] }) =>
      accountingApi.putPostingSplit(postingId, legs),
    onSuccess: invalidate,
  })
}

export function useDeletePostingSplit() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (postingId: string) => accountingApi.deletePostingSplit(postingId),
    onSuccess: invalidate,
  })
}

export function useAiSuggestCategory() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ postingId, lockCategoryId }: { postingId: string; lockCategoryId?: string | null }) =>
      accountingApi.aiSuggestCategory(postingId, lockCategoryId),
    onSuccess: invalidate,
  })
}

export function usePatternSuggestCategory() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ postingId, lockCategoryId }: { postingId: string; lockCategoryId?: string | null }) =>
      accountingApi.patternSuggestCategory(postingId, lockCategoryId),
    onSuccess: invalidate,
  })
}

export function usePatternSuggestCategoryBulk() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (postingIds: string[]) => accountingApi.patternSuggestCategoryBulk(postingIds),
    onSuccess: invalidate,
  })
}

export function useValidatePending() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (postingIds: string[]) => accountingApi.validatePending(postingIds),
    onSuccess: invalidate,
  })
}

export function useSetCategoryPatterns() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (patterns: Record<string, CategoryPattern>) => accountingApi.putCategoryPatterns(patterns),
    onSuccess: invalidate,
  })
}

export function useSetGoals() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (goals: Record<string, Goal>) => accountingApi.putGoals(goals),
    onSuccess: invalidate,
  })
}

export function useSetGoalContributions() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (contributions: Record<string, GoalContribution>) => accountingApi.putGoalContributions(contributions),
    onSuccess: invalidate,
  })
}

export function useSetRecurringAdditions() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (additions: RecurringAddition[]) => accountingApi.putRecurringAdditions(additions),
    onSuccess: invalidate,
  })
}

export function useSetWithdrawalPriorities() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (priorities: WithdrawalPriorityEntry[]) => accountingApi.putWithdrawalPriorities(priorities),
    onSuccess: invalidate,
  })
}

export const useGoalsSummary = (asOf?: string, displayCurrency?: string) =>
  useQuery({
    queryKey: ['accounting', 'goals-summary', asOf ?? {}, displayCurrency ?? {}],
    queryFn: () => accountingApi.goalsSummary(asOf, displayCurrency),
  })

export function useRunRecurringAdditions() {
  const invalidate = useInvalidateAccounting()
  return useMutation({ mutationFn: (asOf?: string) => accountingApi.runRecurringAdditions(asOf), onSuccess: invalidate })
}

export function useRunWithdrawalAutomation() {
  const invalidate = useInvalidateAccounting()
  return useMutation({ mutationFn: (asOf?: string) => accountingApi.runWithdrawalAutomation(asOf), onSuccess: invalidate })
}

export function useSimulateContribution() {
  return useMutation({
    mutationFn: ({ goalId, date, amount }: { goalId: string; date: string; amount: number }) =>
      accountingApi.simulateContribution(goalId, date, amount),
  })
}
