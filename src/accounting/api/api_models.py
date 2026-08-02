"""Pydantic request/response models for `accounting.api`'s endpoints.

Every `BaseModel` subclass used by the routers in `accounting.api.routers`
lives here — request bodies and response models alike — so a model's
shape is defined exactly once, importable by whichever router needs it,
without any router needing to know about another router's models.

The one exception is the entities themselves. An endpoint that returns *an
account* or *a category* returns the mirror in `accounting.api.entities`,
not `accounting.models`' own class — see that module for why. The models
here compose those mirrors (`AccountCloseResponse.account`), so nothing in
this file names a domain entity type directly.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from accounting.api.entities import (
    Account,
    Budget,
    Category,
    CategoryPattern,
    EarningsStatement,
    Goal,
    GoalAutomation,
    GoalContribution,
    ManualTransfer,
    OtherAsset,
    Posting,
    PostingSplitLeg,
    SimulatorScenario,
    Tag,
    TransferLink,
    TransferRule,
)
from accounting.models import (
    BASE_CURRENCY,
    AccountKind,
    CategoryClassification,
    CompoundingFrequency,
    CurrencyCode,
    GoalAutomationFrequency,
    GoalAutomationMode,
    GoalContributionOrigin,
    PendingSuggestionSource,
    TransferLinkSource,
)
from db.money import ZERO, Money, Rate
from http_api.pagination import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX, Page


def _reject_a_non_base_currency(value: CurrencyCode) -> CurrencyCode:
    """Refuse a contribution automation denominated in anything but the base currency.

    Item A6. Unallocated money is a *comparison basis*, not a display
    figure, and `api.routers.goals._unallocated_basis` computes it in
    `models.BASE_CURRENCY` for that reason — so
    `ledger.goal_automations.run_recurring_additions` draws from a
    base-currency pool and every amount it hands back is base-currency.
    A non-base automation broke that in both directions at once: a
    `fixed_amount` EUR `value` was subtracted from the pool as though it
    were USD, and the resulting contribution was persisted labelled EUR
    while holding a USD-derived amount, which every later conversion
    then compounded.

    Refused rather than converted, deliberately. No UI can select a
    currency here — `GoalAutomationsPanel.tsx` sends `'USD'` on both
    create and edit and offers no picker — so converting would be
    arithmetic on money that nothing exercises. The conversion this
    should become the day a picker exists is written up as A6's entry in
    `docs/remaining-work.md`, with the round-trip property it has to
    hold.

    Returns
    -------
    CurrencyCode
        `value`, unchanged, when it is the base currency.

    Raises
    ------
    ValueError
        If `value` is any other currency; pydantic turns it into a 422.
    """
    if value != BASE_CURRENCY:
        message = (
            f"A contribution automation must be denominated in {BASE_CURRENCY}, not {value!r} — "
            "unallocated money is computed in the base currency, so a schedule in another one "
            "would fund from a pool it is not measured against."
        )
        raise ValueError(message)
    return value


BaseCurrencyOnly = Annotated[CurrencyCode, AfterValidator(_reject_a_non_base_currency)]
"""A `CurrencyCode` a request may only ever set to `models.BASE_CURRENCY` — see `_reject_a_non_base_currency`."""


class AccountingStoreResponse(BaseModel):
    """Every persisted accounting entity: accounts, categories, tags, rules, other assets.

    One field per repository `load_*`, recomposed at the router (see
    `api.routers.bootstrap.get_store`) — this response model is the only
    place the whole set is named together; nothing server-side passes it
    around. `transfer_rules` is `repositories.interpretation`'s
    `load_transfer_rules`, under the name every other endpoint and the
    frontend already use for it.

    Dismissed suggestions are deliberately not here: unlike every entity
    that is, they're never read as "give me the whole list to build
    something", only ever checked as "has this one already been
    dismissed" — see `GET /dismissed-suggestions` and
    `repositories.interpretation.dismissed_suggestion_ids`, which query
    that table directly.

    Neither are `opening_balances`, `manual_transfers`, `posting_splits`
    or `posting_merges`, which this response used to carry. No frontend
    code path ever read them: the store is read-only (there is no
    `PUT /store` to round-trip them back), splits and merges only ever
    reach the client already folded into `GET /postings`' resolved rows,
    opening balances are edited one account at a time through
    `PUT /accounts/{account_id}/opening-balance`, and the UI's "manually
    added transfers" are `transfer_links` with no `rule_id` — a different
    thing from a `ManualTransfer`, which is not a table at all any more
    but a projection over `origin='manual'` transactions and their two
    balancing postings (see `repositories.accounts.load_manual_transfers`).

    `account_ids_with_postings` is the one field here that is not an
    entity. It is a server-derived fact — which accounts have had real
    money land on them, and therefore have their kind and currency locked
    — and it is deliberately *not* a field on `entities.Account`: that
    type is a wire mirror clients also send back, and a fact only the
    server can know has no business on a shape a client authors. It rides
    here because the page that needs it already makes this read, so it
    costs no round trip; the alternative was the accounts page fetching
    every posting to derive it in the browser, which is what it did.
    """

    accounts: dict[str, Account]
    account_ids_with_postings: list[str]
    categories: dict[str, Category]
    tags: dict[str, Tag]
    transfer_rules: list[TransferRule]
    other_assets: list[OtherAsset]
    budgets: list[Budget]
    simulator_scenarios: list[SimulatorScenario]
    transfer_links: list[TransferLink]
    category_patterns: dict[str, CategoryPattern]
    goals: dict[str, Goal]
    goal_contributions: dict[str, GoalContribution]
    goal_automations: list[GoalAutomation]


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


class BudgetToDeletePreview(BaseModel):
    """One `Budget` entry a category merge would discard rather than keep.

    The merged-away category's own entry is what's described here — the
    merge target's entry for the same month/category always survives
    unchanged (see `store.remap_category_ids`). `month` is `None` for a
    general budget (applies to every month alike), or `"YYYY-MM"` for a
    per-month one.
    """

    month: str | None
    amount: Money
    currency: CurrencyCode


class BudgetUpsert(BaseModel):
    """Request body for `POST /api/v1/accounting/budgets` — sets one target for one category.

    `month` is what picks which kind of target this is: `"YYYY-MM"` sets
    that one month's, and omitting it (`null`) sets the general,
    every-month-alike one. The two are separate rows and neither
    overwrites the other.

    `budget_id` is never taken from the client — derived server-side from
    `(month, category_id, subcategory_id)`, the same natural key
    `PUT /budgets/{budget_id}` used to require the whole list to encode
    implicitly. Posting this twice for the same triple replaces the
    existing target rather than erroring — unlike a category/tag name,
    there's no ambiguity a human needs to confirm here, every triple maps
    to exactly one budget.
    """

    month: Annotated[str, Field(pattern=r"^\d{4}-\d{2}$")] | None = None
    category_id: str = Field(min_length=1)
    subcategory_id: str | None = None
    amount: Money
    currency: CurrencyCode = "USD"


class TransferRuleCreate(BaseModel):
    """Request body for `POST /api/v1/accounting/transfer-rules` — creates one new rule.

    `rule_id` is never taken from the client — derived server-side from
    `(description_contains, account_id, counterparty_account_id)`, the
    rule's own matching criteria, the same way `BudgetUpsert`'s id comes
    from a budget's own `(month, category_id, subcategory_id)`. Posting
    this twice for the same criteria replaces the existing rule rather
    than duplicating it.
    """

    description_contains: str = Field(min_length=1)
    account_id: str | None = None
    counterparty_account_id: str | None = None
    priority: int = 100
    description: str = ""


class TransferRuleUpdate(BaseModel):
    """Request body for `PATCH /api/v1/accounting/transfer-rules/{rule_id}` — updates one existing rule in place.

    Unlike `TransferRuleCreate`, this never changes which rule is being
    edited — the rule stays identified by the `rule_id` path param even if
    `description_contains`/`account_id`/`counterparty_account_id` (its
    matching criteria) change, so an edit never silently becomes a
    different rule. `expected_version` is the rule's own `version` field
    the client last saw — see `db.base.check_and_bump_row_version`, which
    raises a 409 if it no longer matches what's persisted. It's `None`
    (skip the check, last-write-wins) only for an idempotent toggle of the
    `active` flag, where losing the race against a newer flip of the same
    switch is the wanted outcome, not a conflict — see
    `docs/optimistic-concurrency-versioning.md`.
    """

    description_contains: str = Field(min_length=1)
    account_id: str | None = None
    counterparty_account_id: str | None = None
    priority: int
    description: str = ""
    active: bool = True
    excluded_transaction_ids: list[str] = Field(default_factory=list)
    expected_version: int | None = None


class CategoryPatternCreate(BaseModel):
    """Request body for `POST /api/v1/accounting/category-patterns` — creates one new pattern.

    `pattern_id` is derived server-side the same way `TransferRuleCreate`
    derives `rule_id` — from `(description_contains, category_id,
    subcategory_id)`, this pattern's own matching criteria.
    """

    description_contains: str = Field(min_length=1)
    category_id: str = Field(min_length=1)
    subcategory_id: str | None = None
    priority: int = 100


class CategoryPatternUpdate(BaseModel):
    """Request body for `PATCH /api/v1/accounting/category-patterns/{pattern_id}` — updates one in place.

    Unlike `CategoryPatternCreate`, this never changes which pattern is being edited — the pattern
    stays identified by the `pattern_id` path param. `expected_version` is the pattern's own `version`
    the client last saw — see `db.base.check_and_bump_row_version`, which raises a 409 on a mismatch.
    It's `None` (skip the check, last-write-wins) only for an idempotent toggle of the `active` flag,
    the same exemption `TransferRuleUpdate.expected_version` documents.
    """

    description_contains: str = Field(min_length=1)
    category_id: str = Field(min_length=1)
    subcategory_id: str | None = None
    priority: int
    active: bool = True
    expected_version: int | None = None


class GoalCreate(BaseModel):
    """Request body for `POST /api/v1/accounting/goals` — creates one new goal.

    `goal_id`, `color`, and `created_at` are never taken from the client —
    a goal is an arbitrary user record with no natural key two "the same"
    goal would collide on (two goals can validly share a name), so the
    server mints an opaque id, the same way `GoalContributionCreate`
    already does for a contribution; `color` is picked to be distinct
    from every color already in use, the same way
    `store.next_available_color` already works for categories.
    """

    name: str = Field(min_length=1)
    target_amount: Money
    target_currency: CurrencyCode = "USD"
    target_date: datetime


class GoalUpdate(BaseModel):
    """Request body for `PATCH /api/v1/accounting/goals/{goal_id}` — updates one existing goal in place.

    Unlike `GoalCreate`, this never mints a new id or color — the goal
    stays identified by the `goal_id` path param, and `color` is an
    explicit field here (never re-picked) since editing one goal should
    never shuffle the color already showing everywhere else it's used.
    `expected_version` is the goal's own `version` field the client last
    saw — see `db.base.check_and_bump_row_version`, which raises a 409 if
    it no longer matches what's persisted.
    """

    name: str = Field(min_length=1)
    target_amount: Money
    target_currency: CurrencyCode = "USD"
    target_date: datetime
    color: str = Field(min_length=1)
    expected_version: int


class SimulatorScenarioCreate(BaseModel):
    """Request body for `POST /api/v1/accounting/simulator/scenarios` — creates one new saved scenario.

    `scenario_id` is never taken from the client — two scenarios can
    validly share every input field (a user comparing "what if I ran this
    twice"), so there's no meaningful content to derive an id from; the
    server mints an opaque one instead, the same reasoning as `GoalCreate`.
    """

    name: str = Field(min_length=1)
    initial_capital: Money
    monthly_contribution: Money
    horizon_years: Rate
    annual_rate_pct: Rate
    compounding_frequency: CompoundingFrequency = "monthly"
    currency: CurrencyCode = "USD"


class GoalAutomationCreate(BaseModel):
    """Request body for `POST /api/v1/accounting/goal-automations/contributions` — one new scheduled contribution.

    Contribution-shaped: a withdrawal automation carries none of these
    fields, so it has its own, much smaller `WithdrawalAutomationCreate`.

    `automation_id` is server-minted, same reasoning as `GoalCreate`.
    `priority` is never taken from the client either — a newly created
    rule always goes last (one past the current lowest-priority row),
    matching the Goals page's own "append at the end of the ordered list"
    behavior; drag-and-drop reordering goes through
    `PUT /goal-automations/contributions/order`, unaffected by this.
    """

    goal_id: str = Field(min_length=1)
    start_date: date
    frequency: GoalAutomationFrequency
    end_date: date | None = None
    mode: GoalAutomationMode
    value: Money = Field(default=ZERO, json_schema_extra={"default": 0})
    currency: BaseCurrencyOnly = BASE_CURRENCY


class GoalAutomationUpdate(BaseModel):
    """Request body for `PATCH /api/v1/accounting/goal-automations/{automation_id}` — edits one rule in place.

    A single-rule field edit (amount, dates, frequency, mode, goal),
    scoped to its own `automation_id` so it never blanket-reinserts every
    rule. Carries `priority` unchanged (the row keeps its place);
    re-ordering the list is `PUT /goal-automations/contributions/order`,
    which is the only route that assigns priorities. No
    `expected_version`: like a budget cell, an edit of one rule is
    last-write-wins on that rule (see
    `docs/optimistic-concurrency-versioning.md`).

    Contribution-shaped for the same reason `GoalAutomationCreate` is —
    there is nothing on a withdrawal automation a `PATCH` could edit.
    """

    goal_id: str = Field(min_length=1)
    start_date: date
    frequency: GoalAutomationFrequency
    end_date: date | None = None
    mode: GoalAutomationMode
    value: Money = Field(default=ZERO, json_schema_extra={"default": 0})
    currency: BaseCurrencyOnly = BASE_CURRENCY
    priority: int


class WithdrawalAutomationCreate(BaseModel):
    """Request body for `POST /api/v1/accounting/goal-automations/withdrawals` — puts one goal in the drawdown order.

    A withdrawal automation is nothing but its goal and its place in that
    order, so `goal_id` is the entire body — no schedule, no amount, no
    currency (see `models.GoalAutomation`'s own field notes).

    `automation_id` is *derived* rather than minted, unlike
    `GoalAutomationCreate`: a goal appears at most once in the drawdown
    order, so `repositories.planning.withdrawal_automation_id` is the
    natural key. `priority` appends, exactly as it does for a contribution.
    """

    goal_id: str = Field(min_length=1)


class AutomationOrder(BaseModel):
    """Request body for `PUT /api/v1/accounting/goal-automations/{contributions,withdrawals}/order`.

    Every automation id currently persisted for that direction, in the
    order they should run in — and nothing else. The narrowness is the
    whole point: the whole-list `PUT` this replaced took full automation
    rows, so a drag-to-reorder could smuggle a field edit, an insertion or
    a deletion past the per-automation routes that exist for those. Ids
    alone, checked against the set already stored, can express a
    reordering and nothing more.

    No `priority` field: the server reads it off list position, so a
    submitted order and the stored priorities cannot disagree.
    """

    automation_ids: list[str]


class OtherAssetCreate(BaseModel):
    """Request body for `POST /api/v1/accounting/other-assets` — creates one new manually-entered asset.

    `asset_id` is server-minted, same reasoning as `GoalCreate` — two
    assets can validly share a name (e.g. two rental properties).
    """

    name: str = Field(min_length=1)
    value: Money
    currency: CurrencyCode = "USD"
    note: str = ""


class CategoryRenamePreviewResponse(BaseModel):
    """Response body for `GET /categories/{category_id}/rename-preview`."""

    will_merge: bool
    target_name: str | None
    budgets_to_delete: list[BudgetToDeletePreview] = []


class CategoryCreate(BaseModel):
    """Request body for `POST /categories` — a new top-level category."""

    name: str = Field(min_length=1)
    classification: CategoryClassification
    color: str


class SubcategoryCreate(BaseModel):
    """Request body for `POST /categories/{parent_id}/subcategories` — a new subcategory."""

    name: str = Field(min_length=1)
    color: str


class CategoryDeletePreviewResponse(BaseModel):
    """Response body for `GET /categories/{category_id}/delete-preview`.

    `posting_count` includes every subcategory's postings too, when
    `category_id` is a top-level category (deleting one takes its
    subcategories with it — see `store.category_ids_to_delete`).
    """

    posting_count: int


class CategoryDeleteResponse(BaseModel):
    """Response body for `DELETE /categories/{category_id}`."""

    categories: dict[str, Category]
    uncategorized_posting_count: int


class TagCreate(BaseModel):
    """Request body for `POST /tags` — a new tag."""

    name: str = Field(min_length=1)


class TagRenameRequest(BaseModel):
    """Request body for `POST /tags/{tag_id}/rename`."""

    name: str


class TagRenameResponse(BaseModel):
    """Response body for `POST /tags/{tag_id}/rename`."""

    tags: dict[str, Tag]
    merged: bool


class TagRenamePreviewResponse(BaseModel):
    """Response body for `GET /tags/{tag_id}/rename-preview`."""

    will_merge: bool
    target_name: str | None


class ProjectionPoint(BaseModel):
    """One projected month's balance, for `GET /simulator/project` — see `dashboard.simulator.ProjectionPoint`."""

    month: int
    balance: float
    contributions_to_date: float


class AccountCreate(BaseModel):
    """Request body for `POST /api/v1/accounting/accounts` — everything but the server-generated `account_id`."""

    name: str = Field(min_length=1)
    kind: AccountKind
    institution: str = Field(min_length=1)
    currency: CurrencyCode
    last_four: str | None = None
    parent_account_id: str | None = None
    broker_connection_id: uuid.UUID | None = None
    meta: dict[str, str] = Field(default_factory=dict)


class AccountUpdate(BaseModel):
    """Request body for `PUT /api/v1/accounting/accounts/{account_id}`.

    `institution`, `kind`, and `currency` may only differ from the
    account's current values while it has no postings yet — enforced in
    `put_account`, not here, since that check needs the ledger. `closed`
    isn't edited here — see `close_account`/`reopen_account`, which pair it
    with recording where a closed account's remaining balance went.
    `broker_connection_id` is never locked — it only ever changes which
    value an `external_investment` account shows (see
    `dashboard.net_worth`), never what it has already recorded, so it's
    free to toggle regardless of postings. `last_four` is never locked
    either, for the same reason: it never affects identity or any stored
    history.
    """

    name: str
    institution: str
    kind: AccountKind
    currency: CurrencyCode
    last_four: str | None = None
    broker_connection_id: uuid.UUID | None = None
    meta: dict[str, str] = Field(default_factory=dict)


class AccountCloseRequest(BaseModel):
    """Request body for `POST /api/v1/accounting/accounts/{account_id}/close`."""

    transfers: list[ManualTransfer] = Field(default_factory=list)


class AccountCloseResponse(BaseModel):
    """Response body for `POST /api/v1/accounting/accounts/{account_id}/close`."""

    account: Account
    manual_transfers: list[ManualTransfer]


class DetectRequest(BaseModel):
    """Request body for `POST /api/v1/accounting/detect`."""

    header: list[str]
    filename: str
    first_data_row: dict[str, str] | None = None


class DetectedAccount(BaseModel):
    """A best-guess bank and account kind for one uploaded CSV — see `importers.detect`."""

    institution: str
    account_kind: AccountKind


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
    amount: Money
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
    amount: Money
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


UNCATEGORIZED = "__uncategorized__"
"""The category filter's entry for "no category at all", which is not a category id.

Part of the wire contract, not an implementation detail of either side. A
multi-select filter arrives as a list of query parameters and a list cannot
hold a null, so "match the rows with no category" needs a value — and the
alternative, a separate `include_uncategorized` boolean beside every
multi-select, would be four more parameters saying the same thing four times.
"""

NO_SUBCATEGORY = "__no_subcategory__"
"""The subcategory filter's entry for "no subcategory" — see `UNCATEGORIZED`."""

CONFIRMED = "__confirmed__"
"""The pending filter's entry for "not a suggestion", i.e. a null `pending_source` — see `UNCATEGORIZED`."""

TransferFlag = Literal["rule", "excluded", "manual", "none"]
"""How a transaction's transfer status currently reads, across every mechanism that can set one.

Not mutually exclusive except pairwise: a posting is either mid-transfer via
a rule or manually, or a plain non-transfer, while `excluded` is a separate
historical fact that can be true alongside either.
"""

PostingSortField = Literal[
    "posted_at", "account_id", "description", "amount", "category_id", "subcategory_id", "tag_ids"
]
"""Which resolved column `GET /postings` orders by — one per sortable column on the transactions table."""


class PostingFilters(BaseModel):
    """The transactions screen's filter bar, as the server evaluates it.

    Every predicate here reads a *resolved* value, which is why this could
    not exist before the projection did: the category a redirect or an
    override rewrote, the account a rule repointed, the description a merge
    rewrote, the amount a split changed. See
    `accounting.db.projection.ResolvedPosting`.

    Used in two places, and deliberately the same model in both: as query
    parameters on `GET /postings`, and in the body of the filter-shaped bulk
    actions (`POST /postings/validate-pending`,
    `POST /pattern-suggest-category/bulk`). A bulk action that took its own
    filter shape could disagree with the list the user is looking at, which
    is the whole failure this endpoint exists to prevent.

    Each multi-select carries its own `_exclude` flag rather than a signed
    value list, matching the filter bar's own controls: an empty list means
    "no restriction", and `_exclude` inverts whatever the list selects.
    """

    search: str = Field(default="", description="Case-insensitive substring of the resolved description.")
    account: str | None = Field(default=None, description="One account's natural key.")
    account_exclude: bool = False
    categories: list[str] = Field(
        default_factory=list, description=f"Category natural keys; `{UNCATEGORIZED}` matches rows with none."
    )
    categories_exclude: bool = False
    subcategories: list[str] = Field(
        default_factory=list, description=f"Subcategory natural keys; `{NO_SUBCATEGORY}` matches rows with none."
    )
    subcategories_exclude: bool = False
    tags: list[str] = Field(default_factory=list, description="Tag natural keys; a row matches if it carries any.")
    tags_exclude: bool = False
    month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$", description="A single `YYYY-MM`.")
    start: date | None = Field(default=None, description="First day to include, inclusive.")
    end: date | None = Field(default=None, description="Last day to include, inclusive.")
    pending: list[str] = Field(
        default_factory=list,
        description=f"`ai`, `pattern`, or `{CONFIRMED}` for a row carrying no unvalidated suggestion.",
    )
    pending_exclude: bool = False
    transfer_flags: list[TransferFlag] = Field(default_factory=list)
    transfer_flags_exclude: bool = False
    income_expense: Literal["income", "expense"] | None = Field(
        default=None, description="Restrict to real income or real expense legs, by sign."
    )
    categorized: Literal["categorized", "uncategorized"] | None = None
    needs_categorizing: bool = Field(
        default=False,
        description=(
            "The 'Needs categorizing' tab: a real income/expense leg that is uncategorized, still carries an "
            "unvalidated suggestion, or sits under a parent category whose subcategory has not been picked."
        ),
    )


class PostingQuery(PostingFilters):
    """`GET /postings`' whole query string: the filter bar, plus the sort and the page window.

    One model rather than a model beside four loose parameters, and that is
    forced rather than preferred: FastAPI expands a Pydantic
    query-parameter model into its fields **only when it is the sole query
    parameter of the endpoint**. Declare one other — a `limit`, or even a
    keyword-only marker in the signature — and it silently stops expanding,
    takes the model as a scalar query parameter named after the argument,
    and answers 422 to every request with the model's own parameters absent
    from the schema.

    Inheriting rather than composing keeps the bulk actions honest: they
    take `PostingFilters` itself, so they cannot be handed a sort or a page
    window, and cannot drift from the filter this page was built with.
    """

    sort: PostingSortField = Field(default="posted_at", description="Which resolved column to order by.")
    descending: bool = Field(default=True, description="Order high-to-low. Nulls sort last either way.")
    limit: int = Field(
        default=PAGE_LIMIT_DEFAULT, ge=1, description="How many transactions to return. Clamped to PAGE_LIMIT_MAX."
    )
    offset: int = Field(default=0, ge=0, description="How many transactions to skip.")


class PostingPageCounts(BaseModel):
    """Every count the transactions screen shows about the whole filter, none of which is the page's size.

    `total` on the page envelope counts `window_unit`s — transactions, since
    that is what a page is cut by. The figure beside the table counts
    *rows*, because that is what the table lists: a split transaction
    contributes one transaction and several rows.

    The screen used to derive the row count client-side and label it
    "transactions", which was wrong in exactly the case a split makes the two
    differ. Both are returned rather than one being silently redefined — the
    same choice `http_api.pagination.Page.window_unit` exists to make
    explicit.

    Every field here describes the filter, never the page. That is the whole
    reason they are on the wire: a button whose label counts what happens to
    be rendered, while the action behind it resolves the filter server-side,
    reports a number that is not the number of rows it affects.
    """

    matched_transactions: int
    """How many transactions the filter matches — the same number as the page's `total`."""
    matched_postings: int
    """How many rows the filter matches, across every page."""
    needs_categorizing: int
    """How many matched rows still want a category, ignoring the `needs_categorizing` filter itself.

    The "Needs categorizing" tab's badge, and the size of the set the bulk
    categorizers would act on. Deliberately computed with that one predicate
    lifted, so the badge reads the same whichever tab is open."""
    pending: int
    """How many matched rows carry an unvalidated AI or pattern suggestion.

    What "Validate selection" resolves — `POST /postings/validate-pending`
    takes the filter, so this is the size of the set that button acts on. It
    used to be counted off the rendered rows, which was the same number only
    while the client held every row the filter matched."""
    pending_selected: int
    """How many of `pending` are currently checked, and so will be accepted rather than reverted.

    `pending_selected` is a stored field of the override, not client state,
    so this is a fact about the filter and not about what a page happens to
    have painted. The two together are the "(checked/pending)" on the
    button; the checkbox in the table header toggles one page's worth of
    them and says so."""


class PostingRow(Posting):
    """One posting as displayed on the Transactions page — a `Posting` plus its current resolution state.

    The extra fields are display-only, bolted onto the resolved ledger by
    `get_postings` itself rather than stored on the posting — see
    `ledger.pending`/`ledger.categorization.resolved_transfer_rule_ids_by_transaction`/
    `ledger.transfers.apply_transfer_links`. `resolved_by_transfer_rule_id`
    is only ever set for a rule that *directly* repointed this posting's
    placeholder (a safe, non-`IMPORTABLE_ACCOUNT_KINDS` counterparty) —
    never for one a `TransferLink` (manual or rule-found) resolved
    instead, which shows up via `is_linked_transfer`/`linked_transaction_id`/
    `transfer_link_source` regardless of which account this posting's own
    placeholder leg still points at. `manual_transfer_override_posting_id`
    is set (to the posting actually carrying the override) on both legs of
    a transaction whose placeholder was directly repointed via a manual
    `ManualOverride.account_id`, the same way `resolved_by_transfer_rule_id`
    is set on both legs of a rule-repointed one — and since a manual
    override is applied *after* rules in the resolution pipeline (see
    `ledger.resolution.apply_overlays`) and so always wins if
    both somehow apply to the same transaction, `get_postings` never sets
    `resolved_by_transfer_rule_id` on a transaction that also has one of
    these, so the two are mutually exclusive here — never "via rule" when
    a manual override is what actually decided the account shown.
    """

    pending_source: PendingSuggestionSource | None = None
    pending_selected: bool = True
    resolved_by_transfer_rule_id: str | None = None
    manual_transfer_override_posting_id: str | None = None
    is_linked_transfer: bool = False
    linked_transaction_id: str | None = None
    transfer_link_source: TransferLinkSource | None = None
    is_real_income_expense: bool = False
    """Whether this leg is real income or a real expense, rather than one side of an internal transfer.

    Mirrors `dashboard.income_statement.real_income_expense_legs`: not on a
    virtual placeholder account, its transaction has a leg that is, and the
    transaction is not a confirmed transfer link. On the wire because the
    transactions screen reads it per row — for the "needs categorizing"
    state, the income/expense filter, and whether a rule-repointed row shows
    a transfer badge or a "via rule" tag — and used to derive it by scanning
    the whole ledger client-side."""
    is_excluded_from_rule: bool = False
    """Whether this transaction is opted out of at least one transfer rule.

    A historical fact rather than a transfer classification: it can be true
    alongside a row that is currently a transfer and one that is not, which
    is why it is its own filter option rather than folded into either."""
    linked_leg: LinkedLeg | None = None
    """The partner transaction's real leg, when this row is a confirmed transfer.

    The one thing the transfer badge needs that is not on this row or its
    siblings, and the reason it is joined at read time rather than stored:
    denormalising the partner onto this row would mean every change to the
    partner had to find and rewrite it, which is a second staleness axis on
    top of the one the projection already has."""


class TransactionLegsRequest(BaseModel):
    """Which transactions to look the real leg up for — `POST /postings/legs`' body."""

    transaction_ids: list[str] = Field(default_factory=list, max_length=PAGE_LIMIT_MAX)


class LinkedLeg(BaseModel):
    """The other side of a confirmed transfer, as its badge and detail popup need it.

    Deliberately not a whole `PostingRow`: the partner is not on the page and
    is not rendered as a row, so returning one would invite a client to treat
    it as if it were.
    """

    transaction_id: str
    account_id: str
    description: str
    posted_at: datetime
    amount: Money
    currency: CurrencyCode


class PostingPage(Page[PostingRow, Literal["transaction"]]):
    """One page of resolved postings, cut by transaction, ordered by whatever the client asked to sort on.

    `items` holds every leg of every transaction on the page — including the
    placeholder legs the table itself never renders, because the transfer
    badge and "mark as transfer" both read them. A split transaction
    contributes more rows than legs it was imported with. See
    `repositories.projection.filtered_page` for why the cut is by transaction
    rather than by matching row, and how a transaction with several matching
    rows takes its place in the order.

    `total` counts transactions, as `window_unit` says. `counts` carries the
    two other numbers the screen shows, which are genuinely different
    numbers — see `PostingPageCounts`.
    """

    counts: PostingPageCounts
    """Everything the filter matches, ignoring this page's window."""


class LedgerExportPage(Page[Posting, Literal["posting"]]):
    """One page of the raw ledger, as exported, cut by posting.

    `items` is oldest first, exactly as imported. The raw export applies no
    overlay, so nothing here needs a transaction's legs kept together — which
    is the whole reason its window unit differs from `PostingPage`'s. See
    `repositories.ledger.load_ledger_page`.
    """


class PostingMergeUpsert(BaseModel):
    """Request body for `POST /api/v1/accounting/posting-merges` — records one duplicate-resolution decision.

    `merge_id` is never taken from the client — derived server-side from
    `kept_transaction_id`, since a transaction can only ever be the kept
    side of one merge decision at a time. Posting this twice for the same
    `kept_transaction_id` replaces the existing decision.
    """

    kept_transaction_id: str = Field(min_length=1)
    duplicate_transaction_ids: list[str] = Field(min_length=1)
    description: str | None = None


class TransferLinkCreate(BaseModel):
    """Request body for `POST /api/v1/accounting/transfer-links`.

    Confirms two transactions as one transfer's two sides.

    `link_id`/ordering are never taken from the client — derived
    server-side from the two ids sorted once (see
    `ledger.transfers.make_transfer_link`), so confirming the same pair
    from either side is idempotent.
    """

    transaction_id_a: str = Field(min_length=1)
    transaction_id_b: str = Field(min_length=1)


class GoalContributionCreate(BaseModel):
    """Request body for `POST /api/v1/accounting/goal-contributions` — records one new dated allocation.

    `contribution_id` is never taken from the client — unlike a budget's
    `(month, category_id)`, a contribution is an arbitrary event with no
    natural key to derive one from, so the server generates an opaque one.

    `account_id` records which account the allocated money actually sits
    in; it is stored and echoed back, and read by nothing else yet — see
    `models.GoalContribution`.
    """

    goal_id: str = Field(min_length=1)
    date: datetime
    amount: Money
    currency: CurrencyCode = "USD"
    note: str = ""
    account_id: str | None = None
    source_posting_id: str | None = None
    origin: GoalContributionOrigin = "manual"
    edited: bool = False


class GoalContributionUpdate(BaseModel):
    """Request body for `PUT /api/v1/accounting/goal-contributions/{contribution_id}` — replaces one contribution.

    Every field is required, mirroring `PUT /accounts/{account_id}` — the
    caller already merges its patch into the existing row client-side
    before sending, so there's no partial-update ambiguity to resolve here.
    """

    goal_id: str = Field(min_length=1)
    date: datetime
    amount: Money
    currency: CurrencyCode = "USD"
    note: str = ""
    account_id: str | None = None
    source_posting_id: str | None = None
    origin: GoalContributionOrigin = "manual"
    edited: bool = False


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
    """Request body for `PUT /api/v1/accounting/settings/llm`.

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


class FilteredBulkRequest(BaseModel):
    """The set a bulk action applies to, named by the filter that produced it rather than by a list of ids.

    Both bulk actions used to take `posting_ids` — "always exactly the
    caller's current filtered view", which was true only while the client
    held the whole ledger and could enumerate that view. It cannot now, and
    the honest fix is not to make it page through the collection to rebuild
    a list: it is to send the filter and let the server resolve the set
    **inside the same transaction as the write**, so nothing can shift
    underneath the operation between the two.

    The filter is exactly `GET /postings`' own, so the set a bulk action
    touches is by construction the set the screen is showing. A sort and a
    page window are deliberately absent: a bulk action is not scoped to a
    page (see `matched` on each result, which is what the button reports).
    """

    filters: PostingFilters = Field(default_factory=PostingFilters)


class BulkSuggestResult(BaseModel):
    """Response body for `POST /postings/pattern-suggest-category/bulk`."""

    matched: int
    """How many rows the filter resolved to — what the action was applied over."""
    applied: int
    """How many of them actually got a staged suggestion."""


class ValidatePendingResult(BaseModel):
    """Response body for `POST /postings/validate-pending`."""

    matched: int
    """How many rows the filter resolved to — what the action was applied over.

    Returned so the UI reports what happened rather than assuming it acted
    on what it last rendered, which is no longer the same set."""
    accepted: int
    reverted: int


class TransferSuggestion(BaseModel):
    """One likely internal transfer no rule has resolved yet.

    See `ledger.transfers.find_unmatched_transfer_candidates`.

    Carries both the postings it matched and the transactions they belong
    to. The match is between postings, but the action a user takes on it —
    `POST /transfer-links` — names transactions, so a client without the two
    `transaction_id`s has to find them itself. The one that had to used to
    hold the entire resolved ledger to build a two-entry lookup.
    """

    account_id: str
    posting_id: str
    transaction_id: str
    posted_at: datetime
    description: str
    other_account_id: str
    other_posted_at: datetime
    other_posting_id: str
    other_transaction_id: str
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
    """Request body for `PUT /api/v1/accounting/dismissed-suggestions/{suggestion_id}`.

    No `suggestion_id`: the path carries it. Keeping a copy in the body
    would give one request two places to name the same thing, and the
    handler would have to decide which wins when they disagree.
    """

    kind: Literal["transfer", "duplicate"]
    description: str


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


class NetWorthOtherAssetRow(BaseModel):
    """One manually-entered net-worth line, as an analytics figure rather than as the stored entity.

    A near-copy of `entities.OtherAsset` differing in exactly one field, and
    the difference is the point: `value` is a `float` here where the entity
    holds an exact `Money`. This response's four totals are floats — they are
    summed and currency-converted through the analytics boundary (see
    `ledger.frame`'s T1) — so embedding the exact entity as a summand made one
    object claim both families at once, with no way for a reader to tell which
    figure was safe to add to which.

    The exact value has an address of its own: `GET /store` and
    `GET /other-assets/{asset_id}` both return the entity. Read those to edit
    an asset; read this to chart one.
    """

    asset_id: str
    name: str
    value: float
    currency: CurrencyCode
    note: str


class NetWorthSummary(BaseModel):
    """Assets, liabilities, and net worth as of one date — see `dashboard.net_worth.NetWorthSummary`.

    Every money field here is analytics: a `float`, summed and converted at
    `display_currency`'s rate for the date. Nothing in this object is the exact
    stored value of anything, `other_assets` included — see
    `NetWorthOtherAssetRow`, and `docs/http-api-contract.md` for the split.
    """

    as_of: date
    display_currency: CurrencyCode
    assets: float
    liabilities: float
    other_assets_total: float
    net_worth: float
    accounts: list[NetWorthAccountRow]
    other_assets: list[NetWorthOtherAssetRow]


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
    average_previous_months_cumulative: float | None


class BudgetComparisonRow(BaseModel):
    """One category (or subcategory)'s budget target next to its actual spend.

    Both figures are analytics `float`s. `actual` cannot be anything else — it
    is a converted sum over the resolved ledger — and `budgeted` used to be an
    exact `Money` sitting right beside it, so the row invited a subtraction
    between two numbers from different families and presented the difference
    as if it meant something exact. It does not: the answer is only ever as
    good as `actual`.

    The exact target is `entities.Budget.amount`, returned by `GET /store` and
    `GET /budgets/{budget_id}`. See `dashboard.budgets.BudgetComparisonRow` and
    `docs/http-api-contract.md`.
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
