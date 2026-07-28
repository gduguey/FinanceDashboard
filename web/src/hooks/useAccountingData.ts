import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  type AccountCreate,
  type AccountUpdate,
  accountingApi,
  type CategoryCreate,
  type SubcategoryCreate,
  type TagCreate,
} from '@/lib/accountingApi'
import { BASE_CURRENCY } from '@/lib/currency'
import type {
  AccountingStore,
  BudgetUpsert,
  CanonicalCategoryOverrides,
  CategoryPatternCreate,
  CategoryPatternUpdate,
  CurrencyCode,
  DismissSuggestionRequest,
  GeneralBudgetUpsert,
  GoalContributionCreate,
  GoalContributionUpdate,
  GoalCreate,
  GoalUpdate,
  LlmSettingsUpdate,
  ManualOverride,
  ManualTransfer,
  OpeningBalance,
  OtherAssetCreate,
  PostingMergeUpsert,
  PostingSplitLeg,
  RecurringAddition,
  RecurringAdditionCreate,
  RecurringAdditionUpdate,
  SimulatorScenario,
  SimulatorScenarioCreate,
  TransferLinkCreate,
  TransferRuleCreate,
  TransferRuleUpdate,
  WithdrawalPriorityEntry,
} from '@/types/accounting'

const keys = {
  store: ['accounting', 'store'],
  currencies: ['accounting', 'currencies'],
  llmUsage: ['accounting', 'llm-usage'],
  llmSettings: ['accounting', 'settings', 'llm'],
  llmVerify: (provider: 'gemini' | 'mistral') => ['accounting', 'settings', 'llm', 'verify', provider],
  supportedImportKinds: ['accounting', 'supported-import-kinds'],
  syncStatus: ['accounting', 'sync-status'],
  currentExchangeRate: (currency: string) => ['accounting', 'exchange-rate', 'current', currency],
  exchangeRateHistory: (currency: string) => ['accounting', 'exchange-rate', 'history', currency],
  postings: ['accounting', 'postings'],
  transferSuggestions: ['accounting', 'transfer-suggestions'],
  duplicateSuggestions: ['accounting', 'duplicate-suggestions'],
  dismissedSuggestions: ['accounting', 'dismissed-suggestions'],
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
  suggestedBudgetAmount: (
    categoryId: string,
    month: string,
    lookbackMonths?: number,
    subcategoryId?: string,
    displayCurrency?: string,
  ) => [
    'accounting',
    'suggested-budget-amount',
    categoryId,
    month,
    lookbackMonths ?? {},
    subcategoryId ?? {},
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

export const useLlmSettings = () => useQuery({ queryKey: keys.llmSettings, queryFn: accountingApi.llmSettings })

export function useSetLlmSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (update: LlmSettingsUpdate) => accountingApi.setLlmSettings(update),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.llmSettings })
      // `configured` (used to decide whether to re-verify a saved key)
      // lives in llm-usage's response, not llm-settings' — without this,
      // saving a brand-new key wouldn't flip `configured` to true until
      // some unrelated refetch happened to touch this query.
      queryClient.invalidateQueries({ queryKey: keys.llmUsage })
    },
  })
}

export function useClearLlmSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => accountingApi.clearLlmSettings(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.llmSettings }),
  })
}

export type ConnectionState = 'none' | 'checking' | 'invalid' | 'connected'

// The one shared definition of "is this provider actually connected" —
// Settings and Transactions' usage banner both read this same cached
// query rather than each deciding for themselves. A real auth check (one
// free models.list() call), not just "is a key present" — `configured`
// alone doesn't catch a wrong/expired key. Saving or clearing a key
// invalidates `keys.llmSettings`, which — via React Query's prefix
// matching — invalidates this query too.
export function useLlmConnectionStatus(provider: 'gemini' | 'mistral'): {
  state: ConnectionState
  error: string | null
} {
  const { data: usage } = useLlmUsage()
  const configured = usage?.[provider]?.configured ?? false
  const verify = useQuery({
    queryKey: keys.llmVerify(provider),
    queryFn: () => accountingApi.verifyLlmSettings(provider),
    enabled: configured,
    staleTime: 30_000,
  })

  if (!configured) return { state: 'none', error: null }
  if (verify.isPending || !verify.data) return { state: 'checking', error: null }
  if (!verify.data.ok) return { state: 'invalid', error: verify.data.error }
  return { state: 'connected', error: null }
}

