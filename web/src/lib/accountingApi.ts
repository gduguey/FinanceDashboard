import { ApiError } from '@/lib/api'
import type {
  AccountingStore,
  Category,
  DetectedAccount,
  ImportResult,
  ManualOverride,
  NetWorthSummary,
  OtherAsset,
  Posting,
  Rule,
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

export const accountingApi = {
  store: () => request<AccountingStore>('/api/accounting/store'),
  putCategories: (categories: Record<string, Category>) =>
    request<Record<string, Category>>('/api/accounting/categories', jsonInit('PUT', categories)),
  putTags: (tags: Record<string, Tag>) => request<Record<string, Tag>>('/api/accounting/tags', jsonInit('PUT', tags)),
  putRules: (rules: Rule[]) => request<Rule[]>('/api/accounting/rules', jsonInit('PUT', rules)),
  putOtherAssets: (otherAssets: OtherAsset[]) =>
    request<OtherAsset[]>('/api/accounting/other-assets', jsonInit('PUT', otherAssets)),
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
  rebuild: () => request<{ total_posting_count: number }>('/api/accounting/rebuild', { method: 'POST' }),
  postings: () => request<Posting[]>('/api/accounting/postings'),
  putPostingOverride: (postingId: string, override: ManualOverride) =>
    request<ManualOverride>(`/api/accounting/postings/${encodeURIComponent(postingId)}/override`, jsonInit('PUT', override)),
  transferSuggestions: () => request<TransferSuggestion[]>('/api/accounting/transfer-suggestions'),
  netWorth: (asOf?: string) =>
    request<NetWorthSummary>(asOf ? `/api/accounting/net-worth?as_of=${asOf}` : '/api/accounting/net-worth'),
}
