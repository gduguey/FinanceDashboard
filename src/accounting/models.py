"""Canonical schemas for the accounting domain: what an account, a category, and a posting are.

A `Posting` is the accounting equivalent of `trades.models.LedgerEvent`: one
immutable row of an append-only record, field names doubling as column
names. Where the investing ledger's rows are self-contained facts about one
symbol, a posting is only ever half of an economic event — it always has at
least one sibling posting (sharing `transaction_id`) whose amounts, once
converted to a common currency, sum to zero. See `ACCOUNTING_PLAN.md` for
why that invariant is enforced by callers rather than by a single-row model,
and why a transaction can have more than two postings (a paycheck landing in
two accounts at once, split further into wage and reimbursement legs).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator

AccountKind = Literal[
    "checking",
    "savings",
    "credit_card",
    "vault",
    "cash",
    "loan",
    "income_source",
    "expense_payee",
    "external_investment",
    "other_asset",
]
"""What an account represents. `checking`/`savings`/`credit_card`/`loan`/
`cash` are real accounts you hold. `vault` is a named sub-balance of one
parent savings account (a SoFi Vault, not an account of its own at the
bank). `income_source`/`expense_payee` are virtual counterparties — every
withdrawal needs somewhere the money went, every deposit needs somewhere it
came from, and most of the time that "somewhere" is a payee or payer, not
another account you hold. `external_investment` is a placeholder whose
balance is deliberately never computed here — see `dashboard.net_worth`.
`other_asset` is a manually-entered net-worth line (property, etc.) with no
transaction history at all.
"""

CategoryClassification = Literal["income", "expense"]
"""Whether a category is money coming in or money going out. Both share one
tree (a category can have subcategories regardless of which side it's on),
but the split matters for reporting — an income statement's two columns —
and for which categories even make sense to show when categorizing a
posting whose amount is positive vs. negative.
"""

CurrencyCode = Literal["USD", "EUR"]
"""Every currency this app knows how to hold money in or convert between."""


class Currency(BaseModel):
    """One supported currency's display metadata — never a value on its own, only ever attached to one."""

    model_config = ConfigDict(frozen=True)

    code: CurrencyCode
    symbol: str = Field(min_length=1)
    decimal_places: int = 2


SUPPORTED_CURRENCIES: dict[CurrencyCode, Currency] = {
    "USD": Currency(code="USD", symbol="$", decimal_places=2),
    "EUR": Currency(code="EUR", symbol="€", decimal_places=2),
}
"""Every currency a `currency: CurrencyCode` field elsewhere in this package
can hold — the registry `Currency` is the single record of, so a symbol or
decimal-places convention is only ever declared once. Adding a currency is
exactly two edits: a new arm on `CurrencyCode`, and a new entry here; every
rate lookup, chart, and dropdown is driven by this dict, never a hardcoded
pair (see `ledger.currency.convert` and `market_data.exchange_rates`).
"""

BASE_CURRENCY: CurrencyCode = "USD"
"""The currency every exchange rate in this app is expressed relative to.

A rate table is always `dict[CurrencyCode, float]` mapping a currency code
to how many `BASE_CURRENCY` units one unit of it is worth (`BASE_CURRENCY`
itself always maps to `1.0`) — converting between any two supported
currencies is always a trip through this one shared base, never a direct
N² table of every pair. See `market_data.exchange_rates` (fetches this from
one currency at a time relative to `BASE_CURRENCY`) and `ledger.currency.convert`.
"""


class Account(BaseModel):
    """One place money can sit or be attributed to — a real account, a vault, or a virtual counterparty.

    `parent_account_id` is only set for a `vault`, pointing at the savings
    account it's a named sub-balance of. `external_ref` is only set for the
    `external_investment` kind, naming where its value actually comes from
    (currently always `"trades"`, meaning `trades.dashboard.overview_cards`)
    since this account's balance is never derived from its own postings.
    `meta` holds facts about the account itself rather than any one
    posting — currently just `apy_pct`, the interest rate last seen on a
    statement, carried here because it describes the account's terms, not
    a single transaction. `closed` marks a real-world account that no
    longer exists at its institution — its transaction history stays
    exactly as imported (never deleted), it just stops being offered as a
    destination for new imports or transfers.
    """

    model_config = ConfigDict(frozen=True)

    account_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: AccountKind
    institution: str = Field(min_length=1)
    currency: CurrencyCode
    parent_account_id: str | None = None
    external_ref: str | None = None
    meta: dict[str, str] = Field(default_factory=dict)
    closed: bool = False


class Category(BaseModel):
    """One node in the two-level category tree: a top-level category, or a subcategory of one.

    `parent_category_id` is `None` for a top-level category and points at
    one for a subcategory — never more than one level deep. A subcategory
    is expected (not enforced here) to share its parent's `classification`
    but have its own distinct `color`, never repeated by a sibling
    subcategory or any other category — see `store.next_available_color`,
    the single place a color is ever assigned.
    """

    model_config = ConfigDict(frozen=True)

    category_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    classification: CategoryClassification
    parent_category_id: str | None = None
    color: str = Field(min_length=1)


class Tag(BaseModel):
    """A cross-cutting label — a trip, a move, an event — independent of the category tree.

    Where a category answers "what kind of spend is this," a tag answers
    "what's it part of": a single trip involves food, transport, and
    lodging, each its own category, all sharing one tag.
    """

    model_config = ConfigDict(frozen=True)

    tag_id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class TransferRule(BaseModel):
    """A user-maintained trigger/action pair for automatically resolving a posting's counterparty and category.

    Named specifically for what it's for — linking two of your own
    accounts together as an internal transfer — to avoid reading as the
    same thing as a `CategoryPattern` below, which only ever suggests a
    category and never resolves a counterparty.

    Every field on the trigger side must match for the rule to apply
    (`description_contains` is a case-insensitive substring check;
    `account_id`, when set, restricts the rule to postings on that one
    account). `counterparty_account_id`, when set, must name an *existing*
    account (real or virtual) — a rule only ever repoints a posting's
    placeholder counterparty at an account already known to the store,
    never creates one; add the counterparty account first (see
    `Account`), then reference it here. This applies to a vault the same
    as anything else: create it as an ordinary `vault`-kind account
    (parented at its savings account) first, then write one rule per
    vault name. `priority` breaks ties when more than one rule matches;
    the lowest number wins. `description` is a free-text note on what the
    rule is actually for — purely for a human re-reading the rule list
    later, never read by the matching logic. `active` lets a rule be
    switched off without deleting it — an inactive rule is skipped by
    matching entirely, as if it weren't in the list at all.
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str = Field(min_length=1)
    description_contains: str = Field(min_length=1)
    account_id: str | None = None
    category_id: str | None = None
    subcategory_id: str | None = None
    counterparty_account_id: str | None = None
    priority: int = 0
    description: str = ""
    active: bool = True


class CategoryPattern(BaseModel):
    """A user-maintained description-match pattern that *suggests* a category — never applies one silently.

    Deliberately distinct from `TransferRule`: a `TransferRule` resolves a
    posting's counterparty/category automatically as part of every ledger
    read, with no confirmation step. A `CategoryPattern` only ever produces a
    suggestion the user must explicitly accept or reject (see
    `ledger.pending`, `pending_source="pattern"` on `ManualOverride`) —
    the same confirm-before-it-sticks flow an AI suggestion goes through,
    just keyed off an explicit substring match instead of an LLM call.
    `priority` breaks ties the same way `TransferRule.priority` does: the lowest
    number wins. `active` lets a pattern be switched off without deleting
    it — an inactive pattern is skipped by matching entirely.
    """

    model_config = ConfigDict(frozen=True)

    pattern_id: str = Field(min_length=1)
    description_contains: str = Field(min_length=1)
    category_id: str = Field(min_length=1)
    subcategory_id: str | None = None
    priority: int = 0
    active: bool = True


class OtherAsset(BaseModel):
    """A manually-entered net-worth line with no transaction history — property, a car, etc.

    Unlike everything else in this module, this is a preference-like
    record a user types in directly rather than something derived from a
    posting; it lives in the same store for convenience, not because it's a
    fact about what happened. `value` is in `currency`, not necessarily
    the display currency any given page is showing totals in — conversion
    happens where it's aggregated, in `dashboard.net_worth`.
    """

    model_config = ConfigDict(frozen=True)

    asset_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    value: float
    currency: CurrencyCode = "USD"
    note: str = ""


class OpeningBalance(BaseModel):
    """The balance a real account already had the day before its postings start, e.g. a vault opened outside this app.

    A `Posting` can only ever reflect money moving *through* the ledger, so
    a brand-new account with real money already in it (added here rather
    than discovered via import) would otherwise show a $0 balance until its
    first posting. This is added on top of the posting-derived balance in
    `dashboard.net_worth`, contributing nothing for any `as_of` before
    `as_of_date` — the account simply didn't exist to this ledger yet.
    """

    model_config = ConfigDict(frozen=True)

    account_id: str = Field(min_length=1)
    amount: float
    as_of_date: datetime


class ManualTransfer(BaseModel):
    """A user-recorded transfer between two of their own accounts, never derived from an import.

    Every other posting in this ledger traces back to a real bank
    statement row (see `store`'s module docstring) — this is the one
    deliberate exception, for the one case no statement can ever cover:
    moving out whatever's left in an account right before closing it (see
    `api.close_account`). `from_amount`/`to_amount` are each in that side's
    own account's currency and entered independently rather than via a
    stored exchange rate, so a transfer between two different currencies
    is exactly what the user says left one side and arrived on the other,
    not a computed conversion.
    """

    model_config = ConfigDict(frozen=True)

    transfer_id: str = Field(min_length=1)
    date: datetime
    from_account_id: str = Field(min_length=1)
    to_account_id: str = Field(min_length=1)
    from_amount: float = Field(gt=0)
    to_amount: float = Field(gt=0)
    description: str = ""


class Budget(BaseModel):
    """One month's spending target for one top-level expense category, or one of its subcategories.

    `category_id` is always the top-level category, matching
    `Posting.category_id`. `subcategory_id`, when set, scopes the target to
    that one subcategory's actual spend alone rather than the whole
    category's — matching `Posting.subcategory_id` — so a category and one
    of its subcategories can each carry their own independent target for
    the same month.

    Actual spend against a budget is never stored here or on the postings
    it covers — a posting's own `category_id`/`subcategory_id` already
    determines which budget it counts against for whichever month it
    landed in, so "actual" is always computed fresh from
    `dashboard.income_statement.category_totals`, the same on-demand way
    an account's balance comes from summing its postings rather than a
    cached figure that could drift out of sync.
    """

    model_config = ConfigDict(frozen=True)

    budget_id: str = Field(min_length=1)
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    category_id: str = Field(min_length=1)
    subcategory_id: str | None = None
    amount: float
    currency: CurrencyCode = "USD"


class GeneralBudget(BaseModel):
    """A category's (or subcategory's) spending target applied to every month alike.

    Independent of any per-month `Budget` rows — the Budget page's
    "General" mode edits these; its "Per month" mode
    edits `Budget` instead — the two are stored completely separately (see
    `store.AccountingStore`), never merged or falling back to one
    another, so switching modes never silently overwrites the other.
    """

    model_config = ConfigDict(frozen=True)

    category_id: str = Field(min_length=1)
    subcategory_id: str | None = None
    amount: float
    currency: CurrencyCode = "USD"


class SimulatorScenario(BaseModel):
    """A saved set of inputs to the compound-interest projector (see `dashboard.simulator.project`).

    Every field here is one of the projector's five inputs, plus a name to
    tell saved scenarios apart — nothing here is itself computed; the
    projection is always run fresh from these inputs, never cached.
    """

    model_config = ConfigDict(frozen=True)

    scenario_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    initial_capital: float
    monthly_contribution: float
    horizon_years: float
    annual_rate_pct: float
    compounding_frequency: Literal["annually", "monthly", "daily"] = "monthly"
    currency: CurrencyCode = "USD"


class Goal(BaseModel):
    """A savings target — its balance is never stored here, only derived from its `GoalContribution`s.

    See `dashboard.goals.goal_balance`: the balance at any point in time
    is always the running sum of contributions up to that date, computed
    fresh, the same way an account's balance is never a cached figure
    (see `Budget`'s own docstring for the same reasoning applied to
    spending).
    """

    model_config = ConfigDict(frozen=True)

    goal_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    target_amount: float
    target_currency: CurrencyCode = "USD"
    target_date: datetime
    color: str = Field(min_length=1)
    created_at: datetime


GoalContributionOrigin = Literal["manual", "automation"]


class GoalContribution(BaseModel):
    """One dated, signed allocation into (or withdrawal from) a goal — the only thing a goal's balance derives from.

    `date` is the real day the allocation happened — never a month
    bucket; "this month's contributions" is always a display-time filter
    over these rows, never a separately-stored monthly figure (see
    `dashboard.goals`). A positive `amount` is money going into the goal,
    negative is a withdrawal (see `ledger.goal_automations` for the
    withdrawal-automation trigger). `source_posting_id`, when set, links
    to the real bank transfer this corresponds to, purely for
    traceability — never read by any balance or unallocated computation,
    per the user's own spec: "Never used in math." `origin` distinguishes
    a manually-entered contribution from one an automation wrote; `edited`
    flags an automation-written contribution the user has since hand-edited,
    so the ledger table can show it's no longer purely automatic.
    """

    model_config = ConfigDict(frozen=True)

    contribution_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    date: datetime
    amount: float
    currency: CurrencyCode = "USD"
    note: str = ""
    source_posting_id: str | None = None
    origin: GoalContributionOrigin = "manual"
    edited: bool = False


RecurringAdditionMode = Literal["fixed_amount", "percent_of_unallocated", "remainder"]
RecurringAdditionFrequency = Literal["daily", "weekly", "biweekly", "monthly"]


class RecurringAddition(BaseModel):
    """One ordered rule for automatically allocating unallocated money into a goal on a recurring schedule.

    `priority` is the manually-set execution order (lowest first) the
    Goals page's drag-and-drop reorders — a `fixed_amount` row funded
    first can leave less (or nothing) for a lower-priority one when
    unallocated money runs out; see `ledger.goal_automations.run_recurring_additions`.
    `mode="remainder"` ("whatever's left after all the others") is only
    ever valid on the single lowest-priority row — enforced by the API
    that persists this list, not by this model.

    The schedule itself is `start_date` + `frequency`, optionally bounded
    by `end_date` — see `ledger.goal_automations.next_recurring_occurrence`
    for how a due date is derived from these. For `frequency="monthly"`,
    the day of month is `start_date`'s own day, capped at 28 so every
    month actually has that day rather than silently skipping February on
    a day-30 schedule.
    """

    model_config = ConfigDict(frozen=True)

    addition_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    start_date: date
    frequency: RecurringAdditionFrequency
    end_date: date | None = None
    mode: RecurringAdditionMode
    value: float = 0.0
    currency: CurrencyCode = "USD"
    priority: int = 0

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_schedule_day_of_month(cls, data: object) -> object:
        """Translate the old `schedule_day_of_month`-only schedule onto the new fields, in place.

        The old model had no `start_date` at all — a day-of-month rule
        applied retroactively to any month once persisted. `date(2000, 1,
        day)` reproduces that same unlimited-lookback behavior under the
        new model rather than inventing a start date that would silently
        stop a rule the user already had running. Without this, loading a
        `store.json` written before this schedule redesign would fail
        validation outright the next time the app starts.

        Returns
        -------
        object
            `data`, migrated onto the new schedule fields if it was in the old shape; unchanged otherwise.
        """
        if isinstance(data, dict) and "schedule_day_of_month" in data and "frequency" not in data:
            data = dict(data)
            day = data.pop("schedule_day_of_month")
            data["frequency"] = "monthly"
            data.setdefault("start_date", date(2000, 1, min(int(day), 28)))
        return data


class WithdrawalPriorityEntry(BaseModel):
    """One goal's place in the order goals are drawn down from when unallocated money goes negative.

    Purely an ordering — the withdrawal automation itself
    (`ledger.goal_automations.run_withdrawal_automation`) is event-driven
    (triggered whenever unallocated dips below zero), not scheduled, so
    there's no schedule field here the way `RecurringAddition` has one.
    """

    model_config = ConfigDict(frozen=True)

    goal_id: str = Field(min_length=1)
    priority: int = 0


class EarningsDeposit(BaseModel):
    """One destination a paystub's pay actually lands in — a wage deposit, or a separate reimbursement.

    `account_last4` is the bank account digits the paystub itself prints
    next to a deposit line, when it prints one at all — used to match
    against a real `Account.account_id`'s own trailing digits (see the
    `{institution}:{kind}:{last4}` convention) during reconciliation.
    """

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    account_last4: str | None = None
    amount: float


class EarningsLineItem(BaseModel):
    """One named line under a paystub's reimbursements section, for one pay period.

    Unlike `EarningsDeposit`, this isn't tied to a bank account — it's a
    paystub's own breakdown of *why* money was paid out (a specific
    reimbursement), used to propose splitting a matched deposit into
    named legs (see `dashboard.paystub.propose_posting_splits`).
    """

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    amount: float


class EarningsStatement(BaseModel):
    """A parsed paystub: gross pay, taxes withheld, and where the net pay actually landed.

    Deliberately doesn't try to capture every line item a paystub has —
    only what `dashboard.paystub.reconcile_earnings_statement` and
    `propose_posting_splits` need: the totals, the per-destination-account
    split (one paycheck can land in more than one account), and the
    reimbursement breakdown, since a paycheck-shaped deposit is often wage
    plus one or more separate reimbursements arriving together — the case
    that motivates splitting one bank posting into several (`PostingSplit`).
    """

    model_config = ConfigDict(frozen=True)

    pay_date: datetime
    gross_pay: float
    taxes_withheld: float
    net_pay: float
    deposits: list[EarningsDeposit] = Field(min_length=1)
    reimbursement_lines: list[EarningsLineItem] = Field(default_factory=list)


PendingSuggestionSource = Literal["ai", "pattern"]
"""Which automated categorizer produced a still-unconfirmed suggestion on a posting.

