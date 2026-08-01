import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type AccountingFamily, invalidatedPrefixes, keys } from '@/hooks/accounting/keys'
import * as hooks from '@/hooks/useAccountingData'
import { emptyStore } from '@/test/fixtures'

// One concrete cache key per query the app can hold, argument variants
// included — the probes a mutation's invalidation is measured against. Written
// out rather than derived from the same `prefixes` table the implementation
// uses, so a prefix that stops covering its own key's arguments is a failure
// here rather than a mutual agreement between two views of the same constant.
const PROBES: Record<string, readonly unknown[]> = {
  store: keys.store,
  currencies: keys.currencies,
  llmUsage: keys.llmUsage,
  llmSettings: keys.llmSettings,
  llmVerify: keys.llmVerify('gemini'),
  supportedImportKinds: keys.supportedImportKinds,
  syncStatus: keys.syncStatus,
  currentExchangeRate: keys.currentExchangeRate('EUR'),
  exchangeRateHistory: keys.exchangeRateHistory('EUR'),
  postingCount: keys.postingCount,
  postingMonths: keys.postingMonths,
  transferSuggestions: [...keys.transferSuggestions, 3],
  duplicateSuggestions: [...keys.duplicateSuggestions, 3],
  dismissedSuggestions: keys.dismissedSuggestions,
  netWorth: keys.netWorth('2024-06-30', 'USD'),
  netWorthHistory: keys.netWorthHistory('2024-01-01', '2024-06-30', 7, 'USD'),
  netWorthHistoryByAccount: keys.netWorthHistoryByAccount('2024-01-01', '2024-06-30', 7, 'USD'),
  categoryTotals: keys.categoryTotals('2024-01-01', '2024-06-30', ['a'], 't', 'USD'),
  monthlyIncomeExpense: keys.monthlyIncomeExpense('2024-01-01', '2024-06-30', 'USD'),
  spendCurve: keys.spendCurve('2024-06', 6, 'USD'),
  budgetComparison: keys.budgetComparison('2024-06', 'USD'),
  suggestedBudgetAmount: keys.suggestedBudgetAmount('c', '2024-06', 6, 's', 'USD'),
  interestSummary: keys.interestSummary('2024-06-30'),
  goalsSummary: keys.goalsSummary('2024-06-30', 'USD'),
  simulatorProject: keys.simulatorProject(1, 2, 3, 4, 'monthly'),
}

// React Query's own matching rule: a query is invalidated when one of the
// invalidated keys is a prefix of it.
function coveredBy(probe: readonly unknown[], prefixes: readonly (readonly string[])[]): boolean {
  return prefixes.some((prefix) => prefix.every((part, index) => probe[index] === part))
}

const FILE = new File(['date,amount\n'], 'statement.csv', { type: 'text/csv' })

// Every mutation hook that invalidates, with the families it declares. This
// table is the specification: each row asserts that firing the hook leaves
// exactly the probes those families cover stale, and every other probe fresh.
// A hook's own variables type is checked where it is called in the app; here
// every row is fired through `mutateAsync`'s contravariant parameter, which
// accepts `never` whatever the hook declares.
type MutationCase = [
  name: string,
  useMutationHook: () => { mutateAsync: (variables: never) => Promise<unknown> },
  variables: unknown,
  families: AccountingFamily[],
]