export const useSupportedImportKinds = () =>
  useQuery({ queryKey: keys.supportedImportKinds, queryFn: accountingApi.supportedImportKinds })

export const useSyncStatus = () => useQuery({ queryKey: keys.syncStatus, queryFn: accountingApi.syncStatus })

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

export const useDuplicateSuggestions = (windowDays?: number) =>
  useQuery({
    queryKey: [...keys.duplicateSuggestions, windowDays ?? {}],
    queryFn: () => accountingApi.duplicateSuggestions(windowDays),
  })

export const useDismissedSuggestions = () =>
  useQuery({ queryKey: keys.dismissedSuggestions, queryFn: accountingApi.dismissedSuggestions })

export function useDismissSuggestion() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (body: DismissSuggestionRequest) => accountingApi.dismissSuggestion(body),
    onSuccess: invalidate,
  })
}

export function useRestoreSuggestion() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (suggestionId: string) => accountingApi.restoreSuggestion(suggestionId),
    onSuccess: invalidate,
  })
}

export function useCreatePostingMerge() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (merge: PostingMergeUpsert) => accountingApi.createPostingMerge(merge),
    onSuccess: invalidate,
  })
}

export function useRemovePostingMerge() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (mergeId: string) => accountingApi.removePostingMerge(mergeId),
    onSuccess: invalidate,
  })
}

export function useCreateTransferLink() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (link: TransferLinkCreate) => accountingApi.createTransferLink(link),
    onSuccess: invalidate,
  })
}

export function useRemoveTransferLink() {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (linkId: string) => accountingApi.removeTransferLink(linkId),
    // Drops the link from the cached store the instant "unmark as
    // transfer"/"exclude this transfer" fires, instead of waiting on the
    // round trip — the badge disappears immediately and just reconciles
    // quietly once the real response (and `onSuccess`'s refetch) lands.
    // Rolled back on failure; `onSuccess: invalidate` still refetches
    // regardless, both to reconcile with whatever the server actually
    // persisted and to advance `lastKnownStoreVersion` for the next
    // mutation's own version-conflict check (see `accountingApi.ts`'s
    // `request`) — this optimistic patch only changes how fast the UI
    // *looks* like it responded, never which version header goes out next.
    onMutate: async (linkId) => {
      await queryClient.cancelQueries({ queryKey: keys.store })
      const previous = queryClient.getQueryData<AccountingStore>(keys.store)
      if (previous) {
        queryClient.setQueryData<AccountingStore>(keys.store, {
          ...previous,
          transfer_links: previous.transfer_links.filter((link) => link.link_id !== linkId),
        })
      }
      return { previous }
    },
    onError: (_error, _linkId, context) => {
      if (context?.previous) queryClient.setQueryData(keys.store, context.previous)
    },
    onSuccess: invalidate,
  })
}

export const useNetWorth = (asOf?: string, displayCurrency?: string) =>
  useQuery({
    queryKey: keys.netWorth(asOf, displayCurrency),
    queryFn: () => accountingApi.netWorth(asOf, displayCurrency),
  })

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

export const useSuggestedBudgetAmount = (
  categoryId: string,
  month: string,
  lookbackMonths?: number,
  subcategoryId?: string,
  displayCurrency?: string,
) =>
  useQuery({
    queryKey: keys.suggestedBudgetAmount(categoryId, month, lookbackMonths, subcategoryId, displayCurrency),
    queryFn: () =>
      accountingApi.suggestedBudgetAmount(categoryId, month, lookbackMonths, subcategoryId, displayCurrency),
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
    queryKey: keys.simulatorProject(
      initialCapital,
      monthlyContribution,
      horizonYears,
      annualRatePct,
      compoundingFrequency,
    ),
    queryFn: () =>
      accountingApi.simulatorProject(
        initialCapital,
        monthlyContribution,
        horizonYears,
        annualRatePct,
        compoundingFrequency,
      ),
  })

