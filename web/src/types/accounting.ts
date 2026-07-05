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

export interface Account {
  account_id: string
  name: string
  kind: AccountKind
  institution: string
  currency: string
  parent_account_id: string | null
  external_ref: string | null
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
}

export interface OtherAsset {
  asset_id: string
  name: string
  value_usd: number
  note: string
}

export interface Posting {
  posting_id: string
  transaction_id: string
  account_id: string
  posted_at: string
  amount: number
  currency: string
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

export interface NetWorthAccountRow {
  account_id: string
  name: string
  kind: AccountKind
  parent_account_id: string | null
  balance_usd: number
}

export interface NetWorthSummary {
  as_of: string
  assets_usd: number
  liabilities_usd: number
  other_assets_usd: number
  net_worth_usd: number
  accounts: NetWorthAccountRow[]
  other_assets: OtherAsset[]
}

export interface TransferSuggestion {
  account_id: string
  posting_id: string
  posted_at: string
  other_account_id: string
  other_posting_id: string
  other_posted_at: string
  amount: number
}
