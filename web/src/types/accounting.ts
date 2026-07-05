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

export interface OpeningBalance {
  account_id: string
  amount: number
  as_of_date: string
}

export interface Budget {
  budget_id: string
  month: string
  category_id: string
  amount: number
  currency: CurrencyCode
}

export interface GeneralBudget {
  category_id: string
  amount: number
  currency: CurrencyCode
}

export interface BudgetComparisonRow {
  category_id: string
  category_name: string
  color: string
  budgeted: number
  actual: number
  currency: CurrencyCode
}

export type CompoundingFrequency = 'annually' | 'monthly' | 'daily'

export interface SimulatorScenario {
  scenario_id: string
  name: string
  initial_capital: number
  monthly_contribution: number
  horizon_years: number
  annual_rate_pct: number
  compounding_frequency: CompoundingFrequency
  currency: CurrencyCode
}

export interface ProjectionPoint {
  month: number
  balance: number
  contributions_to_date: number
}

export interface InterestAccountRow {
  account_id: string
  account_name: string
  currency: CurrencyCode
  apy_pct: number
  interest_earned_this_year: number
  current_balance: number
  projected_next_12_months: number
  benchmark_apy_pct: number | null
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

export interface EarningsDeposit {
  label: string
  account_last4: string | null
  amount: number
}

export interface EarningsLineItem {
  label: string
  amount: number
}

export interface EarningsStatement {
  pay_date: string
  gross_pay: number
  taxes_withheld: number
  net_pay: number
  deposits: EarningsDeposit[]
  reimbursement_lines: EarningsLineItem[]
}

export interface DepositMatch {
  label: string
  amount: number
  account_last4: string | null
  posting_id: string | null
  account_id: string | null
}

export interface ProposedSplitLeg {
  amount: number
  category_id: string | null
  subcategory_id: string | null
  description: string
}

export interface ProposedSplit {
  posting_id: string
  account_id: string
  legs: ProposedSplitLeg[]
}

export interface PaystubReconciliationResult {
  statement: EarningsStatement
  matches: DepositMatch[]
  is_fully_matched: boolean
  proposed_splits: ProposedSplit[]
}

export interface PostingSplitLeg {
  amount: number
  category_id: string | null
  subcategory_id: string | null
  description: string
}

export interface AccountingStore {
  accounts: Record<string, Account>
  categories: Record<string, Category>
  tags: Record<string, Tag>
  rules: Rule[]
  other_assets: OtherAsset[]
  opening_balances: Record<string, OpeningBalance>
  budgets: Budget[]
  general_budgets: Record<string, GeneralBudget>
  simulator_scenarios: SimulatorScenario[]
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
  parent_account_id: string | null
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

export interface NetWorthHistoryByAccountPoint {
  date: string
  account_id: string
  account_name: string
  balance: number
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

export interface LlmProviderUsage {
  configured: boolean
  used_count: number
  period: 'daily' | 'monthly'
  is_limited: boolean
  last_error: string | null
}

export type LlmUsage = Record<string, LlmProviderUsage>

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
