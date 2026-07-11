"""Pydantic request/response models for `accounting.api`'s endpoints.

Every `BaseModel` subclass used by the routers in `accounting.api.routers`
lives here — request bodies and response models alike — so a model's
shape is defined exactly once, importable by whichever router needs it,
without any router needing to know about another router's models.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from accounting.models import (
    Account,
    AccountKind,
    Budget,
    Category,
    CategoryClassification,
    CategoryPattern,
    CurrencyCode,
    EarningsStatement,
    GeneralBudget,
    Goal,
    GoalContribution,
    ManualTransfer,
    OpeningBalance,
    OtherAsset,
    PendingSuggestionSource,
    Posting,
    PostingMerge,
    PostingSplit,
    PostingSplitLeg,
    RecurringAddition,
    SimulatorScenario,
    Tag,
    TransferRule,
    WithdrawalPriorityEntry,
)


class AccountingStoreResponse(BaseModel):
    """Every persisted accounting entity: accounts, categories, tags, rules, other assets.

    Mirrors `store.AccountingStore` field-for-field, except `rules` is
    exposed as `transfer_rules` (the name every other endpoint and the
    frontend already use for it) and `dismissed_suggestions` is omitted —
    nothing in the frontend reads the whole store for those.
    """

    accounts: dict[str, Account]
    categories: dict[str, Category]
    tags: dict[str, Tag]
    transfer_rules: list[TransferRule]
    other_assets: list[OtherAsset]
    opening_balances: dict[str, OpeningBalance]
    manual_transfers: list[ManualTransfer]
    budgets: list[Budget]
    simulator_scenarios: list[SimulatorScenario]
    posting_splits: dict[str, PostingSplit]
    posting_merges: dict[str, PostingMerge]
    general_budgets: dict[str, GeneralBudget]
    category_patterns: dict[str, CategoryPattern]
    goals: dict[str, Goal]
    goal_contributions: dict[str, GoalContribution]
    recurring_additions: list[RecurringAddition]
    withdrawal_priorities: list[WithdrawalPriorityEntry]


class ExchangeRateSyncResult(BaseModel):
    """Response body for `POST /sync-exchange-rates`."""

    as_of: date
    base_currency: CurrencyCode
    rates_to_base: dict[CurrencyCode, float]


class CurrentExchangeRate(BaseModel):
    """Response body for `GET /exchange-rates/current`."""

    currency: CurrencyCode
    base_currency: CurrencyCode
    rate_to_base: float
    as_of: date
    window_days: int


class ExchangeRateHistoryPoint(BaseModel):
    """One cached day's raw and smoothed exchange rate, for `GET /exchange-rates/history`."""

    date: date
    rate: float
    smoothed_rate: float


class CategoryRenameRequest(BaseModel):
    """Request body for `POST /categories/{category_id}/rename`."""

    name: str


class CategoryRenameResponse(BaseModel):
    """Response body for `POST /categories/{category_id}/rename`."""

    categories: dict[str, Category]
    merged: bool


class ProjectionPoint(BaseModel):
    """One projected month's balance, for `GET /simulator/project` — see `dashboard.simulator.ProjectionPoint`."""

    month: int
    balance: float
    contributions_to_date: float


class AccountUpdate(BaseModel):
    """Request body for `PUT /api/accounting/accounts/{account_id}`.

    `institution`, `kind`, and `currency` may only differ from the
    account's current values while it has no postings yet — enforced in
    `put_account`, not here, since that check needs the ledger. `closed`
    isn't edited here — see `close_account`/`reopen_account`, which pair it
    with recording where a closed account's remaining balance went.
    `external_ref` is never locked — it only ever changes which value an
    `external_investment` account shows (see `dashboard.net_worth`), never
    what it has already recorded, so it's free to toggle regardless of postings.
    """

    name: str
    institution: str
    kind: AccountKind
    currency: CurrencyCode
    external_ref: str | None = None
    meta: dict[str, str] = Field(default_factory=dict)


class AccountIdResponse(BaseModel):
    """Response body naming one account, for endpoints whose only real effect is removing something."""

    account_id: str


class AccountCloseRequest(BaseModel):
    """Request body for `POST /api/accounting/accounts/{account_id}/close`."""

    transfers: list[ManualTransfer] = Field(default_factory=list)


class AccountCloseResponse(BaseModel):
    """Response body for `POST /api/accounting/accounts/{account_id}/close`."""

    account: Account
    manual_transfers: list[ManualTransfer]


class DetectRequest(BaseModel):
    """Request body for `POST /api/accounting/detect`."""

    header: list[str]
    filename: str
    first_data_row: dict[str, str] | None = None


class DetectedAccount(BaseModel):
    """A best-guess bank, account kind, and stable account id for one uploaded CSV — see `importers.detect`."""

    institution: str
    account_kind: AccountKind
    account_id: str
    account_name: str
    parent_account_id: str | None = None


class SupportedImportKind(BaseModel):
    """One `(institution, account_kind)` pair with a registered CSV standardizer."""

    institution: str
    account_kind: str


