import { ApiError, parseBody, RowVersionConflictError } from '@/lib/api'
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
  Goal,
  GoalAutomation,
  GoalAutomationCreate,
  GoalAutomationUpdate,
  GoalContribution,
  GoalContributionCreate,
  GoalContributionUpdate,
  GoalCreate,
  GoalsSummary,
  GoalUpdate,
  ImportResult,
  InterestAccountRow,
  LedgerExportPage,
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
  PostingPage,
  PostingSplitLeg,
  ProjectionPoint,
  RawPosting,
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
} from '@/types/accounting'

// Where this module's endpoints live, declared once rather than repeated in
// every path below — the mirror image of `accounting.api.api`'s own router
// prefix, so the two move together. See `api.ts`'s `TRADES_API_BASE` for the
// shape; exported for the same reason (a browser-driven CSV download needs the
// URL, not a parsed response).
export const ACCOUNTING_API_BASE = '/api/v1/accounting'

// One request path for every accounting endpoint. Nothing store-wide is
// sent or cached here: a write that needs conflict detection carries its
// own row's `expected_version` in the request body (goals, transfer
// rules, category patterns), and every other write is either scoped to
// the rows it names or deliberately last-write-wins. A 409 therefore
// only ever means *that one row* moved, which is what
// `RowVersionConflictError` says and what App.tsx's one global handler
// turns into a reload prompt.
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${ACCOUNTING_API_BASE}${path}`, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const message = body?.detail ?? `${response.status} ${response.statusText}`
    if (response.status === 409) throw new RowVersionConflictError(message)
    throw new ApiError(message)
  }
  return (await parseBody(response)) as T
}

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
  broker_connection_id?: string | null
  meta: Record<string, string>
}

export interface AccountUpdate {
  name: string
  institution: string
  kind: string
  currency: string
  last_four?: string | null
  broker_connection_id?: string | null
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

const POSTINGS_PAGE_LIMIT = 5000
/** The server's own hard cap (`api_models.PAGE_LIMIT_MAX`) — the fewest round trips it will allow. */

/**
 * How far a paging loop may advance, given the page size the server applied.
 *
 * Guards the one input that could hang the tab: a stride of zero would make
 * the loop re-request the same offset forever. The server validates
 * `limit >= 1`, so this should be unreachable — which is exactly why it
 * should fail loudly rather than spin.
 */
function pageStride(appliedLimit: number): number {
  if (!Number.isInteger(appliedLimit) || appliedLimit < 1) {
    throw new Error(`Server returned an unusable page limit: ${appliedLimit}`)
  }
  return appliedLimit
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
  store: () => request<AccountingStore>('/store'),
  currencies: () => request<Currency[]>('/currencies'),
  llmUsage: () => request<LlmUsage>('/llm-usage'),
  llmSettings: () => request<LlmSettings>('/settings/llm'),
  setLlmSettings: (update: LlmSettingsUpdate) =>
    request<LlmSettings>('/settings/llm', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(update),
    }),
  clearLlmSettings: () => request<LlmSettings>('/settings/llm', { method: 'DELETE' }),
  verifyLlmSettings: (provider: 'gemini' | 'mistral') =>
    request<VerifyResult>(`/settings/llm/verify?provider=${provider}`, { method: 'POST' }),
  currentExchangeRate: (currency: string) =>
    request<CurrentExchangeRate>(`/exchange-rates/current${queryString({ currency })}`),
  exchangeRateHistory: (currency: string) =>
    request<ExchangeRateHistoryPoint[]>(`/exchange-rates/history${queryString({ currency })}`),
  createCategory: (category: CategoryCreate) => request<Category>('/categories', jsonInit('POST', category)),
  createSubcategory: (parentId: string, subcategory: SubcategoryCreate) =>
    request<Category>(`/categories/${encodeURIComponent(parentId)}/subcategories`, jsonInit('POST', subcategory)),
  categoryRenamePreview: (categoryId: string, name: string) =>
    request<CategoryRenamePreview>(
      `/categories/${encodeURIComponent(categoryId)}/rename-preview${queryString({ name })}`,
    ),
  renameCategory: (categoryId: string, name: string) =>
    request<{ categories: Record<string, Category>; merged: boolean }>(
      `/categories/${encodeURIComponent(categoryId)}/rename`,
      jsonInit('POST', { name }),
    ),
  categoryDeletePreview: (categoryId: string) =>
    request<CategoryDeletePreview>(`/categories/${encodeURIComponent(categoryId)}/delete-preview`),
  deleteCategory: (categoryId: string) =>
    request<{ categories: Record<string, Category>; uncategorized_posting_count: number }>(
      `/categories/${encodeURIComponent(categoryId)}`,
      { method: 'DELETE' },
    ),
  createTag: (tag: TagCreate) => request<Tag>('/tags', jsonInit('POST', tag)),
  deleteTag: (tagId: string) => request<void>(`/tags/${encodeURIComponent(tagId)}`, { method: 'DELETE' }),
  tagRenamePreview: (tagId: string, name: string) =>
    request<TagRenamePreview>(`/tags/${encodeURIComponent(tagId)}/rename-preview${queryString({ name })}`),
  renameTag: (tagId: string, name: string) =>
    request<{ tags: Record<string, Tag>; merged: boolean }>(
      `/tags/${encodeURIComponent(tagId)}/rename`,
      jsonInit('POST', { name }),
    ),
  createTransferRule: (rule: TransferRuleCreate) => request<TransferRule>('/transfer-rules', jsonInit('POST', rule)),
  patchTransferRule: (ruleId: string, update: TransferRuleUpdate) =>
    request<TransferRule>(`/transfer-rules/${encodeURIComponent(ruleId)}`, jsonInit('PATCH', update)),
  deleteTransferRule: (ruleId: string) =>
    request<void>(`/transfer-rules/${encodeURIComponent(ruleId)}`, {
      method: 'DELETE',
    }),
  createOtherAsset: (asset: OtherAssetCreate) => request<OtherAsset>('/other-assets', jsonInit('POST', asset)),
  deleteOtherAsset: (assetId: string) =>
    request<void>(`/other-assets/${encodeURIComponent(assetId)}`, {
      method: 'DELETE',
    }),
  postAccount: (account: AccountCreate) => request<Account>('/accounts', jsonInit('POST', account)),
  putAccount: (accountId: string, update: AccountUpdate) =>
    request<Account>(`/accounts/${encodeURIComponent(accountId)}`, jsonInit('PUT', update)),
  deleteAccount: (accountId: string) =>
    request<void>(`/accounts/${encodeURIComponent(accountId)}`, { method: 'DELETE' }),
  putOpeningBalance: (accountId: string, openingBalance: OpeningBalance) =>
    request<OpeningBalance>(
      `/accounts/${encodeURIComponent(accountId)}/opening-balance`,
      jsonInit('PUT', openingBalance),
    ),
  deleteOpeningBalance: (accountId: string) =>
    request<void>(`/accounts/${encodeURIComponent(accountId)}/opening-balance`, {
      method: 'DELETE',
    }),
  closeAccount: (accountId: string, transfers: ManualTransfer[]) =>
    request<{ account: Account; manual_transfers: ManualTransfer[] }>(
      `/accounts/${encodeURIComponent(accountId)}/close`,
      jsonInit('POST', { transfers }),
    ),
  reopenAccount: (accountId: string) =>
    request<Account>(`/accounts/${encodeURIComponent(accountId)}/reopen`, { method: 'POST' }),
  detect: (header: string[], filename: string, firstDataRow?: Record<string, string>) =>
    request<DetectedAccount | null>('/detect', jsonInit('POST', { header, filename, first_data_row: firstDataRow })),
  supportedImportKinds: () => request<{ institution: string; account_kind: string }[]>('/supported-import-kinds'),
  importCsv: (file: File, accountId: string) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('account_id', accountId)
    return request<ImportResult>('/import', { method: 'POST', body: formData })
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
    return request<CanonicalImportPreview>('/import/canonical/preview', {
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
    return request<CanonicalImportResult>('/import/canonical', { method: 'POST', body: formData })
  },
  importPaystub: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return request<PaystubReconciliationResult>('/import/paystub', { method: 'POST', body: formData })
  },
  previewCategorizeFromFile: (file: File, separator?: string, dateOrder?: string) => {
    const formData = new FormData()
    formData.append('file', file)
    if (separator) formData.append('separator', separator)
    if (dateOrder) formData.append('date_order', dateOrder)
    return request<CategorizeFromFilePreview>('/import/categorize-from-file/preview', {
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
    return request<CategorizeFromFileApplyResult>('/import/categorize-from-file/apply', {
      method: 'POST',
      body: formData,
    })
  },
  rebuild: () => request<{ total_posting_count: number }>('/rebuild', { method: 'POST' }),
  // Pages through the collection until it is exhausted, rather than asking
  // for one page. The server caps any single page (`PAGE_LIMIT_MAX`), so a
  // single request cannot return a large user's whole ledger — and returning
  // a silently truncated ledger is not an option for a money app. PR 4
  // replaces this loop with real pagination in the Transactions table; until
  // then every consumer still receives the complete list it expects.
  //
  // `total` counts *transactions* and `limit` is in transactions too, so the
  // loop advances by the page's own size and stops once `offset` covers
  // `total`. It advances by `page.limit`, the size the server actually
  // applied after clamping, never by the size we asked for: if this
  // constant is ever above the server's cap — mid-deploy, say — advancing
  // by the request would step past records the server never sent and
  // truncate the ledger silently, which is the exact failure paging exists
  // to avoid.
  postings: async () => {
    const limit = POSTINGS_PAGE_LIMIT
    const items: Posting[] = []
    let offset = 0
    let total = 0
    do {
      const page = await request<PostingPage>(`/postings${queryString({ limit, offset })}`)
      items.push(...page.items)
      total = page.total
      offset += pageStride(page.limit)
    } while (offset < total)
    return items
  },
  // Same paging loop as `postings` above, and for the same reason — an
  // export that silently stopped at the cap would write a partial backup to
  // a file the user believes is complete. `total` and `limit` count postings
  // here, not transactions.
  ledgerExport: async () => {
    const limit = POSTINGS_PAGE_LIMIT
    const items: RawPosting[] = []
    let offset = 0
    let total = 0
    do {
      const page = await request<LedgerExportPage>(`/ledger/export${queryString({ limit, offset })}`)
      items.push(...page.items)
      total = page.total
      offset += pageStride(page.limit)
    } while (offset < total)
    return items
  },
  // A request only ever carries the fields the caller means to change —
  // `put_posting_override` merges into whatever's already stored for
  // fields left out, so the request body is a genuine partial, unlike
  // the full `ManualOverride` this endpoint returns once merged.
  putPostingOverride: (postingId: string, override: Partial<ManualOverride>) =>
    request<ManualOverride>(`/postings/${encodeURIComponent(postingId)}/override`, jsonInit('PUT', override)),
  putPostingSplit: (postingId: string, legs: PostingSplitLeg[]) =>
    request<{ posting_id: string; legs: PostingSplitLeg[] }>(
      `/postings/${encodeURIComponent(postingId)}/split`,
      jsonInit('PUT', legs),
    ),
  deletePostingSplit: (postingId: string) =>
    request<void>(`/postings/${encodeURIComponent(postingId)}/split`, {
      method: 'DELETE',
    }),
  aiSuggestCategory: (postingId: string, lockCategoryId?: string | null) =>
    request<{ category_id: string | null; subcategory_id: string | null; applied: boolean }>(
      `/postings/${encodeURIComponent(postingId)}/ai-suggest-category${queryString({ lock_category_id: lockCategoryId ?? undefined })}`,
      { method: 'POST' },
    ),
  patternSuggestCategory: (postingId: string, lockCategoryId?: string | null) =>
    request<{ category_id: string | null; subcategory_id: string | null; applied: boolean }>(
      `/postings/${encodeURIComponent(postingId)}/pattern-suggest-category${queryString({ lock_category_id: lockCategoryId ?? undefined })}`,
      { method: 'POST' },
    ),
  patternSuggestCategoryBulk: (postingIds: string[]) =>
    request<{ applied: number }>(
      '/postings/pattern-suggest-category/bulk',
      jsonInit('POST', { posting_ids: postingIds }),
    ),
  validatePending: (postingIds: string[]) =>
    request<{ accepted: number; reverted: number }>(
      '/postings/validate-pending',
      jsonInit('POST', { posting_ids: postingIds }),
    ),
  createCategoryPattern: (pattern: CategoryPatternCreate) =>
    request<CategoryPattern>('/category-patterns', jsonInit('POST', pattern)),
  patchCategoryPattern: (patternId: string, update: CategoryPatternUpdate) =>
    request<CategoryPattern>(`/category-patterns/${encodeURIComponent(patternId)}`, jsonInit('PATCH', update)),
  deleteCategoryPattern: (patternId: string) =>
    request<void>(`/category-patterns/${encodeURIComponent(patternId)}`, {
      method: 'DELETE',
    }),
  transferSuggestions: (windowDays?: number) =>
    request<TransferSuggestion[]>(`/transfer-suggestions${queryString({ window_days: windowDays })}`),
  duplicateSuggestions: (windowDays?: number) =>
    request<DuplicateGroup[]>(`/duplicate-suggestions${queryString({ window_days: windowDays })}`),
  dismissedSuggestions: () => request<DismissedSuggestion[]>('/dismissed-suggestions'),
  // A `PUT` at the suggestion's own id, not a `POST` to the collection: the
  // archive entry is keyed by the id this caller already holds, so dismissing
  // the same suggestion twice is one idempotent write to one address rather
  // than two submissions the server has to reconcile.
  dismissSuggestion: (suggestionId: string, body: DismissSuggestionRequest) =>
    request<DismissedSuggestion>(`/dismissed-suggestions/${encodeURIComponent(suggestionId)}`, jsonInit('PUT', body)),
  restoreSuggestion: (suggestionId: string) =>
    request<void>(`/dismissed-suggestions/${encodeURIComponent(suggestionId)}`, {
      method: 'DELETE',
    }),
  createPostingMerge: (merge: PostingMergeUpsert) => request<PostingMerge>('/posting-merges', jsonInit('POST', merge)),
  removePostingMerge: (mergeId: string) =>
    request<void>(`/posting-merges/${encodeURIComponent(mergeId)}`, {
      method: 'DELETE',
    }),
  createTransferLink: (link: TransferLinkCreate) => request<TransferLink>('/transfer-links', jsonInit('POST', link)),
  removeTransferLink: (linkId: string) =>
    request<void>(`/transfer-links/${encodeURIComponent(linkId)}`, {
      method: 'DELETE',
    }),
  netWorth: (asOf?: string, displayCurrency?: string) =>
    request<NetWorthSummary>(`/net-worth${queryString({ as_of: asOf, display_currency: displayCurrency })}`),
  netWorthHistory: (start: string, end: string, intervalDays?: number, displayCurrency?: string) =>
    request<NetWorthHistoryPoint[]>(
      `/net-worth/history${queryString({ start, end, interval_days: intervalDays, display_currency: displayCurrency })}`,
    ),
  netWorthHistoryByAccount: (start: string, end: string, intervalDays?: number, displayCurrency?: string) =>
    request<NetWorthHistoryByAccountPoint[]>(
      `/net-worth/history/by-account${queryString({ start, end, interval_days: intervalDays, display_currency: displayCurrency })}`,
    ),
  categoryTotals: (start: string, end: string, accountIds?: string[], tagId?: string, displayCurrency?: string) =>
    request<CategoryTotalRow[]>(
      `/income-statement/category-totals${queryString({ start, end, account_ids: accountIds?.join(','), tag_id: tagId, display_currency: displayCurrency })}`,
    ),
  monthlyIncomeExpense: (start: string, end: string, displayCurrency?: string) =>
    request<MonthlyIncomeExpenseRow[]>(
      `/income-statement/monthly${queryString({ start, end, display_currency: displayCurrency })}`,
    ),
  spendCurve: (month: string, lookbackMonths?: number, displayCurrency?: string) =>
    request<SpendCurvePoint[]>(
      `/income-statement/spend-curve${queryString({ month, lookback_months: lookbackMonths, display_currency: displayCurrency })}`,
    ),
  setBudget: (budget: BudgetUpsert) => request<Budget>('/budgets', jsonInit('POST', budget)),
  removeBudget: (budgetId: string) => request<void>(`/budgets/${encodeURIComponent(budgetId)}`, { method: 'DELETE' }),
  budgetComparison: (month: string, displayCurrency?: string) =>
    request<BudgetComparisonRow[]>(`/budgets/comparison${queryString({ month, display_currency: displayCurrency })}`),
  suggestedBudgetAmount: (
    categoryId: string,
    month: string,
    lookbackMonths?: number,
    subcategoryId?: string,
    displayCurrency?: string,
  ) =>
    request<{ suggested_amount: number }>(
      `/budgets/suggested-amount${queryString({ category_id: categoryId, month, lookback_months: lookbackMonths, subcategory_id: subcategoryId, display_currency: displayCurrency })}`,
    ),
  interestSummary: (asOf?: string) => request<InterestAccountRow[]>(`/interest-summary${queryString({ as_of: asOf })}`),
  createSimulatorScenario: (scenario: SimulatorScenarioCreate) =>
    request<SimulatorScenario>('/simulator/scenarios', jsonInit('POST', scenario)),
  deleteSimulatorScenario: (scenarioId: string) =>
    request<void>(`/simulator/scenarios/${encodeURIComponent(scenarioId)}`, {
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
      `/simulator/project${queryString({
        initial_capital: initialCapital,
        monthly_contribution: monthlyContribution,
        horizon_years: horizonYears,
        annual_rate_pct: annualRatePct,
        compounding_frequency: compoundingFrequency,
      })}`,
    ),
  createGoal: (goal: GoalCreate) => request<Goal>('/goals', jsonInit('POST', goal)),
  patchGoal: (goalId: string, update: GoalUpdate) =>
    request<Goal>(`/goals/${encodeURIComponent(goalId)}`, jsonInit('PATCH', update)),
  deleteGoal: (goalId: string) => request<void>(`/goals/${encodeURIComponent(goalId)}`, { method: 'DELETE' }),
  createGoalContribution: (contribution: GoalContributionCreate) =>
    request<GoalContribution>('/goal-contributions', jsonInit('POST', contribution)),
  updateGoalContribution: (contributionId: string, contribution: GoalContributionUpdate) =>
    request<GoalContribution>(
      `/goal-contributions/${encodeURIComponent(contributionId)}`,
      jsonInit('PUT', contribution),
    ),
  removeGoalContribution: (contributionId: string) =>
    request<void>(`/goal-contributions/${encodeURIComponent(contributionId)}`, {
      method: 'DELETE',
    }),
  // Ids only, in the wanted order — the server derives every `priority` from
  // list position, and refuses an id set that isn't exactly what it already
  // stores, so a reorder cannot carry a field edit, an insert or a delete.
  reorderContributionAutomations: (automationIds: string[]) =>
    request<GoalAutomation[]>(
      '/goal-automations/contributions/order',
      jsonInit('PUT', { automation_ids: automationIds }),
    ),
  createContributionAutomation: (automation: GoalAutomationCreate) =>
    request<GoalAutomation>('/goal-automations/contributions', jsonInit('POST', automation)),
  patchGoalAutomation: (automationId: string, update: GoalAutomationUpdate) =>
    request<GoalAutomation>(`/goal-automations/${encodeURIComponent(automationId)}`, jsonInit('PATCH', update)),
  deleteGoalAutomation: (automationId: string) =>
    request<void>(`/goal-automations/${encodeURIComponent(automationId)}`, {
      method: 'DELETE',
    }),
  // Adding a goal to the drawdown order and rearranging it are two different
  // requests now: the create is what makes a goal a member of the order, and
  // the reorder may only permute the members it already has.
  createWithdrawalAutomation: (goalId: string) =>
    request<GoalAutomation>('/goal-automations/withdrawals', jsonInit('POST', { goal_id: goalId })),
  reorderWithdrawalAutomations: (automationIds: string[]) =>
    request<GoalAutomation[]>(
      '/goal-automations/withdrawals/order',
      jsonInit('PUT', { automation_ids: automationIds }),
    ),
  syncStatus: () => request<SyncStatus>('/sync-status'),
  goalsSummary: (asOf?: string, displayCurrency?: string) =>
    request<GoalsSummary>(`/goals/summary${queryString({ as_of: asOf, display_currency: displayCurrency })}`),
  runRecurringAdditions: (asOf?: string) =>
    request<GoalContribution[]>(`/goals/run-recurring-additions${queryString({ as_of: asOf })}`, {
      method: 'POST',
    }),
  runWithdrawalAutomation: (asOf?: string) =>
    request<{ withdrawals: GoalContribution[]; remaining_shortfall: number }>(
      `/goals/run-withdrawal-automation${queryString({ as_of: asOf })}`,
      { method: 'POST' },
    ),
  simulateContribution: (goalId: string, date: string, amount: number) =>
    request<{
      unallocated_as_of_date: number
      exceeds_unallocated: boolean
      projected_next_run_unallocated: number
      would_go_negative: boolean
    }>('/goals/simulate-contribution', jsonInit('POST', { goal_id: goalId, date, amount })),
}
