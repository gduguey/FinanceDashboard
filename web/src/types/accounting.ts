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
  closed: boolean
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

export interface TransferRule {
  rule_id: string
  description_contains: string
  account_id: string | null
  category_id: string | null
  subcategory_id: string | null
  counterparty_account_id: string | null
  priority: number
  description: string
  active: boolean
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

export interface ManualTransfer {
  transfer_id: string
  date: string
  from_account_id: string
  to_account_id: string
  from_amount: number
  to_amount: number
  description: string
}

export interface Budget {
  budget_id: string
  month: string
  category_id: string
  subcategory_id: string | null
  amount: number
  currency: CurrencyCode
}

export interface GeneralBudget {
  category_id: string
  subcategory_id: string | null
  amount: number
  currency: CurrencyCode
}

export interface BudgetComparisonRow {
  category_id: string
  category_name: string
  subcategory_id: string | null
  subcategory_name: string | null
  color: string
  budgeted: number
  actual: number
  currency: CurrencyCode
}

export interface SyncStatus {
  last_import_at: string | null
}

export interface Goal {
  goal_id: string
  name: string
  target_amount: number
  target_currency: CurrencyCode
  target_date: string
  color: string
  created_at: string
}

export type GoalContributionOrigin = 'manual' | 'automation'

export interface GoalContribution {
  contribution_id: string
  goal_id: string
  date: string
  amount: number
  currency: CurrencyCode
  note: string
  source_posting_id: string | null
  origin: GoalContributionOrigin
  edited: boolean
}

export type RecurringAdditionMode = 'fixed_amount' | 'percent_of_unallocated' | 'remainder'
export type RecurringAdditionFrequency = 'daily' | 'weekly' | 'biweekly' | 'monthly'

export interface RecurringAddition {
  addition_id: string
  goal_id: string
  start_date: string
  frequency: RecurringAdditionFrequency
  end_date: string | null
  mode: RecurringAdditionMode
  value: number
  currency: CurrencyCode
  priority: number
}

export interface WithdrawalPriorityEntry {
  goal_id: string
  priority: number
}

export interface GoalsSummary {
  balances: Record<string, number>
  unallocated: number
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

export type PendingSuggestionSource = 'ai' | 'pattern'

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
  pending_source: PendingSuggestionSource | null
  pending_selected: boolean
  resolved_by_transfer_rule_id: string | null
}

export interface CategoryPattern {
  pattern_id: string
  description_contains: string
  category_id: string
  subcategory_id: string | null
  priority: number
  active: boolean
}

export interface ManualOverride {
  account_id?: string | null
  category_id?: string | null
  subcategory_id?: string | null
  tag_ids?: string[] | null
  pending_selected?: boolean
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
  transfer_rules: TransferRule[]
  category_patterns: Record<string, CategoryPattern>
  other_assets: OtherAsset[]
  opening_balances: Record<string, OpeningBalance>
  manual_transfers: ManualTransfer[]
  posting_merges: Record<string, PostingMerge>
  budgets: Budget[]
  general_budgets: Record<string, GeneralBudget>
  simulator_scenarios: SimulatorScenario[]
  goals: Record<string, Goal>
  goal_contributions: Record<string, GoalContribution>
  recurring_additions: RecurringAddition[]
  withdrawal_priorities: WithdrawalPriorityEntry[]
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

export interface CanonicalImportResult extends ImportResult {
  new_categories: Category[]
}

export interface CanonicalImportPreview {
  new_categories: Category[]
}

export interface CanonicalCategoryOverrides {
  categories: Record<string, string>
  subcategories: Record<string, Record<string, string>>
}

export interface CategorizationMatch {
  row_number: number
  posted_at: string
  description: string
  amount: number
  proposed_category_id: string | null
  proposed_category_name: string | null
  proposed_subcategory_id: string | null
  proposed_subcategory_name: string | null
  posting_id: string | null
  transaction_id: string | null
  matched_description: string | null
  existing_category_id: string | null
  confidence: number | null
}

export interface CategorizeFromFilePreview {
  matches: CategorizationMatch[]
  new_categories: Category[]
}

export interface CategorizeFromFileApplyResult {
  updated_posting_count: number
  new_categories: Category[]
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
  description: string
  other_account_id: string
  other_posted_at: string
  other_posting_id: string
  other_description: string
  amount: number
  suggestion_id: string
}

export interface DuplicatePosting {
  posting_id: string
  transaction_id: string
  posted_at: string
  description: string
  amount: number
}

export interface DuplicateGroup {
  group_key: string
  account_id: string
  certainty: number
  postings: DuplicatePosting[]
  suggestion_id: string
}

export type DismissedSuggestionKind = 'transfer' | 'duplicate'

export interface DismissedSuggestion {
  suggestion_id: string
  kind: DismissedSuggestionKind
  description: string
  dismissed_at: string
}

export interface DismissSuggestionRequest {
  suggestion_id: string
  kind: DismissedSuggestionKind
  description: string
}

export interface PostingMerge {
  merge_id: string
  kept_transaction_id: string
  duplicate_transaction_ids: string[]
  description: string | null
}

export interface LlmProviderUsage {
  configured: boolean
  used_count: number
  period: 'daily' | 'monthly'
  is_limited: boolean
  last_error: string | null
}

export type LlmUsage = Record<string, LlmProviderUsage>

export interface LlmSettings {
  gemini_key_set: boolean
  mistral_key_set: boolean
}

export interface LlmSettingsUpdate {
  gemini_api_key?: string
  mistral_api_key?: string
}

export interface VerifyResult {
  ok: boolean
  error: string | null
}

export interface CategoryTotalRow {
  classification: CategoryClassification
  category_id: string
  category_name: string
  subcategory_id: string | null
  subcategory_name: string | null
  // The subcategory's own color when there is one, else the top-level
  // category's — use this for anything scoped to one row.
  color: string
  // Always the top-level category's own color, regardless of subcategory —
  // use this when aggregating several subcategory rows back into one
  // top-level slice, so it doesn't inherit whichever subcategory happened
  // to be encountered first.
  category_color: string
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