class SyncStatus(BaseModel):
    """When a bank statement was most recently imported, across every institution and account."""

    last_import_at: datetime | None


class SkippedRowsInfo(BaseModel):
    """Rows an import couldn't parse — see `importers.canonical.csv.SkippedRowsInfo`."""

    total_rows: int
    skipped_count: int
    bad_dates: int
    bad_amounts: int
    skipped_row_numbers: list[int]


class ImportResult(BaseModel):
    """Response body for `POST /import` — what one CSV import produced.

    `skipped_rows` is only ever present (non-`None`) when at least one row
    couldn't be parsed; omitted from the hand-written TS `ImportResult`
    type, but real, present-when-relevant behavior of this endpoint.
    """

    account_id: str
    new_posting_count: int
    total_posting_count: int
    skipped_rows: SkippedRowsInfo | None = None


class CanonicalCategoryOverridesRequest(BaseModel):
    """The `category_overrides` form field's JSON shape — see `importers.canonical.csv.CategoryOverrides`."""

    categories: dict[str, str] = Field(default_factory=dict)
    subcategories: dict[str, dict[str, str]] = Field(default_factory=dict)


class CanonicalImportPreview(BaseModel):
    """Response body for `POST /import/canonical/preview`."""

    new_categories: list[Category]


class CanonicalImportResult(ImportResult):
    """Response body for `POST /import/canonical` — an `ImportResult` plus any categories the file created."""

    new_categories: list[Category]


class CategorizationMatch(BaseModel):
    """One file row's proposed match against the ledger — see `importers.categorize_from_file.CategorizationMatch`."""

    row_number: int
    posted_at: datetime
    description: str
    amount: float
    proposed_category_id: str | None
    proposed_category_name: str | None
    proposed_subcategory_id: str | None
    proposed_subcategory_name: str | None
    posting_id: str | None
    transaction_id: str | None
    matched_description: str | None
    existing_category_id: str | None
    confidence: float | None


class CategorizeFromFilePreview(BaseModel):
    """Response body for `POST /import/categorize-from-file/preview`."""

    matches: list[CategorizationMatch]
    new_categories: list[Category]
    skipped_rows: SkippedRowsInfo | None = None


class CategorizeFromFileApplyResult(BaseModel):
    """Response body for `POST /import/categorize-from-file/apply`."""

    updated_posting_count: int
    new_categories: list[Category]


class DepositMatch(BaseModel):
    """One paystub deposit, matched (or not) against a real bank posting — see `dashboard.paystub.DepositMatch`."""

    label: str
    amount: float
    account_last4: str | None
    posting_id: str | None
    account_id: str | None


class ProposedSplit(BaseModel):
    """A proposed way to split one matched deposit into categorized legs — see `dashboard.paystub.ProposedSplit`."""

    posting_id: str
    account_id: str
    legs: list[PostingSplitLeg]


class PaystubReconciliationResult(BaseModel):
    """Response body for `POST /import/paystub`."""

    statement: EarningsStatement
    matches: list[DepositMatch]
    is_fully_matched: bool
    proposed_splits: list[ProposedSplit]


class RebuildResult(BaseModel):
    """Response body for `POST /rebuild`."""

    total_posting_count: int


class PostingRow(Posting):
    """One posting as displayed on the Transactions page — a `Posting` plus its current resolution state.

    The three extra fields are display-only, bolted onto the resolved
    ledger by `get_postings` itself rather than stored on the posting —
    see `ledger.pending`/`ledger.categorization.resolved_transfer_rule_ids_by_transaction`.
    """

    pending_source: PendingSuggestionSource | None = None
    pending_selected: bool = True
    resolved_by_transfer_rule_id: str | None = None


class PostingIdResponse(BaseModel):
    """Response body naming one posting, for endpoints whose only real effect is removing something."""

    posting_id: str


class LlmProviderUsage(BaseModel):
    """One LLM provider's self-tracked call count this period, and whether it's currently rate-limited."""

    configured: bool
    used_count: int
    period: Literal["daily", "monthly"]
    is_limited: bool
    last_error: str | None


class VerifyResult(BaseModel):
    """Response body for `POST /settings/llm/verify`."""

    ok: bool
    error: str | None


class LLMSettingsUpdate(BaseModel):
    """Request body for `PUT /api/accounting/settings/llm`.

    Either field left `None` leaves that one exactly as it was — entering
    a Gemini key doesn't clear an existing Mistral one.
    """

    gemini_api_key: str | None = None
    mistral_api_key: str | None = None


class LlmSettings(BaseModel):
    """Response body for `GET`/`PUT`/`DELETE /settings/llm` — whether each provider's key override is set."""

    gemini_key_set: bool
    mistral_key_set: bool


class CategorySuggestionResult(BaseModel):
    """Response body for `POST .../ai-suggest-category` and `.../pattern-suggest-category`."""

    category_id: str | None
    subcategory_id: str | None
    applied: bool


class PatternSuggestBulkRequest(BaseModel):
    """Which postings to run category-pattern matching over, in one call."""

    posting_ids: list[str]


