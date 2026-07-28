import { ApiError, StoreVersionConflictError } from '@/lib/api'
import type {
  Account,
  AccountingStore,
  Budget,
  BudgetComparisonRow,
  BudgetUpsert,
  CanonicalCategoryOverrides,
  CanonicalImportPreview,
  CanonicalImportResult,
  CategorizeFromFileApplyResult,
  CategorizeFromFilePreview,
  Category,
  CategoryClassification,
  CategoryPattern,
  CategoryPatternCreate,
  CategoryPatternUpdate,
  CategoryTotalRow,
  Currency,
  CurrencyCode,
  CurrentExchangeRate,
  DetectedAccount,
  DismissedSuggestion,
  DismissSuggestionRequest,
  DuplicateGroup,
  ExchangeRateHistoryPoint,
  GeneralBudget,
  GeneralBudgetUpsert,
  Goal,
  GoalContribution,
  GoalContributionCreate,
  GoalContributionUpdate,
  GoalCreate,
  GoalsSummary,
  GoalUpdate,
  ImportResult,
  InterestAccountRow,
  LlmSettings,
  LlmSettingsUpdate,
  LlmUsage,
  ManualOverride,
  ManualTransfer,
  MonthlyIncomeExpenseRow,
  NetWorthHistoryByAccountPoint,
  NetWorthHistoryPoint,
  NetWorthSummary,
  OpeningBalance,
  OtherAsset,
  OtherAssetCreate,
  PaystubReconciliationResult,
  Posting,
  PostingMerge,
  PostingMergeUpsert,
  PostingSplitLeg,
  ProjectionPoint,
  RecurringAddition,
  RecurringAdditionCreate,
  RecurringAdditionUpdate,
  SimulatorScenario,
  SimulatorScenarioCreate,
  SpendCurvePoint,
  SyncStatus,
  Tag,
  TransferLink,
  TransferLinkCreate,
  TransferRule,
  TransferRuleCreate,
  TransferRuleUpdate,
  TransferSuggestion,
  VerifyResult,
  WithdrawalPriorityEntry,
} from '@/types/accounting'

// The most recent `version` this module has seen out of any accounting
// response — updated below on every response that carries one (in
// practice only `GET /store`, including the refetch most mutations
// trigger via query invalidation), and sent back on every non-GET
// request so the backend can tell whether anything changed in between.
// Module-level rather than threaded through every one of ~30 mutation
// call sites individually, mirroring how the backend reads it off
// `session.info` in one place (`accounting.store.save_store`) instead of
// threading it through its own ~30 call sites.
let lastKnownStoreVersion: number | null = null

async function requestRaw<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const message = body?.detail ?? `${response.status} ${response.statusText}`
    if (response.status === 409) throw new StoreVersionConflictError(message)
    throw new ApiError(message)
  }
  return (await response.json()) as T
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET'
  const headers = new Headers(init?.headers)
  if (method !== 'GET' && lastKnownStoreVersion !== null) {
    headers.set('X-Expected-Store-Version', String(lastKnownStoreVersion))
  }
  const data = await requestRaw<T>(path, { ...init, headers })
  if (data && typeof data === 'object' && 'version' in data && typeof data.version === 'number') {
    lastKnownStoreVersion = data.version
  }
  return data
}

// For per-resource endpoints that must never touch the whole-store version
// cache — used across transfer rules, tags, other-assets, category patterns,
// simulator scenarios, goals, and recurring additions. Some carry their own
// row-scoped `version`; others (several deletes) do no version check at all.
// Routing any of these through `request` would both send the unrelated
// whole-store version as `X-Expected-Store-Version` (harmless; these backends
// never read it) and, more importantly, overwrite `lastKnownStoreVersion` with
// the *resource's own* version number, corrupting every other resource's own
// conflict check on its next save. A 409 here
// still throws the same `StoreVersionConflictError` `App.tsx`'s one
// global handler already knows how to show — it's a version conflict
// either way, just scoped to one row instead of the whole store.
const requestScoped = requestRaw

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export interface AccountCreate {
  name: string
  kind: string
  institution: string
  currency: string
  last_four?: string | null
  parent_account_id?: string | null
  external_ref?: string | null
  meta: Record<string, string>
}

