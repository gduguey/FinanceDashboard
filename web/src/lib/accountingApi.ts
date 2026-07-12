import { ApiError } from '@/lib/api'
import type {
  Account,
  AccountingStore,
  Budget,
  GeneralBudget,
  BudgetComparisonRow,
  Category,
  CategoryTotalRow,
  CurrentExchangeRate,
  Currency,
  DetectedAccount,
  ExchangeRateHistoryPoint,
  ExchangeRateSyncResult,
  ImportResult,
  InterestAccountRow,
  ManualOverride,
  MonthlyIncomeExpenseRow,
  NetWorthHistoryByAccountPoint,
  NetWorthHistoryPoint,
  NetWorthSummary,
  OpeningBalance,
  OtherAsset,
  PaystubReconciliationResult,
  Posting,
  PostingSplitLeg,
  ProjectionPoint,
  Rule,
  SimulatorScenario,
  SofiStatementImportResult,
  SpendCurvePoint,
  Tag,
  TransferSuggestion,
} from '@/types/accounting'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(body?.detail ?? `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export interface ImportAccountInfo {
  institution: string
  account_kind: string
  account_id: string
  account_name: string
  currency?: string
  parent_account_id?: string | null
}

export interface AccountUpdate {
  name: string
  institution: string
  kind: string
  currency: string
  meta: Record<string, string>
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
  syncExchangeRates: () => request<ExchangeRateSyncResult>('/api/accounting/sync-exchange-rates', { method: 'POST' }),
  currentExchangeRate: (currency: string) =>
    request<CurrentExchangeRate>(`/api/accounting/exchange-rates/current${queryString({ currency })}`),
  exchangeRateHistory: (currency: string) =>
    request<ExchangeRateHistoryPoint[]>(`/api/accounting/exchange-rates/history${queryString({ currency })}`),
  putCategories: (categories: Record<string, Category>) =>
    request<Record<string, Category>>('/api/accounting/categories', jsonInit('PUT', categories)),
  putTags: (tags: Record<string, Tag>) => request<Record<string, Tag>>('/api/accounting/tags', jsonInit('PUT', tags)),
  putRules: (rules: Rule[]) => request<Rule[]>('/api/accounting/rules', jsonInit('PUT', rules)),
  putOtherAssets: (otherAssets: OtherAsset[]) =>
    request<OtherAsset[]>('/api/accounting/other-assets', jsonInit('PUT', otherAssets)),
  postAccount: (account: Account) => request<Account>('/api/accounting/accounts', jsonInit('POST', account)),
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
  detect: (header: string[], filename: string, firstDataRow?: Record<string, string>) =>
    request<DetectedAccount | null>(
      '/api/accounting/detect',
      jsonInit('POST', { header, filename, first_data_row: firstDataRow }),
    ),
  supportedImportKinds: () =>
    request<{ institution: string; account_kind: string }[]>('/api/accounting/supported-import-kinds'),
  importCsv: (file: File, info: ImportAccountInfo) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('institution', info.institution)
    formData.append('account_kind', info.account_kind)
    formData.append('account_id', info.account_id)
    formData.append('account_name', info.account_name)
    if (info.currency) formData.append('currency', info.currency)
    if (info.parent_account_id) formData.append('parent_account_id', info.parent_account_id)
    return request<ImportResult>('/api/accounting/import', { method: 'POST', body: formData })
  },
  importSofiStatementPdf: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return request<SofiStatementImportResult>('/api/accounting/import/sofi-statement-pdf', {
      method: 'POST',
      body: formData,
    })
  },
  importPaystub: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return request<PaystubReconciliationResult>('/api/accounting/import/paystub', { method: 'POST', body: formData })
  },
  rebuild: () => request<{ total_posting_count: number }>('/api/accounting/rebuild', { method: 'POST' }),
  postings: () => request<Posting[]>('/api/accounting/postings'),
  putPostingOverride: (postingId: string, override: ManualOverride) =>
    request<ManualOverride>(`/api/accounting/postings/${encodeURIComponent(postingId)}/override`, jsonInit('PUT', override)),
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
  transferSuggestions: () => request<TransferSuggestion[]>('/api/accounting/transfer-suggestions'),
  netWorth: (asOf?: string, displayCurrency?: string) =>
    request<NetWorthSummary>(`/api/accounting/net-worth${queryString({ as_of: asOf, display_currency: displayCurrency })}`),
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
  putBudgets: (budgets: Budget[]) => request<Budget[]>('/api/accounting/budgets', jsonInit('PUT', budgets)),
  putGeneralBudgets: (generalBudgets: Record<string, GeneralBudget>) =>
    request<Record<string, GeneralBudget>>('/api/accounting/general-budgets', jsonInit('PUT', generalBudgets)),
  budgetComparison: (month: string, displayCurrency?: string) =>
    request<BudgetComparisonRow[]>(
      `/api/accounting/budgets/comparison${queryString({ month, display_currency: displayCurrency })}`,
    ),
  suggestedBudgetAmount: (categoryId: string, month: string, lookbackMonths?: number, displayCurrency?: string) =>
    request<{ suggested_amount: number }>(
      `/api/accounting/budgets/suggested-amount${queryString({ category_id: categoryId, month, lookback_months: lookbackMonths, display_currency: displayCurrency })}`,
    ),
  interestSummary: (asOf?: string) =>
    request<InterestAccountRow[]>(`/api/accounting/interest-summary${queryString({ as_of: asOf })}`),
  putSimulatorScenarios: (scenarios: SimulatorScenario[]) =>
    request<SimulatorScenario[]>('/api/accounting/simulator/scenarios', jsonInit('PUT', scenarios)),
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
}