export function useDeleteSimulatorScenario() {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (scenarioId: string) => accountingApi.deleteSimulatorScenario(scenarioId),
    onMutate: async (scenarioId) => {
      await queryClient.cancelQueries({ queryKey: keys.store })
      const previous = queryClient.getQueryData<AccountingStore>(keys.store)
      if (previous) {
        queryClient.setQueryData<AccountingStore>(keys.store, {
          ...previous,
          simulator_scenarios: previous.simulator_scenarios.filter((s) => s.scenario_id !== scenarioId),
        })
      }
      return { previous }
    },
    onError: (_error, _scenarioId, context) => {
      if (context?.previous) queryClient.setQueryData(keys.store, context.previous)
    },
    onSuccess: invalidate,
  })
}

export function useCreateSimulatorScenario() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (scenario: SimulatorScenarioCreate) => accountingApi.createSimulatorScenario(scenario),
    onSuccess: invalidate,
  })
}

// Unlike a whole-tree replace, this refuses a
// same-classification, same-name duplicate server-side (409) instead of
// silently overwriting whatever already had that computed id.
export function useCreateCategory() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (category: CategoryCreate) => accountingApi.createCategory(category),
    onSuccess: invalidate,
  })
}

export function useCreateSubcategory() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ parentId, subcategory }: { parentId: string; subcategory: SubcategoryCreate }) =>
      accountingApi.createSubcategory(parentId, subcategory),
    onSuccess: invalidate,
  })
}

// Read-only — reports whether a rename would merge into an existing
// category without persisting anything, so no cache invalidation.
export function useCategoryRenamePreview() {
  return useMutation({
    mutationFn: ({ categoryId, name }: { categoryId: string; name: string }) =>
      accountingApi.categoryRenamePreview(categoryId, name),
  })
}

// Renaming to an existing category's (or, for a subcategory, an existing
// sibling's) name merges into it — repointing postings, rules, budgets,
// and manual overrides — so this invalidates everything, not just the
// category tree, unlike a plain `useSetCategories` edit.
export function useRenameCategory() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ categoryId, name }: { categoryId: string; name: string }) =>
      accountingApi.renameCategory(categoryId, name),
    onSuccess: invalidate,
  })
}

// Read-only — reports how many postings deleting a category would
// uncategorize without persisting anything, so no cache invalidation.
export function useCategoryDeletePreview() {
  return useMutation({
    mutationFn: (categoryId: string) => accountingApi.categoryDeletePreview(categoryId),
  })
}

// Uncategorizes every posting (and clears/drops every rule, budget,
// pattern, and split leg) referencing the deleted category or, for a
// top-level one, any of its subcategories — so this invalidates
// everything, same as a merge does.
export function useDeleteCategory() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (categoryId: string) => accountingApi.deleteCategory(categoryId),
    onSuccess: invalidate,
  })
}

export function useDeleteTag() {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (tagId: string) => accountingApi.deleteTag(tagId),
    onMutate: async (tagId) => {
      await queryClient.cancelQueries({ queryKey: keys.store })
      const previous = queryClient.getQueryData<AccountingStore>(keys.store)
      if (previous) {
        const remaining = { ...previous.tags }
        delete remaining[tagId]
        queryClient.setQueryData<AccountingStore>(keys.store, { ...previous, tags: remaining })
      }
      return { previous }
    },
    onError: (_error, _tagId, context) => {
      if (context?.previous) queryClient.setQueryData(keys.store, context.previous)
    },
    onSuccess: invalidate,
  })
}

// Unlike a whole-list replace, this refuses a same-name
// (case-insensitive) duplicate server-side (409) instead of silently
// overwriting whatever already had that computed id.
export function useCreateTag() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (tag: TagCreate) => accountingApi.createTag(tag),
    onSuccess: invalidate,
  })
}

// Read-only — reports whether a rename would merge into an existing tag
// without persisting anything, so no cache invalidation.
export function useTagRenamePreview() {
  return useMutation({
    mutationFn: ({ tagId, name }: { tagId: string; name: string }) => accountingApi.tagRenamePreview(tagId, name),
  })
}

// Renaming to an existing tag's name merges into it — repointing
// `posting_tags` rows and `tag_ids_override` arrays — so this invalidates
// everything, not just the tag list.
export function useRenameTag() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ tagId, name }: { tagId: string; name: string }) => accountingApi.renameTag(tagId, name),
    onSuccess: invalidate,
  })
}