export interface AccountUpdate {
  name: string
  institution: string
  kind: string
  currency: string
  last_four?: string | null
  external_ref?: string | null
  meta: Record<string, string>
}

export interface CategoryCreate {
  name: string
  classification: CategoryClassification
  color: string
}

export interface SubcategoryCreate {
  name: string
  color: string
}

export interface BudgetToDeletePreview {
  month: string | null
  amount: number
  currency: string
}

export interface CategoryRenamePreview {
  will_merge: boolean
  target_name: string | null
  budgets_to_delete: BudgetToDeletePreview[]
}

export interface CategoryDeletePreview {
  posting_count: number
}

export interface TagCreate {
  name: string
}

export interface TagRenamePreview {
  will_merge: boolean
  target_name: string | null
}

function queryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value))
  }
  const string = search.toString()
  return string ? `?${string}` : ''
}

export const accountingApi = {
  store: () => request<AccountingStore>('/api/accounting/store'),
  currencies: () => request<Currency[]>('/api/accounting/currencies'),
  llmUsage: () => request<LlmUsage>('/api/accounting/llm-usage'),
  llmSettings: () => request<LlmSettings>('/api/accounting/settings/llm'),
  setLlmSettings: (update: LlmSettingsUpdate) =>
    request<LlmSettings>('/api/accounting/settings/llm', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(update),
    }),
  clearLlmSettings: () => request<LlmSettings>('/api/accounting/settings/llm', { method: 'DELETE' }),
  verifyLlmSettings: (provider: 'gemini' | 'mistral') =>
    request<VerifyResult>(`/api/accounting/settings/llm/verify?provider=${provider}`, { method: 'POST' }),
  currentExchangeRate: (currency: string) =>
    request<CurrentExchangeRate>(`/api/accounting/exchange-rates/current${queryString({ currency })}`),
  exchangeRateHistory: (currency: string) =>
    request<ExchangeRateHistoryPoint[]>(`/api/accounting/exchange-rates/history${queryString({ currency })}`),
  putCategories: (categories: Record<string, Category>) =>
    request<Record<string, Category>>('/api/accounting/categories', jsonInit('PUT', categories)),
  createCategory: (category: CategoryCreate) =>
    request<Category>('/api/accounting/categories', jsonInit('POST', category)),
  createSubcategory: (parentId: string, subcategory: SubcategoryCreate) =>
    request<Category>(
      `/api/accounting/categories/${encodeURIComponent(parentId)}/subcategories`,
      jsonInit('POST', subcategory),
    ),
  categoryRenamePreview: (categoryId: string, name: string) =>
    request<CategoryRenamePreview>(
      `/api/accounting/categories/${encodeURIComponent(categoryId)}/rename-preview${queryString({ name })}`,
    ),
  renameCategory: (categoryId: string, name: string) =>
    request<{ categories: Record<string, Category>; merged: boolean }>(
      `/api/accounting/categories/${encodeURIComponent(categoryId)}/rename`,
      jsonInit('POST', { name }),
    ),
  categoryDeletePreview: (categoryId: string) =>
    request<CategoryDeletePreview>(`/api/accounting/categories/${encodeURIComponent(categoryId)}/delete-preview`),
  deleteCategory: (categoryId: string) =>
    request<{ categories: Record<string, Category>; uncategorized_posting_count: number }>(
      `/api/accounting/categories/${encodeURIComponent(categoryId)}`,
      { method: 'DELETE' },
    ),
  createTag: (tag: TagCreate) => request<Tag>('/api/accounting/tags', jsonInit('POST', tag)),
  deleteTag: (tagId: string) =>
    requestScoped<{ tag_id: string }>(`/api/accounting/tags/${encodeURIComponent(tagId)}`, { method: 'DELETE' }),
  tagRenamePreview: (tagId: string, name: string) =>
    request<TagRenamePreview>(
      `/api/accounting/tags/${encodeURIComponent(tagId)}/rename-preview${queryString({ name })}`,
    ),
  renameTag: (tagId: string, name: string) =>
    request<{ tags: Record<string, Tag>; merged: boolean }>(
      `/api/accounting/tags/${encodeURIComponent(tagId)}/rename`,
      jsonInit('POST', { name }),
    ),
  createTransferRule: (rule: TransferRuleCreate) =>
    requestScoped<TransferRule>('/api/accounting/transfer-rules', jsonInit('POST', rule)),
  patchTransferRule: (ruleId: string, update: TransferRuleUpdate) =>
    requestScoped<TransferRule>(
      `/api/accounting/transfer-rules/${encodeURIComponent(ruleId)}`,
      jsonInit('PATCH', update),
    ),
  deleteTransferRule: (ruleId: string) =>
    requestScoped<{ rule_id: string }>(`/api/accounting/transfer-rules/${encodeURIComponent(ruleId)}`, {
      method: 'DELETE',
    }),
  createOtherAsset: (asset: OtherAssetCreate) =>
    request<OtherAsset>('/api/accounting/other-assets', jsonInit('POST', asset)),
  deleteOtherAsset: (assetId: string) =>
    requestScoped<{ asset_id: string }>(`/api/accounting/other-assets/${encodeURIComponent(assetId)}`, {
      method: 'DELETE',
    }),
  postAccount: (account: AccountCreate) => request<Account>('/api/accounting/accounts', jsonInit('POST', account)),
  putAccount: (accountId: string, update: AccountUpdate) =>
    request<Account>(`/api/accounting/accounts/${encodeURIComponent(accountId)}`, jsonInit('PUT', update)),
  deleteAccount: (accountId: string) =>
    request<{ account_id: string }>(`/api/accounting/accounts/${encodeURIComponent(accountId)}`, { method: 'DELETE' }),
  putOpeningBalance: (accountId: string, openingBalance: OpeningBalance) =>
    request<OpeningBalance>(
      `/api/accounting/accounts/${encodeURIComponent(accountId)}/opening-balance`,
      jsonInit('PUT', openingBalance),
    ),
  deleteOpeningBalance: (accountId: string) =>
    request<{ account_id: string }>(`/api/accounting/accounts/${encodeURIComponent(accountId)}/opening-balance`, {
      method: 'DELETE',
    }),
  closeAccount: (accountId: string, transfers: ManualTransfer[]) =>
    request<{ account: Account; manual_transfers: ManualTransfer[] }>(
      `/api/accounting/accounts/${encodeURIComponent(accountId)}/close`,
      jsonInit('POST', { transfers }),
    ),
  reopenAccount: (accountId: string) =>
    request<Account>(`/api/accounting/accounts/${encodeURIComponent(accountId)}/reopen`, { method: 'POST' }),
  detect: (header: string[], filename: string, firstDataRow?: Record<string, string>) =>
    request<DetectedAccount | null>(
      '/api/accounting/detect',
      jsonInit('POST', { header, filename, first_data_row: firstDataRow }),
    ),
  supportedImportKinds: () =>
    request<{ institution: string; account_kind: string }[]>('/api/accounting/supported-import-kinds'),
  importCsv: (file: File, accountId: string) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('account_id', accountId)
    return request<ImportResult>('/api/accounting/import', { method: 'POST', body: formData })
  },
  previewCanonicalImport: (
    file: File,
    accountId: string,
    currency: CurrencyCode,
    separator?: string,
    dateOrder?: string,
  ) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('account_id', accountId)
    formData.append('currency', currency)
    if (separator) formData.append('separator', separator)
    if (dateOrder) formData.append('date_order', dateOrder)
    return request<CanonicalImportPreview>('/api/accounting/import/canonical/preview', {
      method: 'POST',
      body: formData,
    })
  },
  importCanonicalCsv: (
    file: File,
    accountId: string,
    separator?: string,
    dateOrder?: string,
    categoryOverrides?: CanonicalCategoryOverrides,
  ) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('account_id', accountId)
    if (separator) formData.append('separator', separator)
    if (dateOrder) formData.append('date_order', dateOrder)
    if (categoryOverrides) formData.append('category_overrides', JSON.stringify(categoryOverrides))
    return request<CanonicalImportResult>('/api/accounting/import/canonical', { method: 'POST', body: formData })
  },
  importPaystub: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return request<PaystubReconciliationResult>('/api/accounting/import/paystub', { method: 'POST', body: formData })
  },
  previewCategorizeFromFile: (file: File, separator?: string, dateOrder?: string) => {
    const formData = new FormData()
    formData.append('file', file)
    if (separator) formData.append('separator', separator)
    if (dateOrder) formData.append('date_order', dateOrder)
    return request<CategorizeFromFilePreview>('/api/accounting/import/categorize-from-file/preview', {
      method: 'POST',
      body: formData,
    })
  },
  applyCategorizeFromFile: (file: File, confirmedRowNumbers: number[], separator?: string, dateOrder?: string) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('confirmed_row_numbers', JSON.stringify(confirmedRowNumbers))
    if (separator) formData.append('separator', separator)
    if (dateOrder) formData.append('date_order', dateOrder)
    return request<CategorizeFromFileApplyResult>('/api/accounting/import/categorize-from-file/apply', {
      method: 'POST',
      body: formData,
    })
  },
  rebuild: () => request<{ total_posting_count: number }>('/api/accounting/rebuild', { method: 'POST' }),
  postings: () => request<Posting[]>('/api/accounting/postings'),
  ledgerExport: () => request<Posting[]>('/api/accounting/ledger/export'),
  // A request only ever carries the fields the caller means to change —
  // `put_posting_override` merges into whatever's already stored for
  // fields left out, so the request body is a genuine partial, unlike
  // the full `ManualOverride` this endpoint returns once merged.
  putPostingOverride: (postingId: string, override: Partial<ManualOverride>) =>
    request<ManualOverride>(
      `/api/accounting/postings/${encodeURIComponent(postingId)}/override`,
      jsonInit('PUT', override),
    ),
  putPostingSplit: (postingId: string, legs: PostingSplitLeg[]) =>
    request<{ posting_id: string; legs: PostingSplitLeg[] }>(
      `/api/accounting/postings/${encodeURIComponent(postingId)}/split`,
      jsonInit('PUT', legs),
    ),
  deletePostingSplit: (postingId: string) =>
    request<{ posting_id: string }>(`/api/accounting/postings/${encodeURIComponent(postingId)}/split`, {
      method: 'DELETE',
    }),
  aiSuggestCategory: (postingId: string, lockCategoryId?: string | null) =>
    request<{ category_id: string | null; subcategory_id: string | null; applied: boolean }>(
      `/api/accounting/postings/${encodeURIComponent(postingId)}/ai-suggest-category${queryString({ lock_category_id: lockCategoryId ?? undefined })}`,
      { method: 'POST' },
    ),
  patternSuggestCategory: (postingId: string, lockCategoryId?: string | null) =>
    request<{ category_id: string | null; subcategory_id: string | null; applied: boolean }>(
      `/api/accounting/postings/${encodeURIComponent(postingId)}/pattern-suggest-category${queryString({ lock_category_id: lockCategoryId ?? undefined })}`,
      { method: 'POST' },
    ),
  patternSuggestCategoryBulk: (postingIds: string[]) =>
    request<{ applied: number }>(
      '/api/accounting/postings/pattern-suggest-category/bulk',
      jsonInit('POST', { posting_ids: postingIds }),
    ),
  validatePending: (postingIds: string[]) =>
    request<{ accepted: number; reverted: number }>(
      '/api/accounting/postings/validate-pending',
      jsonInit('POST', { posting_ids: postingIds }),
    ),
  createCategoryPattern: (pattern: CategoryPatternCreate) =>
    requestScoped<CategoryPattern>('/api/accounting/category-patterns', jsonInit('POST', pattern)),
  patchCategoryPattern: (patternId: string, update: CategoryPatternUpdate) =>
    requestScoped<CategoryPattern>(
      `/api/accounting/category-patterns/${encodeURIComponent(patternId)}`,
      jsonInit('PATCH', update),
    ),
  deleteCategoryPattern: (patternId: string) =>
    requestScoped<{ pattern_id: string }>(`/api/accounting/category-patterns/${encodeURIComponent(patternId)}`, {
      method: 'DELETE',
    }),
  transferSuggestions: (windowDays?: number) =>
    request<TransferSuggestion[]>(`/api/accounting/transfer-suggestions${queryString({ window_days: windowDays })}`),
  duplicateSuggestions: (windowDays?: number) =>
    request<DuplicateGroup[]>(`/api/accounting/duplicate-suggestions${queryString({ window_days: windowDays })}`),
  dismissedSuggestions: () => request<DismissedSuggestion[]>('/api/accounting/dismissed-suggestions'),
  dismissSuggestion: (body: DismissSuggestionRequest) =>
    request<DismissedSuggestion>('/api/accounting/dismissed-suggestions', jsonInit('POST', body)),
  restoreSuggestion: (suggestionId: string) =>
    request<{ suggestion_id: string }>(`/api/accounting/dismissed-suggestions/${encodeURIComponent(suggestionId)}`, {
      method: 'DELETE',
    }),
  createPostingMerge: (merge: PostingMergeUpsert) =>
    request<PostingMerge>('/api/accounting/posting-merges', jsonInit('POST', merge)),
  removePostingMerge: (mergeId: string) =>
    request<{ merge_id: string }>(`/api/accounting/posting-merges/${encodeURIComponent(mergeId)}`, {
      method: 'DELETE',
    }),
  createTransferLink: (link: TransferLinkCreate) =>
    request<TransferLink>('/api/accounting/transfer-links', jsonInit('POST', link)),
  removeTransferLink: (linkId: string) =>
    request<{ link_id: string }>(`/api/accounting/transfer-links/${encodeURIComponent(linkId)}`, {
      method: 'DELETE',
    }),
  netWorth: (asOf?: string, displayCurrency?: string) =>
    request<NetWorthSummary>(
      `/api/accounting/net-worth${queryString({ as_of: asOf, display_currency: displayCurrency })}`,
    ),
  netWorthHistory: (start: string, end: string, intervalDays?: number, displayCurrency?: string) =>
    request<NetWorthHistoryPoint[]>(
      `/api/accounting/net-worth/history${queryString({ start, end, interval_days: intervalDays, display_currency: displayCurrency })}`,
    ),
  netWorthHistoryByAccount: (start: string, end: string, intervalDays?: number, displayCurrency?: string) =>
    request<NetWorthHistoryByAccountPoint[]>(
      `/api/accounting/net-worth/history/by-account${queryString({ start, end, interval_days: intervalDays, display_currency: displayCurrency })}`,
    ),
  categoryTotals: (start: string, end: string, accountIds?: string[], tagId?: string, displayCurrency?: string) =>
    request<CategoryTotalRow[]>(
      `/api/accounting/income-statement/category-totals${queryString({ start, end, account_ids: accountIds?.join(','), tag_id: tagId, display_currency: displayCurrency })}`,
    ),
  monthlyIncomeExpense: (start: string, end: string, displayCurrency?: string) =>
    request<MonthlyIncomeExpenseRow[]>(
      `/api/accounting/income-statement/monthly${queryString({ start, end, display_currency: displayCurrency })}`,
    ),
  spendCurve: (month: string, lookbackMonths?: number, displayCurrency?: string) =>
    request<SpendCurvePoint[]>(
      `/api/accounting/income-statement/spend-curve${queryString({ month, lookback_months: lookbackMonths, display_currency: displayCurrency })}`,
    ),
  setBudget: (budget: BudgetUpsert) => request<Budget>('/api/accounting/budgets', jsonInit('POST', budget)),
  removeBudget: (budgetId: string) =>
    request<{ budget_id: string }>(`/api/accounting/budgets/${encodeURIComponent(budgetId)}`, { method: 'DELETE' }),
  setGeneralBudget: (generalBudget: GeneralBudgetUpsert) =>
    request<GeneralBudget>('/api/accounting/general-budgets', jsonInit('POST', generalBudget)),
  removeGeneralBudget: (key: string) =>
    request<{ key: string }>(`/api/accounting/general-budgets/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  budgetComparison: (month: string, displayCurrency?: string) =>
    request<BudgetComparisonRow[]>(
      `/api/accounting/budgets/comparison${queryString({ month, display_currency: displayCurrency })}`,
    ),
  suggestedBudgetAmount: (
    categoryId: string,
    month: string,
    lookbackMonths?: number,
    subcategoryId?: string,
    displayCurrency?: string,
  ) =>
    request<{ suggested_amount: number }>(
      `/api/accounting/budgets/suggested-amount${queryString({ category_id: categoryId, month, lookback_months: lookbackMonths, subcategory_id: subcategoryId, display_currency: displayCurrency })}`,
    ),
  interestSummary: (asOf?: string) =>
    request<InterestAccountRow[]>(`/api/accounting/interest-summary${queryString({ as_of: asOf })}`),
  createSimulatorScenario: (scenario: SimulatorScenarioCreate) =>
    request<SimulatorScenario>('/api/accounting/simulator/scenarios', jsonInit('POST', scenario)),
  deleteSimulatorScenario: (scenarioId: string) =>
    requestScoped<{ scenario_id: string }>(`/api/accounting/simulator/scenarios/${encodeURIComponent(scenarioId)}`, {
      method: 'DELETE',
    }),
  simulatorProject: (
    initialCapital: number,
    monthlyContribution: number,
    horizonYears: number,
    annualRatePct: number,
    compoundingFrequency: SimulatorScenario['compounding_frequency'],
  ) =>
    request<ProjectionPoint[]>(
      `/api/accounting/simulator/project${queryString({
        initial_capital: initialCapital,
        monthly_contribution: monthlyContribution,
        horizon_years: horizonYears,
        annual_rate_pct: annualRatePct,
        compounding_frequency: compoundingFrequency,
      })}`,
    ),
  createGoal: (goal: GoalCreate) => requestScoped<Goal>('/api/accounting/goals', jsonInit('POST', goal)),
  patchGoal: (goalId: string, update: GoalUpdate) =>
    requestScoped<Goal>(`/api/accounting/goals/${encodeURIComponent(goalId)}`, jsonInit('PATCH', update)),
  deleteGoal: (goalId: string) =>
    requestScoped<{ goal_id: string }>(`/api/accounting/goals/${encodeURIComponent(goalId)}`, { method: 'DELETE' }),
  createGoalContribution: (contribution: GoalContributionCreate) =>
    request<GoalContribution>('/api/accounting/goal-contributions', jsonInit('POST', contribution)),
  updateGoalContribution: (contributionId: string, contribution: GoalContributionUpdate) =>
    request<GoalContribution>(
      `/api/accounting/goal-contributions/${encodeURIComponent(contributionId)}`,
      jsonInit('PUT', contribution),
    ),
  removeGoalContribution: (contributionId: string) =>
    request<{ contribution_id: string }>(`/api/accounting/goal-contributions/${encodeURIComponent(contributionId)}`, {
      method: 'DELETE',
    }),
  putRecurringAdditions: (additions: RecurringAddition[]) =>
    request<RecurringAddition[]>('/api/accounting/recurring-additions', jsonInit('PUT', additions)),
  createRecurringAddition: (addition: RecurringAdditionCreate) =>
    request<RecurringAddition>('/api/accounting/recurring-additions', jsonInit('POST', addition)),
  patchRecurringAddition: (additionId: string, update: RecurringAdditionUpdate) =>
    requestScoped<RecurringAddition>(
      `/api/accounting/recurring-additions/${encodeURIComponent(additionId)}`,
      jsonInit('PATCH', update),
    ),
  deleteRecurringAddition: (additionId: string) =>
    requestScoped<{ addition_id: string }>(`/api/accounting/recurring-additions/${encodeURIComponent(additionId)}`, {
      method: 'DELETE',
    }),
  putWithdrawalPriorities: (priorities: WithdrawalPriorityEntry[]) =>
    request<WithdrawalPriorityEntry[]>('/api/accounting/withdrawal-priorities', jsonInit('PUT', priorities)),
  syncStatus: () => request<SyncStatus>('/api/accounting/sync-status'),
  goalsSummary: (asOf?: string, displayCurrency?: string) =>
    request<GoalsSummary>(
      `/api/accounting/goals/summary${queryString({ as_of: asOf, display_currency: displayCurrency })}`,
    ),
  runRecurringAdditions: (asOf?: string) =>
    request<GoalContribution[]>(`/api/accounting/goals/run-recurring-additions${queryString({ as_of: asOf })}`, {
      method: 'POST',
    }),
  runWithdrawalAutomation: (asOf?: string) =>
    request<{ withdrawals: GoalContribution[]; remaining_shortfall: number }>(
      `/api/accounting/goals/run-withdrawal-automation${queryString({ as_of: asOf })}`,
      { method: 'POST' },
    ),
  simulateContribution: (goalId: string, date: string, amount: number) =>
    request<{
      unallocated_as_of_date: number
      exceeds_unallocated: boolean
      projected_next_run_unallocated: number
      would_go_negative: boolean
    }>('/api/accounting/goals/simulate-contribution', jsonInit('POST', { goal_id: goalId, date, amount })),
}
