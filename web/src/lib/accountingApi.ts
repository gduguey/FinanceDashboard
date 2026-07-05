import { ApiError } from '@/lib/api'
import type {
  Account,
  AccountingStore,
  Category,
  CategoryTotalRow,
  Currency,
  DetectedAccount,
  ImportResult,
  ManualOverride,
  MonthlyIncomeExpenseRow,
  NetWorthHistoryPoint,
  NetWorthSummary,
  OtherAsset,
  Posting,
  Rule,
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
  setExchangeRate: (eurUsdRate: number) =>
    request<{ eur_usd_rate: number }>('/api/accounting/settings/exchange-rate', jsonInit('PUT', { eur_usd_rate: eurUsdRate })),
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
  detect: (header: string[], filename: string) =>
    request<DetectedAccount | null>('/api/accounting/detect', jsonInit('POST', { header, filename })),
  importCsv: (file: File, info: ImportAccountInfo) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('institution', info.institution)
    formData.append('account_kind', info.account_kind)
    formData.append('account_id', info.account_id)
    formData.append('account_name', info.account_name)
    if (info.currency) formData.append('currency', info.currency)
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
  rebuild: () => request<{ total_posting_count: number }>('/api/accounting/rebuild', { method: 'POST' }),
  postings: () => request<Posting[]>('/api/accounting/postings'),
  putPostingOverride: (postingId: string, override: ManualOverride) =>
    request<ManualOverride>(`/api/accounting/postings/${encodeURIComponent(postingId)}/override`, jsonInit('PUT', override)),
  transferSuggestions: () => request<TransferSuggestion[]>('/api/accounting/transfer-suggestions'),
  netWorth: (asOf?: string, displayCurrency?: string) =>
    request<NetWorthSummary>(`/api/accounting/net-worth${queryString({ as_of: asOf, display_currency: displayCurrency })}`),
  netWorthHistory: (start: string, end: string, intervalDays?: number, displayCurrency?: string) =>
    request<NetWorthHistoryPoint[]>(
      `/api/accounting/net-worth/history${queryString({ start, end, interval_days: intervalDays, display_currency: displayCurrency })}`,
    ),
  categoryTotals: (start: string, end: string, accountIds?: string[], displayCurrency?: string) =>
    request<CategoryTotalRow[]>(
      `/api/accounting/income-statement/category-totals${queryString({ start, end, account_ids: accountIds?.join(','), display_currency: displayCurrency })}`,
    ),
  monthlyIncomeExpense: (start: string, end: string, displayCurrency?: string) =>
    request<MonthlyIncomeExpenseRow[]>(
      `/api/accounting/income-statement/monthly${queryString({ start, end, display_currency: displayCurrency })}`,
    ),
  spendCurve: (month: string, lookbackMonths?: number, displayCurrency?: string) =>
    request<SpendCurvePoint[]>(
      `/api/accounting/income-statement/spend-curve${queryString({ month, lookback_months: lookbackMonths, display_currency: displayCurrency })}`,
    ),
}