export function usePatchTransferRule() {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ ruleId, update }: { ruleId: string; update: TransferRuleUpdate }) =>
      accountingApi.patchTransferRule(ruleId, update),
    // Same optimistic-patch-then-reconcile approach as `useRemoveTransferLink`,
    // but scoped to the one rule being edited (toggling active, editing
    // fields, excluding/un-excluding a transaction) rather than the whole
    // `transfer_rules` array — each call carries and checks its own
    // `update.expected_version`, not the shared whole-store one, so firing
    // several of these back-to-back (e.g. rapid clicks) can never silently
    // clobber a sibling rule's edit or spuriously conflict with an
    // unrelated save elsewhere in the store. Rolled back on failure;
    // `onSuccess: invalidate` still refetches regardless, to reconcile
    // with whatever the server actually persisted (including the bumped
    // `version` for this rule's next edit).
    onMutate: async ({ ruleId, update }) => {
      await queryClient.cancelQueries({ queryKey: keys.store })
      const previous = queryClient.getQueryData<AccountingStore>(keys.store)
      if (previous) {
        queryClient.setQueryData<AccountingStore>(keys.store, {
          ...previous,
          transfer_rules: previous.transfer_rules.map((rule) =>
            rule.rule_id === ruleId ? { ...rule, ...update } : rule,
          ),
        })
      }
      return { previous }
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(keys.store, context.previous)
    },
    onSuccess: invalidate,
  })
}

export function useDeleteTransferRule() {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (ruleId: string) => accountingApi.deleteTransferRule(ruleId),
    onMutate: async (ruleId) => {
      await queryClient.cancelQueries({ queryKey: keys.store })
      const previous = queryClient.getQueryData<AccountingStore>(keys.store)
      if (previous) {
        queryClient.setQueryData<AccountingStore>(keys.store, {
          ...previous,
          transfer_rules: previous.transfer_rules.filter((rule) => rule.rule_id !== ruleId),
        })
      }
      return { previous }
    },
    onError: (_error, _ruleId, context) => {
      if (context?.previous) queryClient.setQueryData(keys.store, context.previous)
    },
    onSuccess: invalidate,
  })
}

export function useCreateTransferRule() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (rule: TransferRuleCreate) => accountingApi.createTransferRule(rule),
    onSuccess: invalidate,
  })
}

export function useDeleteOtherAsset() {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (assetId: string) => accountingApi.deleteOtherAsset(assetId),
    onMutate: async (assetId) => {
      await queryClient.cancelQueries({ queryKey: keys.store })
      const previous = queryClient.getQueryData<AccountingStore>(keys.store)
      if (previous) {
        queryClient.setQueryData<AccountingStore>(keys.store, {
          ...previous,
          other_assets: previous.other_assets.filter((asset) => asset.asset_id !== assetId),
        })
      }
      return { previous }
    },
    onError: (_error, _assetId, context) => {
      if (context?.previous) queryClient.setQueryData(keys.store, context.previous)
    },
    onSuccess: invalidate,
  })
}

export function useCreateOtherAsset() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (asset: OtherAssetCreate) => accountingApi.createOtherAsset(asset),
    onSuccess: invalidate,
  })
}

// Single-item budget mutations — only send the one budget being changed
// over the wire, not the user's entire budget history for every edit
// (see accounting.api.routers.store.post_budget/post_general_budget).
export function useSetBudget() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (budget: BudgetUpsert) => accountingApi.setBudget(budget),
    onSuccess: invalidate,
  })
}

export function useRemoveBudget() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (budgetId: string) => accountingApi.removeBudget(budgetId),
    onSuccess: invalidate,
  })
}

export function useSetGeneralBudget() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (generalBudget: GeneralBudgetUpsert) => accountingApi.setGeneralBudget(generalBudget),
    onSuccess: invalidate,
  })
}

export function useRemoveGeneralBudget() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (key: string) => accountingApi.removeGeneralBudget(key),
    onSuccess: invalidate,
  })
}

export function useCreateAccount() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (account: AccountCreate) => accountingApi.postAccount(account),
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

export function useCloseAccount() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ accountId, transfers }: { accountId: string; transfers: ManualTransfer[] }) =>
      accountingApi.closeAccount(accountId, transfers),
    onSuccess: invalidate,
  })
}

export function useReopenAccount() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (accountId: string) => accountingApi.reopenAccount(accountId),
    onSuccess: invalidate,
  })
}

