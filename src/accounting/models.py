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

from datetime import datetime
from typing import ClassVar, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

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
    a single transaction.
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


class Category(BaseModel):
    """One node in the two-level category tree: a top-level category, or a subcategory of one.

    `parent_category_id` is `None` for a top-level category and points at
    one for a subcategory — never more than one level deep. A subcategory
    is expected (not enforced here) to share its parent's `classification`
    and `color`, so a chart coloring by top-level category stays consistent
    when a user drills into subcategories.
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


class Rule(BaseModel):
    """A user-maintained trigger/action pair for automatically resolving a posting's counterparty and category.

    Every field on the trigger side must match for the rule to apply
    (`description_contains` is a case-insensitive substring check;
    `account_id`, when set, restricts the rule to postings on that one
    account). `counterparty_account_id`/`counterparty_account_name`/
    `counterparty_account_kind` describe the real account this posting's
    placeholder counterparty should be repointed at — created on first
    match if it doesn't exist yet (e.g. a new vault). `priority` breaks ties
    when more than one rule matches; the lowest number wins. `description`
    is a free-text note on what the rule is actually for — purely for a
    human re-reading the rule list later, never read by the matching logic.
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str = Field(min_length=1)
    description_contains: str = Field(min_length=1)
    account_id: str | None = None
    category_id: str | None = None
    subcategory_id: str | None = None
    counterparty_account_id: str | None = None
    counterparty_account_name: str | None = None
    counterparty_account_kind: AccountKind | None = None
    counterparty_parent_account_id: str | None = None
    priority: int = 0
    description: str = ""


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


class Budget(BaseModel):
    """One month's spending target for one top-level expense category.

    Actual spend against a budget is never stored here or on the postings
    it covers — a posting's own `category_id` already determines which
    budget it counts against for whichever month it landed in, so
    "actual" is always computed fresh from
    `dashboard.income_statement.category_totals`, the same on-demand way
    an account's balance comes from summing its postings rather than a
    cached figure that could drift out of sync.
    """

    model_config = ConfigDict(frozen=True)

    budget_id: str = Field(min_length=1)
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    category_id: str = Field(min_length=1)
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


class EarningsStatement(BaseModel):
    """A parsed paystub: gross pay, taxes withheld, and where the net pay actually landed.

    Deliberately doesn't try to capture every line item a paystub has —
    only what `dashboard.paystub.reconcile_earnings_statement` needs: the
    totals, and the per-destination-account split, since one paycheck can
    land in more than one account (a direct-deposit split, or wage plus a
    separately-deposited expense reimbursement) — the case that motivates
    splitting one bank posting into several (`PostingSplit`).
    """

    model_config = ConfigDict(frozen=True)

    pay_date: datetime
    gross_pay: float
    taxes_withheld: float
    net_pay: float
    deposits: list[EarningsDeposit] = Field(min_length=1)


class ManualOverride(BaseModel):
    """A user's direct edit to one posting, always winning over whatever a rule would have produced.

    Every field is optional independently — setting only `tag_ids` on a
    posting a rule already categorized correctly doesn't touch its
    category. Keyed by `posting_id` in `store.overrides_path`, applied
    after `ledger.categorization.apply_rules` every time postings are read,
    never baked into the ledger cache itself — so re-importing a statement
    or editing a rule can never silently erase a manual correction.
    """

    model_config = ConfigDict(frozen=True)

    account_id: str | None = None
    category_id: str | None = None
    subcategory_id: str | None = None
    tag_ids: list[str] | None = None


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