`"ai"` is an LLM suggestion (see `api.post_ai_suggest_category`); `"pattern"`
is a `CategoryPattern` match (see `api.post_pattern_suggest_category`). Kept
as two distinct values (not one boolean) so the UI can render each in its
own color and the "temporary" filter can distinguish them, per the user's
explicit request that the two never share a visual or a stored flag.
"""


class ManualOverride(BaseModel):
    """A user's direct edit to one posting, always winning over whatever a rule would have produced.

    Every field is optional independently — setting only `tag_ids` on a
    posting a rule already categorized correctly doesn't touch its
    category. Keyed by `posting_id` in `store.overrides_path`, applied
    after `ledger.categorization.apply_rules` every time postings are read,
    never baked into the ledger cache itself — so re-importing a statement
    or editing a rule can never silently erase a manual correction.

    `pending_source`/`pending_selected`/`pending_previous_*` track a
    suggestion an automated categorizer applied but the user hasn't
    confirmed yet (see `ledger.pending.resolve_pending_postings`): the
    category/subcategory fields are already updated optimistically, but
    the posting still renders as "temporary" until the user either accepts
    it (clearing the `pending_*` fields, keeping the new category) or
    rejects it (restoring `pending_previous_category_id`/
    `pending_previous_subcategory_id` and clearing `pending_*`). Snapshotting
    the previous category/subcategory here, rather than trying to recompute
    "what a rule would have produced," is what makes rejection exact even
    when the previous value came from a rule rather than a prior override.
    """

    model_config = ConfigDict(frozen=True)

    account_id: str | None = None
    category_id: str | None = None
    subcategory_id: str | None = None
    tag_ids: list[str] | None = None
    pending_source: PendingSuggestionSource | None = None
    pending_selected: bool = True
    pending_previous_category_id: str | None = None
    pending_previous_subcategory_id: str | None = None


class PostingSplitLeg(BaseModel):
    """One piece of a posting split into several independently-categorized legs.

    A paycheck landing as one $3,200 bank deposit might really be $3,000
    wage plus $200 expense reimbursement — two different things that
    happen to have arrived in one transfer. `amount` keeps the sign
    convention of the posting being split (same account, same direction).
    """

    model_config = ConfigDict(frozen=True)

    amount: float
    category_id: str | None = None
    subcategory_id: str | None = None
    description: str = ""


class PostingSplit(BaseModel):
    """A user's decision to break one posting into several legs, keyed by the original posting's id.

    `legs` must sum to exactly the original posting's `amount` — enforced
    where a split is written (the ledger still has to balance), not here,
    since validating that requires looking up the posting this describes.
    Never baked into the ledger cache itself, for the same reason
    `ManualOverride` isn't: re-importing a statement or rebuilding from
    raw archives can never silently erase a split a user set up.
    """

    model_config = ConfigDict(frozen=True)

    posting_id: str = Field(min_length=1)
    legs: list[PostingSplitLeg] = Field(min_length=2)


class PostingMerge(BaseModel):
    """A user's decision that two or more imported transactions are the same real-world event, recorded twice.

    Every transaction in `duplicate_transaction_ids` is dropped entirely
    (both its legs) from the resolved ledger; `kept_transaction_id`'s own
    transaction is the one that survives, its description overridden by
    `description` when given. Never baked into the ledger cache itself,
    for the same reason `PostingSplit`/`ManualOverride` aren't —
    re-importing a statement or rebuilding from raw archives can never
    silently resurrect a duplicate a user already resolved.
    """

    model_config = ConfigDict(frozen=True)

    merge_id: str = Field(min_length=1)
    kept_transaction_id: str = Field(min_length=1)
    duplicate_transaction_ids: list[str] = Field(min_length=1)
    description: str | None = None


class DismissedSuggestion(BaseModel):
    """A user's decision that an auto-detected suggestion isn't relevant, archived rather than discarded.

    `suggestion_id` is a stable key derived from the suggestion's own
    content (see `api._transfer_suggestion_id`/`_duplicate_suggestion_id`),
    not a random ID — the same real-world pair or group always dismisses
    and restores under the same key, regardless of how many times the
    detector recomputes it. Dismissing never edits a transfer rule, a
    posting, or a merge; it only removes one entry from the list of things
    still being proposed, so restoring it (deleting this record) is always
    lossless.
    """

    model_config = ConfigDict(frozen=True)

    suggestion_id: str = Field(min_length=1)
    kind: Literal["transfer", "duplicate"]
    description: str
    dismissed_at: datetime


class Posting(BaseModel):
    """One leg of one economic event — one row, like `trades.models.LedgerEvent`.

    `category_id` is always a top-level category; `subcategory_id`, when
    set, must be a child of that same category — never a leaf stored
    without its parent. Both are `None` on a posting against a virtual
    `income_source`/`expense_payee` counterparty until a rule or a manual
    edit resolves it. `amount` is signed from this posting's own account's
    point of view: positive means money arrived, negative means it left.
    `meta` carries provenance and rare, importer-specific facts (a dedup
    hash, which bank format produced this row) the same way
    `LedgerEvent.meta` does for IBKR data — never a new typed column for
    something only one source ever needs.
    """

    model_config = ConfigDict(frozen=True)

    posting_id: str = Field(min_length=1)
    transaction_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    posted_at: datetime
    amount: float
    currency: CurrencyCode
    category_id: str | None = None
    subcategory_id: str | None = None
    budget_id: str | None = None
    tag_ids: list[str] = Field(default_factory=list)
    description: str = ""
    meta: dict[str, str] = Field(default_factory=dict)

    polars_schema: ClassVar[dict[str, type[pl.DataType] | pl.DataType]] = {
        "posting_id": pl.Utf8,
        "transaction_id": pl.Utf8,
        "account_id": pl.Utf8,
        "posted_at": pl.Datetime("us"),
        "amount": pl.Float64,
        "currency": pl.Utf8,
        "category_id": pl.Utf8,
        "subcategory_id": pl.Utf8,
        "budget_id": pl.Utf8,
        "tag_ids": pl.List(pl.Utf8),
        "description": pl.Utf8,
        "meta": pl.Object,
    }