export function useImportCsv() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ file, accountId }: { file: File; accountId: string }) => accountingApi.importCsv(file, accountId),
    onSuccess: invalidate,
  })
}

export function useImportCanonicalCsv() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({
      file,
      accountId,
      separator,
      dateOrder,
      categoryOverrides,
    }: {
      file: File
      accountId: string
      separator?: string
      dateOrder?: string
      categoryOverrides?: CanonicalCategoryOverrides
    }) => accountingApi.importCanonicalCsv(file, accountId, separator, dateOrder, categoryOverrides),
    onSuccess: invalidate,
  })
}

// Read-only — never touches the store or ledger, so no cache invalidation.
export function useCanonicalImportPreview() {
  return useMutation({
    mutationFn: ({
      file,
      accountId,
      currency,
      separator,
      dateOrder,
    }: {
      file: File
      accountId: string
      currency: CurrencyCode
      separator?: string
      dateOrder?: string
    }) => accountingApi.previewCanonicalImport(file, accountId, currency, separator, dateOrder),
  })
}

// Read-only — matches a file against the ledger without persisting anything, so no cache invalidation.
export function useCategorizeFromFilePreview() {
  return useMutation({
    mutationFn: ({ file, separator, dateOrder }: { file: File; separator?: string; dateOrder?: string }) =>
      accountingApi.previewCategorizeFromFile(file, separator, dateOrder),
  })
}

export function useApplyCategorizeFromFile() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({
      file,
      confirmedRowNumbers,
      separator,
      dateOrder,
    }: {
      file: File
      confirmedRowNumbers: number[]
      separator?: string
      dateOrder?: string
    }) => accountingApi.applyCategorizeFromFile(file, confirmedRowNumbers, separator, dateOrder),
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
    mutationFn: ({ postingId, override }: { postingId: string; override: Partial<ManualOverride> }) =>
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

export function usePatchCategoryPattern() {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ patternId, update }: { patternId: string; update: CategoryPatternUpdate }) =>
      accountingApi.patchCategoryPattern(patternId, update),
    // Same per-row optimistic-patch approach as `usePatchTransferRule` — editing or toggling one
    // pattern can never clobber a concurrent edit to a different one.
    onMutate: async ({ patternId, update }) => {
      await queryClient.cancelQueries({ queryKey: keys.store })
      const previous = queryClient.getQueryData<AccountingStore>(keys.store)
      if (previous) {
        queryClient.setQueryData<AccountingStore>(keys.store, {
          ...previous,
          category_patterns: {
            ...previous.category_patterns,
            [patternId]: { ...previous.category_patterns[patternId], ...update },
          },
        })
      }
      return { previous }
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(keys.store, context.previous)
    },
    onSuccess: invalidate,
  })
}

export function useDeleteCategoryPattern() {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (patternId: string) => accountingApi.deleteCategoryPattern(patternId),
    onMutate: async (patternId) => {
      await queryClient.cancelQueries({ queryKey: keys.store })
      const previous = queryClient.getQueryData<AccountingStore>(keys.store)
      if (previous) {
        const remaining = { ...previous.category_patterns }
        delete remaining[patternId]
        queryClient.setQueryData<AccountingStore>(keys.store, { ...previous, category_patterns: remaining })
      }
      return { previous }
    },
    onError: (_error, _patternId, context) => {
      if (context?.previous) queryClient.setQueryData(keys.store, context.previous)
    },
    onSuccess: invalidate,
  })
}

export function useCreateCategoryPattern() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (pattern: CategoryPatternCreate) => accountingApi.createCategoryPattern(pattern),
    onSuccess: invalidate,
  })
}

export function usePatchGoal() {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ goalId, update }: { goalId: string; update: GoalUpdate }) => accountingApi.patchGoal(goalId, update),
    // Same per-row optimistic-patch approach as `usePatchTransferRule` —
    // each call carries and checks its own `update.expected_version`, not
    // a shared whole-store one, so editing two different goals can never
    // clobber each other regardless of how the requests interleave.
    onMutate: async ({ goalId, update }) => {
      await queryClient.cancelQueries({ queryKey: keys.store })
      const previous = queryClient.getQueryData<AccountingStore>(keys.store)
      if (previous) {
        queryClient.setQueryData<AccountingStore>(keys.store, {
          ...previous,
          goals: { ...previous.goals, [goalId]: { ...previous.goals[goalId], ...update } },
        })
      }
      return { previous }
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(keys.store, context.previous)
    },
    onSuccess: invalidate,
  })
}