const MUTATIONS: MutationCase[] = [
  ['useSetLlmSettings', hooks.useSetLlmSettings, { gemini_api_key: 'k' }, ['llm']],
  ['useClearLlmSettings', hooks.useClearLlmSettings, undefined, ['llm']],
  [
    'useDismissSuggestion',
    hooks.useDismissSuggestion,
    { suggestionId: 's', kind: 'transfer', description: '' },
    ['suggestions'],
  ],
  ['useRestoreSuggestion', hooks.useRestoreSuggestion, 's', ['suggestions']],
  [
    'useCreatePostingMerge',
    hooks.useCreatePostingMerge,
    { kept_transaction_id: 't', duplicate_transaction_ids: ['u'] },
    ['ledger'],
  ],
  ['useRemovePostingMerge', hooks.useRemovePostingMerge, 'm', ['ledger']],
  [
    'useCreateTransferLink',
    hooks.useCreateTransferLink,
    { transaction_id_a: 'a', transaction_id_b: 'b' },
    ['store', 'ledger'],
  ],
  ['useRemoveTransferLink', hooks.useRemoveTransferLink, 'l', ['store', 'ledger']],
  ['useDeleteSimulatorScenario', hooks.useDeleteSimulatorScenario, 's', ['store']],
  ['useCreateSimulatorScenario', hooks.useCreateSimulatorScenario, {}, ['store']],
  ['useCreateCategory', hooks.useCreateCategory, { name: 'n', classification: 'expense', color: '#000' }, ['store']],
  [
    'useCreateSubcategory',
    hooks.useCreateSubcategory,
    { parentId: 'p', subcategory: { name: 'n', color: '#000' } },
    ['store'],
  ],
  ['useRenameCategory', hooks.useRenameCategory, { categoryId: 'c', name: 'n' }, ['store', 'ledger']],
  ['useDeleteCategory', hooks.useDeleteCategory, 'c', ['store', 'ledger']],
  ['useDeleteTag', hooks.useDeleteTag, 't', ['store', 'ledger']],
  ['useCreateTag', hooks.useCreateTag, { name: 'n' }, ['store']],
  ['useRenameTag', hooks.useRenameTag, { tagId: 't', name: 'n' }, ['store', 'ledger']],
  ['usePatchTransferRule', hooks.usePatchTransferRule, { ruleId: 'r', update: {} }, ['store', 'ledger']],
  ['useDeleteTransferRule', hooks.useDeleteTransferRule, 'r', ['store', 'ledger']],
  ['useCreateTransferRule', hooks.useCreateTransferRule, {}, ['store', 'ledger']],
  ['useDeleteOtherAsset', hooks.useDeleteOtherAsset, 'a', ['store', 'netWorth']],
  ['useCreateOtherAsset', hooks.useCreateOtherAsset, {}, ['store', 'netWorth']],
  ['useSetBudget', hooks.useSetBudget, {}, ['store', 'budgets']],
  ['useRemoveBudget', hooks.useRemoveBudget, 'b', ['store', 'budgets']],
  ['useCreateAccount', hooks.useCreateAccount, {}, ['store']],
  ['useUpdateAccount', hooks.useUpdateAccount, { accountId: 'a', update: {} }, ['store', 'ledger']],
  ['useDeleteAccount', hooks.useDeleteAccount, 'a', ['store', 'ledger']],
  ['useSetOpeningBalance', hooks.useSetOpeningBalance, { accountId: 'a', openingBalance: {} }, ['netWorth']],
  ['useCloseAccount', hooks.useCloseAccount, { accountId: 'a', transfers: [] }, ['store', 'ledger']],
  ['useReopenAccount', hooks.useReopenAccount, 'a', ['store', 'ledger']],
  ['useImportCsv', hooks.useImportCsv, { file: FILE, accountId: 'a' }, ['store', 'ledger', 'sync']],
  ['useImportCanonicalCsv', hooks.useImportCanonicalCsv, { file: FILE, accountId: 'a' }, ['store', 'ledger', 'sync']],
  [
    'useApplyCategorizeFromFile',
    hooks.useApplyCategorizeFromFile,
    { file: FILE, confirmedRowNumbers: [1] },
    ['store', 'ledger'],
  ],
  ['useRebuildLedger', hooks.useRebuildLedger, undefined, ['store', 'ledger', 'sync']],
  ['useSetPostingOverride', hooks.useSetPostingOverride, { postingId: 'p', override: {} }, ['ledger']],
  ['useSetPostingSplit', hooks.useSetPostingSplit, { postingId: 'p', legs: [] }, ['ledger']],
  ['useDeletePostingSplit', hooks.useDeletePostingSplit, 'p', ['ledger']],
  ['useAiSuggestCategory', hooks.useAiSuggestCategory, { postingId: 'p' }, ['ledger', 'llm']],
  ['usePatternSuggestCategory', hooks.usePatternSuggestCategory, { postingId: 'p' }, ['ledger']],
  ['usePatternSuggestCategoryBulk', hooks.usePatternSuggestCategoryBulk, ['p'], ['ledger']],
  ['useValidatePending', hooks.useValidatePending, ['p'], ['ledger']],
  ['usePatchCategoryPattern', hooks.usePatchCategoryPattern, { patternId: 'p', update: {} }, ['store']],
  ['useDeleteCategoryPattern', hooks.useDeleteCategoryPattern, 'p', ['store']],
  ['useCreateCategoryPattern', hooks.useCreateCategoryPattern, {}, ['store']],
  ['usePatchGoal', hooks.usePatchGoal, { goalId: 'g', update: {} }, ['store', 'goals']],
  ['useDeleteGoal', hooks.useDeleteGoal, 'g', ['store', 'goals']],
  ['useCreateGoal', hooks.useCreateGoal, {}, ['store', 'goals']],
  ['useCreateGoalContribution', hooks.useCreateGoalContribution, {}, ['store', 'goals']],
  [
    'useUpdateGoalContribution',
    hooks.useUpdateGoalContribution,
    { contributionId: 'c', contribution: {} },
    ['store', 'goals'],
  ],
  ['useRemoveGoalContribution', hooks.useRemoveGoalContribution, 'c', ['store', 'goals']],
  ['useReorderContributionAutomations', hooks.useReorderContributionAutomations, ['a'], ['store', 'goals']],
  ['usePatchGoalAutomation', hooks.usePatchGoalAutomation, { automationId: 'a', update: {} }, ['store', 'goals']],
  ['useDeleteGoalAutomation', hooks.useDeleteGoalAutomation, 'a', ['store', 'goals']],
  ['useCreateContributionAutomation', hooks.useCreateContributionAutomation, {}, ['store', 'goals']],
  ['useCreateWithdrawalAutomation', hooks.useCreateWithdrawalAutomation, 'g', ['store', 'goals']],
  ['useReorderWithdrawalAutomations', hooks.useReorderWithdrawalAutomations, ['a'], ['store', 'goals']],
  ['useRunRecurringAdditions', hooks.useRunRecurringAdditions, undefined, ['store', 'goals']],
  ['useRunWithdrawalAutomation', hooks.useRunWithdrawalAutomation, undefined, ['store', 'goals']],
]

