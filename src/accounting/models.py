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


class Account(BaseModel):
    """One place money can sit or be attributed to — a real account, a vault, or a virtual counterparty.

    `parent_account_id` is only set for a `vault`, pointing at the savings
    account it's a named sub-balance of. `external_ref` is only set for the
    `external_investment` kind, naming where its value actually comes from
    (currently always `"trades"`, meaning `trades.dashboard.overview_cards`)
    since this account's balance is never derived from its own postings.
    """

    model_config = ConfigDict(frozen=True)

    account_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: AccountKind
    institution: str = Field(min_length=1)
    currency: str = Field(min_length=1)
    parent_account_id: str | None = None
    external_ref: str | None = None


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
    when more than one rule matches; the lowest number wins.
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


class OtherAsset(BaseModel):
    """A manually-entered net-worth line with no transaction history — property, a car, etc.

    Unlike everything else in this module, this is a preference-like
    record a user types in directly rather than something derived from a
    posting; it lives in the same store for convenience, not because it's a
    fact about what happened.
    """

    model_config = ConfigDict(frozen=True)

    asset_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    value_usd: float
    note: str = ""


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
    currency: str = Field(min_length=1)
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
