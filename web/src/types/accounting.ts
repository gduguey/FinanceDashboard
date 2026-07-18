// Every type below is a thin alias onto `./schema.ts` — the file
// `openapi-typescript` generates from `src/accounting/api.py`'s own
// OpenAPI schema (see `scripts/export_openapi_schema.py` and the
// `generate:schema` npm script). Nothing here is hand-typed against the
// JSON shape anymore: a backend field rename shows up here automatically
// next time `schema.ts` is regenerated, and CI's `openapi-types` workflow
// fails the build if it isn't.
import type { components } from './schema'

export type Account = components['schemas']['Account']
export type AccountKind = Account['kind']
export type Category = components['schemas']['Category']
export type CategoryClassification = Category['classification']
export type Currency = components['schemas']['Currency']
export type CurrencyCode = Currency['code']
export type Tag = components['schemas']['Tag']
export type TransferRule = components['schemas']['TransferRule']
export type OtherAsset = components['schemas']['OtherAsset']
export type OpeningBalance = components['schemas']['OpeningBalance']
export type ManualTransfer = components['schemas']['ManualTransfer']
export type Budget = components['schemas']['Budget']
export type BudgetUpsert = components['schemas']['BudgetUpsert']
export type GeneralBudget = components['schemas']['GeneralBudget']
export type GeneralBudgetUpsert = components['schemas']['GeneralBudgetUpsert']
export type BudgetComparisonRow = components['schemas']['BudgetComparisonRow']
export type SyncStatus = components['schemas']['SyncStatus']
export type Goal = components['schemas']['Goal']
export type GoalContribution = components['schemas']['GoalContribution']
export type GoalContributionOrigin = GoalContribution['origin']
export type GoalContributionCreate = components['schemas']['GoalContributionCreate']
export type GoalContributionUpdate = components['schemas']['GoalContributionUpdate']
export type RecurringAddition = components['schemas']['RecurringAddition']
export type RecurringAdditionMode = RecurringAddition['mode']
export type RecurringAdditionFrequency = RecurringAddition['frequency']
export type WithdrawalPriorityEntry = components['schemas']['WithdrawalPriorityEntry']
export type GoalsSummary = components['schemas']['GoalsSummary']
export type SimulatorScenario = components['schemas']['SimulatorScenario']
export type CompoundingFrequency = SimulatorScenario['compounding_frequency']
export type ProjectionPoint = components['schemas']['ProjectionPoint']
export type InterestAccountRow = components['schemas']['InterestAccountRow']

// `GET /api/accounting/postings` bolts three display-only fields
// (`pending_source`, `pending_selected`, `resolved_by_transfer_rule_id`)
// onto the stored posting — that richer shape is `PostingRow`, which is
// what every consumer of this `Posting` alias actually reads. The bare
// `Posting` schema (no bolted-on fields) backs request bodies elsewhere.
export type Posting = components['schemas']['PostingRow']
export type PendingSuggestionSource = NonNullable<Posting['pending_source']>

export type CategoryPattern = components['schemas']['CategoryPattern']
export type ManualOverride = components['schemas']['ManualOverride']
export type EarningsDeposit = components['schemas']['EarningsDeposit']
export type EarningsLineItem = components['schemas']['EarningsLineItem']
export type EarningsStatement = components['schemas']['EarningsStatement']
export type DepositMatch = components['schemas']['DepositMatch']
export type PostingSplitLeg = components['schemas']['PostingSplitLeg']
export type ProposedSplitLeg = components['schemas']['PostingSplitLeg']
export type ProposedSplit = components['schemas']['ProposedSplit']
export type PaystubReconciliationResult = components['schemas']['PaystubReconciliationResult']
export type AccountingStore = components['schemas']['AccountingStoreResponse']
export type CurrentExchangeRate = components['schemas']['CurrentExchangeRate']
export type ExchangeRateHistoryPoint = components['schemas']['ExchangeRateHistoryPoint']
export type DetectedAccount = components['schemas']['DetectedAccount']
export type ImportResult = components['schemas']['ImportResult']
export type CanonicalImportResult = components['schemas']['CanonicalImportResult']
export type CanonicalImportPreview = components['schemas']['CanonicalImportPreview']

// `category_overrides` is sent as a JSON-encoded string form field (see
// `accounting.api._read_category_overrides`), never a structured request
// body FastAPI can describe — no schema component exists for it.
export interface CanonicalCategoryOverrides {
  categories: Record<string, string>
  subcategories: Record<string, Record<string, string>>
}

export type CategorizationMatch = components['schemas']['CategorizationMatch']
export type CategorizeFromFilePreview = components['schemas']['CategorizeFromFilePreview']
export type CategorizeFromFileApplyResult = components['schemas']['CategorizeFromFileApplyResult']
export type NetWorthAccountRow = components['schemas']['NetWorthAccountRow']
export type NetWorthSummary = components['schemas']['NetWorthSummary']
export type NetWorthHistoryPoint = components['schemas']['NetWorthHistoryPoint']
export type NetWorthHistoryByAccountPoint = components['schemas']['NetWorthHistoryByAccountPoint']
export type TransferSuggestion = components['schemas']['TransferSuggestion']
export type DuplicatePosting = components['schemas']['DuplicatePosting']
export type DuplicateGroup = components['schemas']['DuplicateGroup']
export type DismissedSuggestion = components['schemas']['DismissedSuggestion']
export type DismissedSuggestionKind = DismissedSuggestion['kind']
export type DismissSuggestionRequest = components['schemas']['DismissSuggestionRequest']
export type PostingMerge = components['schemas']['PostingMerge']
export type PostingMergeUpsert = components['schemas']['PostingMergeUpsert']
export type TransferLink = components['schemas']['TransferLink']
export type TransferLinkSource = TransferLink['source']
export type TransferLinkCreate = components['schemas']['TransferLinkCreate']
export type LlmProviderUsage = components['schemas']['LlmProviderUsage']

// `GET /api/accounting/llm-usage` returns a bare `symbol -> LlmProviderUsage`
// map with no fixed keys (one per configured provider) — there's no named
// response model for openapi-typescript to generate a component for.
export type LlmUsage = Record<string, LlmProviderUsage>

export type LlmSettings = components['schemas']['LlmSettings']
export type LlmSettingsUpdate = components['schemas']['LLMSettingsUpdate']
export type VerifyResult = components['schemas']['accounting__api__api_models__VerifyResult']
export type CategoryTotalRow = components['schemas']['CategoryTotalRow']
export type MonthlyIncomeExpenseRow = components['schemas']['MonthlyIncomeExpenseRow']
export type SpendCurvePoint = components['schemas']['SpendCurvePoint']

export const UNCATEGORIZED_INCOME_CATEGORY_ID = 'uncategorized:income-category'
export const UNCATEGORIZED_EXPENSE_CATEGORY_ID = 'uncategorized:expense-category'