describe('accounting cache keys', () => {
  it('never invalidates the whole feature', () => {
    const everything = invalidatedPrefixes([
      'store',
      'ledger',
      'suggestions',
      'budgets',
      'netWorth',
      'goals',
      'llm',
      'sync',
    ])

    expect(everything.some((prefix) => prefix.length < 2)).toBe(false)
  })

  it('reports each prefix once however many families name it', () => {
    // `ledger` and `netWorth` both cover the net-worth queries.
    const prefixes = invalidatedPrefixes(['ledger', 'netWorth'])

    expect(prefixes.length).toBe(new Set(prefixes.map((prefix) => prefix.join('/'))).size)
  })
})

describe('what each mutation invalidates', () => {
  let queryClient: QueryClient

  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () => new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      ),
    )
    queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    // Seeded rather than fetched: a query with no observer is marked
    // invalidated without being refetched, which is exactly the signal under
    // test and keeps the stubbed `fetch` free for the mutations themselves.
    for (const probe of Object.values(PROBES)) queryClient.setQueryData(probe, {})
    // One probe needs a real shape rather than a placeholder, because a
    // mutation paints it optimistically and reads into it to do so.
    queryClient.setQueryData(keys.store, emptyStore())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }

  it.each(MUTATIONS)('%s', async (_name, useMutationHook, variables, families) => {
    const { result } = renderHook(() => useMutationHook(), { wrapper })

    await result.current.mutateAsync(variables as never)

    const expected = invalidatedPrefixes(families)
    const stale = Object.entries(PROBES)
      .filter(([, probe]) => queryClient.getQueryState(probe)?.isInvalidated)
      .map(([label]) => label)
      .sort()
    const wanted = Object.entries(PROBES)
      .filter(([, probe]) => coveredBy(probe, expected))
      .map(([label]) => label)
      .sort()

    expect(stale).toEqual(wanted)
  })

  // The queries no write can move. Under the retired `['accounting']` helper
  // every one of these was refetched on every mutation in the app.
  it.each([
    'currencies',
    'supportedImportKinds',
    'currentExchangeRate',
    'exchangeRateHistory',
    'simulatorProject',
  ])('leaves %s alone whatever is written', (label) => {
    const everything = invalidatedPrefixes([
      'store',
      'ledger',
      'suggestions',
      'budgets',
      'netWorth',
      'goals',
      'llm',
      'sync',
    ])

    expect(coveredBy(PROBES[label], everything)).toBe(false)
  })
})
