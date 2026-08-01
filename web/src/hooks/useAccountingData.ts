/**
 * Every accounting query and mutation hook, from one import path.
 *
 * A barrel over `hooks/accounting/`, which is where the hooks actually live.
 * They were one 1,144-line module with 91 exports; splitting it by subject —
 * settings, taxonomy, postings, imports, analytics, goals — is what makes each
 * file short enough to read whole, and re-exporting from here is what keeps
 * roughly forty call sites from having to learn which subject their hook
 * belongs to.
 *
 * Two modules are worth opening directly rather than through this file:
 * `accounting/keys.ts` holds the cache keys and the vocabulary every mutation
 * declares its invalidation in, and `accounting/mutations.ts` holds the three
 * factories every hook is built from.
 */

export {
  useBudgetComparison,
  useCategoryTotals,
  useCreateSimulatorScenario,
  useDeleteSimulatorScenario,
  useInterestSummary,
  useMonthlyIncomeExpense,
  useNetWorth,
  useNetWorthHistory,
  useNetWorthHistoryByAccount,
  useSimulatorProjection,
  useSpendCurve,
  useSuggestedBudgetAmount,
} from '@/hooks/accounting/analytics'
export {
  useCreateContributionAutomation,
  useCreateGoal,
  useCreateGoalContribution,
  useCreateWithdrawalAutomation,
  useDeleteGoal,
  useDeleteGoalAutomation,
  useGoalsSummary,
  usePatchGoal,
  usePatchGoalAutomation,
  useRemoveGoalContribution,
  useReorderContributionAutomations,
  useReorderWithdrawalAutomations,
  useRunRecurringAdditions,
  useRunWithdrawalAutomation,
  useSimulateContribution,
  useUpdateGoalContribution,
} from '@/hooks/accounting/goals'
export {
  useApplyCategorizeFromFile,
  useCanonicalImportPreview,
  useCategorizeFromFilePreview,
  useImportCanonicalCsv,
  useImportCsv,
  useImportPaystub,
  useRebuildLedger,
} from '@/hooks/accounting/imports'
export { type AccountingFamily, invalidatedPrefixes, keys, useInvalidateAccounting } from '@/hooks/accounting/keys'
export {
  patchEntry,
  useAccountingMutation,
  useOptimisticStoreMutation,
  usePreviewMutation,
  withoutEntry,
} from '@/hooks/accounting/mutations'
export {
  useAiSuggestCategory,
  useCreatePostingMerge,
  useCreateTransferLink,
  useDeletePostingSplit,
  useDismissedSuggestions,
  useDismissSuggestion,
  useDuplicateSuggestions,
  usePatternSuggestCategory,
  usePatternSuggestCategoryBulk,
  usePostingMonths,
  usePostingSummary,
  usePostingsPage,
  useRemovePostingMerge,
  useRemoveTransferLink,
  useRestoreSuggestion,
  useSetPostingOverride,
  useSetPostingSplit,
  useTransactionLegs,
  useTransferSuggestions,
  useValidatePending,
} from '@/hooks/accounting/postings'
export {
  type ConnectionState,
  useAccountingStore,
  useClearLlmSettings,
  useCurrencies,
  useCurrentExchangeRate,
  useExchangeRateHistory,
  useLlmConnectionStatus,
  useLlmSettings,
  useLlmUsage,
  useRatesToBase,
  useSetLlmSettings,
  useSupportedImportKinds,
  useSyncStatus,
} from '@/hooks/accounting/settings'
export {
  useCategoryDeletePreview,
  useCategoryRenamePreview,
  useCloseAccount,
  useCreateAccount,
  useCreateCategory,
  useCreateCategoryPattern,
  useCreateOtherAsset,
  useCreateSubcategory,
  useCreateTag,
  useCreateTransferRule,
  useDeleteAccount,
  useDeleteCategory,
  useDeleteCategoryPattern,
  useDeleteOtherAsset,
  useDeleteTag,
  useDeleteTransferRule,
  usePatchCategoryPattern,
  usePatchTransferRule,
  useRemoveBudget,
  useRenameCategory,
  useRenameTag,
  useReopenAccount,
  useSetBudget,
  useSetOpeningBalance,
  useTagRenamePreview,
  useUpdateAccount,
} from '@/hooks/accounting/taxonomy'