class BulkSuggestResult(BaseModel):
    """Response body for `POST /postings/pattern-suggest-category/bulk`."""

    applied: int


class ValidatePendingRequest(BaseModel):
    """Which postings' pending suggestions to resolve — always exactly the caller's current filtered view."""

    posting_ids: list[str]


class ValidatePendingResult(BaseModel):
    """Response body for `POST /postings/validate-pending`."""

    accepted: int
    reverted: int


class TransferSuggestion(BaseModel):
    """One likely internal transfer no rule has resolved yet.

    See `ledger.transfers.find_unmatched_transfer_candidates`.
    """

    account_id: str
    posting_id: str
    posted_at: datetime
    description: str
    other_account_id: str
    other_posted_at: datetime
    other_posting_id: str
    other_description: str
    amount: float
    suggestion_id: str


class DuplicatePosting(BaseModel):
    """One posting belonging to a likely-duplicate group — see `ledger.duplicates.DuplicatePosting`."""

    posting_id: str
    transaction_id: str
    posted_at: datetime
    description: str
    amount: float


class DuplicateGroup(BaseModel):
    """A likely-duplicate group of transactions — see `ledger.duplicates.DuplicateGroup`."""

    group_key: str
    account_id: str
    certainty: float
    postings: list[DuplicatePosting]
    suggestion_id: str


class DismissSuggestionRequest(BaseModel):
    """Request body for `POST /api/accounting/dismissed-suggestions`."""

    suggestion_id: str
    kind: Literal["transfer", "duplicate"]
    description: str


class SuggestionIdResponse(BaseModel):
    """Response body naming one dismissed suggestion, for endpoints whose only real effect is removing something."""

    suggestion_id: str


class InterestAccountRow(BaseModel):
    """One savings/vault account's year-to-date interest, current APY, balance, and a one-year projection.

    See `dashboard.interest.InterestAccountRow`.
    """

    account_id: str
    account_name: str
    currency: CurrencyCode
    apy_pct: float
    interest_earned_this_year: float
    current_balance: float
    projected_next_12_months: float
    benchmark_apy_pct: float | None


class NetWorthAccountRow(BaseModel):
    """One real account's current balance, in its own currency — see `dashboard.net_worth.AccountBalanceRow`."""

    account_id: str
    name: str
    kind: AccountKind
    parent_account_id: str | None
    balance: float
    currency: CurrencyCode


class NetWorthSummary(BaseModel):
    """Assets, liabilities, and net worth as of one date — see `dashboard.net_worth.NetWorthSummary`."""

    as_of: date
    display_currency: CurrencyCode
    assets: float
    liabilities: float
    other_assets_total: float
    net_worth: float
    accounts: list[NetWorthAccountRow]
    other_assets: list[OtherAsset]


class NetWorthHistoryPoint(BaseModel):
    """One date's aggregate net worth, for `GET /net-worth/history`."""

    date: date
    net_worth: float


class NetWorthHistoryByAccountPoint(BaseModel):
    """One (date, account) balance, for `GET /net-worth/history/by-account`."""

    date: date
    account_id: str
    account_name: str
    balance: float


class CategoryTotalRow(BaseModel):
    """One classification/category/subcategory's summed amount — see `dashboard.income_statement.category_totals`."""

    classification: CategoryClassification
    category_id: str
    category_name: str
    subcategory_id: str | None
    subcategory_name: str | None
    color: str
    category_color: str
    amount: float


class MonthlyIncomeExpenseRow(BaseModel):
    """One calendar month's real income and real expense — see `dashboard.income_statement.monthly_income_expense`."""

    month: str
    income: float
    expense: float


class SpendCurvePoint(BaseModel):
    """One day's cumulative spend, next to the same day-of-month average over prior months.

    See `dashboard.income_statement.spend_curve_vs_average`.
    """

    day: int
    current_month_cumulative: float
    average_previous_months_cumulative: float


class BudgetComparisonRow(BaseModel):
    """One category (or subcategory)'s budget target next to its actual spend.

    See `dashboard.budgets.BudgetComparisonRow`.
    """

    category_id: str
    category_name: str
    subcategory_id: str | None
    subcategory_name: str | None
    color: str
    budgeted: float
    actual: float
    currency: CurrencyCode


class SuggestedBudgetAmount(BaseModel):
    """Response body for `GET /budgets/suggested-amount`."""

    suggested_amount: float


class GoalsSummary(BaseModel):
    """Response body for `GET /goals/summary`."""

    balances: dict[str, float]
    unallocated: float


class WithdrawalAutomationResult(BaseModel):
    """Response body for `POST /goals/run-withdrawal-automation`."""

    withdrawals: list[GoalContribution]
    remaining_shortfall: float


class SimulateContributionRequest(BaseModel):
    """A proposed manual contribution, checked against unallocated money before the user commits to it."""

    goal_id: str
    date: date
    amount: float


class SimulateContributionResult(BaseModel):
    """Response body for `POST /goals/simulate-contribution`."""

    unallocated_as_of_date: float
    exceeds_unallocated: bool
    projected_next_run_unallocated: float
    would_go_negative: bool
