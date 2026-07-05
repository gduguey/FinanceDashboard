export type AccountKind =
  | 'checking'
  | 'savings'
  | 'credit_card'
  | 'vault'
  | 'cash'
  | 'loan'
  | 'income_source'
  | 'expense_payee'
  | 'external_investment'
  | 'other_asset'

export type CategoryClassification = 'income' | 'expense'

export type CurrencyCode = 'USD' | 'EUR'

export interface Currency {
  code: CurrencyCode
  symbol: string
  decimal_places: number
}

export interface Account {
  account_id: string
  name: string
  kind: AccountKind
  institution: string
  currency: CurrencyCode
  parent_account_id: string | null
  external_ref: string | null
  meta: Record<string, string>
}

export interface Category {
  category_id: string
  name: string
  classification: CategoryClassification
  parent_category_id: string | null
  color: string
}

export interface Tag {
  tag_id: string
  name: string
}

export interface Rule {
  rule_id: string
  description_contains: string
  account_id: string | null
  category_id: string | null
  subcategory_id: string | null
  counterparty_account_id: string | null
  counterparty_account_name: string | null
  counterparty_account_kind: AccountKind | null
  counterparty_parent_account_id: string | null
  priority: number
  description: string
}

export interface OtherAsset {
  asset_id: string
  name: string
  value: number
  currency: CurrencyCode
  note: string
}

export interface Posting {
  posting_id: string
  transaction_id: string
  account_id: string
  posted_at: string
  amount: number
  currency: CurrencyCode
  category_id: string | null
  subcategory_id: string | null
  budget_id: string | null
  tag_ids: string[]
  description: string
  meta: Record<string, string>
}

export interface ManualOverride {
  account_id?: string | null
  category_id?: string | null
  subcategory_id?: string | null
  tag_ids?: string[] | null
}

export interface AccountingStore {
  accounts: Record<string, Account>
  categories: Record<string, Category>
  tags: Record<string, Tag>
  rules: Rule[]
  other_assets: OtherAsset[]
}

export interface ExchangeRateSyncResult {
  as_of: string
  base_currency: CurrencyCode
  rates_to_base: Record<CurrencyCode, number>
}

export interface CurrentExchangeRate {
  currency: CurrencyCode
  base_currency: CurrencyCode
  rate_to_base: number
  as_of: string
  window_days: number
}

export interface ExchangeRateHistoryPoint {
  date: string
  rate: number
  smoothed_rate: number
}

export interface DetectedAccount {
  institution: string
  account_kind: AccountKind
  account_id: string
  account_name: string
}

export interface ImportResult {
  account_id: string
  new_posting_count: number
  total_posting_count: number
}

export interface SofiStatementImportResult {
  account_ids: string[]
  new_posting_count: number
  total_posting_count: number
}

export interface NetWorthAccountRow {
  account_id: string
  name: string
  kind: AccountKind
  parent_account_id: string | null
  balance: number
  currency: CurrencyCode
}

export interface NetWorthSummary {
  as_of: string
  display_currency: CurrencyCode
  assets: number
  liabilities: number
  other_assets_total: number
  net_worth: number
  accounts: NetWorthAccountRow[]
  other_assets: OtherAsset[]
}

export interface NetWorthHistoryPoint {
  date: string
  net_worth: number
}

export interface TransferSuggestion {
  account_id: string
  posting_id: string
  posted_at: string
  other_account_id: string
  other_posted_at: string
  other_posting_id: string
  amount: number
}

export interface CategoryTotalRow {
  classification: CategoryClassification
  category_id: string
  category_name: string
  subcategory_id: string | null
  subcategory_name: string | null
  color: string
  amount: number
}

export interface MonthlyIncomeExpenseRow {
  month: string
  income: number
  expense: number
}

export interface SpendCurvePoint {
  day: number
  current_month_cumulative: number
  average_previous_months_cumulative: number
}

export const UNCATEGORIZED_INCOME_CATEGORY_ID = 'uncategorized:income-category'
export const UNCATEGORIZED_EXPENSE_CATEGORY_ID = 'uncategorized:expense-category'