export function useDeleteGoal() {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (goalId: string) => accountingApi.deleteGoal(goalId),
    onMutate: async (goalId) => {
      await queryClient.cancelQueries({ queryKey: keys.store })
      const previous = queryClient.getQueryData<AccountingStore>(keys.store)
      if (previous) {
        const remaining = { ...previous.goals }
        delete remaining[goalId]
        queryClient.setQueryData<AccountingStore>(keys.store, { ...previous, goals: remaining })
      }
      return { previous }
    },
    onError: (_error, _goalId, context) => {
      if (context?.previous) queryClient.setQueryData(keys.store, context.previous)
    },
    onSuccess: invalidate,
  })
}

export function useCreateGoal() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (goal: GoalCreate) => accountingApi.createGoal(goal),
    onSuccess: invalidate,
  })
}

export function useCreateGoalContribution() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (contribution: GoalContributionCreate) => accountingApi.createGoalContribution(contribution),
    onSuccess: invalidate,
  })
}

export function useUpdateGoalContribution() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ contributionId, contribution }: { contributionId: string; contribution: GoalContributionUpdate }) =>
      accountingApi.updateGoalContribution(contributionId, contribution),
    onSuccess: invalidate,
  })
}

export function useRemoveGoalContribution() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (contributionId: string) => accountingApi.removeGoalContribution(contributionId),
    onSuccess: invalidate,
  })
}

// Whole-list PUT — used only for drag-to-reorder (a pure ordering operation); single-rule field
// edits and deletes go through the scoped hooks below.
export function useSetRecurringAdditions() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (additions: RecurringAddition[]) => accountingApi.putRecurringAdditions(additions),
    onSuccess: invalidate,
  })
}

export function usePatchRecurringAddition() {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ additionId, update }: { additionId: string; update: RecurringAdditionUpdate }) =>
      accountingApi.patchRecurringAddition(additionId, update),
    // Optimistically patch the one addition in the cached list so a field edit
    // reflects immediately instead of lagging until the refetch — same
    // onMutate/onError shape as `usePatchGoal`, over a list rather than a map.
    onMutate: async ({ additionId, update }) => {
      await queryClient.cancelQueries({ queryKey: keys.store })
      const previous = queryClient.getQueryData<AccountingStore>(keys.store)
      if (previous) {
        queryClient.setQueryData<AccountingStore>(keys.store, {
          ...previous,
          recurring_additions: previous.recurring_additions.map((addition) =>
            addition.addition_id === additionId ? { ...addition, ...update } : addition,
          ),
        })
      }
      return { previous }
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(keys.store, context.previous)
    },
    onSuccess: invalidate,
  })
}

export function useDeleteRecurringAddition() {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (additionId: string) => accountingApi.deleteRecurringAddition(additionId),
    onMutate: async (additionId) => {
      await queryClient.cancelQueries({ queryKey: keys.store })
      const previous = queryClient.getQueryData<AccountingStore>(keys.store)
      if (previous) {
        queryClient.setQueryData<AccountingStore>(keys.store, {
          ...previous,
          recurring_additions: previous.recurring_additions.filter((addition) => addition.addition_id !== additionId),
        })
      }
      return { previous }
    },
    onError: (_error, _additionId, context) => {
      if (context?.previous) queryClient.setQueryData(keys.store, context.previous)
    },
    onSuccess: invalidate,
  })
}

export function useCreateRecurringAddition() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (addition: RecurringAdditionCreate) => accountingApi.createRecurringAddition(addition),
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
  return useMutation({
    mutationFn: (asOf?: string) => accountingApi.runRecurringAdditions(asOf),
    onSuccess: invalidate,
  })
}

export function useRunWithdrawalAutomation() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (asOf?: string) => accountingApi.runWithdrawalAutomation(asOf),
    onSuccess: invalidate,
  })
}

export function useSimulateContribution() {
  return useMutation({
    mutationFn: ({ goalId, date, amount }: { goalId: string; date: string; amount: number }) =>
      accountingApi.simulateContribution(goalId, date, amount),
  })
}
