"""Canonical schemas for the accounting domain: what an account, a category, and a posting are.

A `Posting` is the accounting equivalent of `trades.models.LedgerEvent`: one
immutable row of an append-only record, field names doubling as column
names. Where the investing ledger's rows are self-contained facts about one
symbol, a posting is only ever half of an economic event — it always has at
least one sibling posting (sharing `transaction_id`) whose amounts, once
converted to a common currency, sum to zero. That invariant is enforced by
callers (see `docs/accounting/architecture.md`'s "Invariant" section), not
by a single-row model, since a transaction can have more than two postings
(a paycheck landing in two accounts at once, split further into wage and
reimbursement legs).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal, assert_never, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from db.currency import CURRENCY_REFERENCE, CurrencyCode
from db.money import Money, Rate

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


def _is_importable_account_kind(kind: AccountKind) -> bool:
    """Classify one `AccountKind` as importable (has its own CSV standardizer) or not.

    Deliberately exhaustive rather than a bare frozenset literal: every
    branch below names every `AccountKind` value explicitly, so adding a
    new one to that `Literal` without updating this function fails the
    `assert_never` type check at build time — a bare `frozenset` would
    have silently classified an unhandled new kind as "not importable"
    forever, with nothing ever flagging that no one actually decided that.

    Returns
    -------
    bool
    """
    match kind:
        case "checking" | "savings" | "credit_card" | "vault":
            return True
        case "cash" | "loan" | "income_source" | "expense_payee" | "external_investment" | "other_asset":
            return False
    assert_never(kind)


IMPORTABLE_ACCOUNT_KINDS: frozenset[AccountKind] = frozenset(
    kind for kind in get_args(AccountKind) if _is_importable_account_kind(kind)
)
"""Every `AccountKind` with a registered CSV standardizer (see `importers.ingest.supported_import_kinds`).

An account of one of these kinds might already have its own,
independently-imported transaction for the same real-world event as some
other posting's placeholder counterparty — so a `TransferRule` may never
repoint a placeholder directly onto one of these (see
`ledger.categorization.apply_rules`); doing so risks the same posting being
counted twice, once from each side's own import. Repointing straight onto
any other kind (a virtual `income_source`/`expense_payee`, or a real
account nothing is ever independently imported for) stays safe, since
nothing else will ever independently post to it.
"""

VIRTUAL_ACCOUNT_KINDS: frozenset[AccountKind] = frozenset({"income_source", "expense_payee"})
"""The counterparty kinds that are not somewhere money actually sits.

Their "balance" is only how much has passed through categorization, so
every figure about money you hold excludes them — net worth
(`dashboard.net_worth`), the per-account history endpoint, and the opening
balances `dashboard.goals.unallocated_balance` counts. The income statement
reads the same set from the other side, to *find* the legs whose sibling is
one of these (`dashboard.income_statement`). One definition rather than one
per module, so a new virtual kind cannot be added to half of them.
"""

CategoryClassification = Literal["income", "expense"]
"""Whether a category is money coming in or money going out. Both share one
tree (a category can have subcategories regardless of which side it's on),
but the split matters for reporting — an income statement's two columns —
and for which categories even make sense to show when categorizing a
posting whose amount is positive vs. negative.
"""

TransactionOrigin = Literal["imported", "manual"]
"""Where a transaction came from: a bank statement, or a person typing it in.

The one discriminator on `db.core.Transaction`, and the reason manual
transfers need no ledger of their own. An `imported` transaction is
reproducible — replaying its archived statement recreates it, so a rebuild
owns it and may prune it. A `manual` one is not reproducible from anything:
no statement will ever describe it (see `ManualTransfer`), so a rebuild
must leave it alone. Everything else about the two is identical, which is
exactly why one column is enough and a second table was not.
"""


class Currency(BaseModel):
    """One supported currency's display metadata — never a value on its own, only ever attached to one."""

    model_config = ConfigDict(frozen=True)

    code: CurrencyCode
    symbol: str = Field(min_length=1)
    decimal_places: int = 2


SUPPORTED_CURRENCIES: dict[CurrencyCode, Currency] = {
    code: Currency(code=code, symbol=reference.symbol, decimal_places=reference.decimal_places)
    for code, reference in CURRENCY_REFERENCE.items()
}
"""Every currency a `currency: CurrencyCode` field elsewhere in this package
can hold — the display registry every rate lookup, chart, and dropdown is
driven by, never a hardcoded pair (see `ledger.currency.convert` and
`market_data.exchange_rates`).

A *projection* of `db.currency.CURRENCY_REFERENCE` now, rather than a second
declaration of the same three facts. That module is where the list lives
because `trades` needs it too and cannot import this package — which is
exactly why `trades.ledger_events.currency` used to have no constraint at
all. The rows of `public.currencies` come from the same place, so a currency
cannot be renderable here and unstorable there. Adding one is still one
edit, and it is now in `db.currency`.
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
    account it's a named sub-balance of. `broker_connection_id` is only set
    for the `external_investment` kind, naming the *broker connection*
    whose portfolio this account mirrors, since its balance is never
    derived from its own postings.

    That field replaces `external_ref`, a free-text column whose only ever
    value was the literal `"trades"` and which `dashboard.net_worth`
    string-matched on. It is the one id on this model that is a raw
    `broker_connections.id` rather than a natural key, deliberately: it
    points across the seam into the other ledger's schema, where this
    package has no business resolving natural keys, and the database
    enforces it as a real foreign key (see `db.core.Account`) so an account
    can never name a connection that isn't there.

    `meta` holds facts about the account itself rather than any one
    posting — currently just `apy_pct`, the interest rate last seen on a
    statement, carried here because it describes the account's terms, not
    a single transaction. `closed` marks a real-world account that no
    longer exists at its institution — its transaction history stays
    exactly as imported (never deleted), it just stops being offered as a
    destination for new imports or transfers. `last_four` is the real
    trailing digits the institution shows for this account, when it shows
    any at all — `None` for vaults, cash, loans, and virtual counterparties,
    which have none.
    """

    model_config = ConfigDict(frozen=True)

    account_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: AccountKind
    institution: str = Field(min_length=1)
    currency: CurrencyCode
    last_four: str | None = None
    parent_account_id: str | None = None
    broker_connection_id: uuid.UUID | None = None
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


RuleEffect = Literal["transfer", "categorize"]
"""What a `categorization_rules` row does with the postings its description match selects.

`"transfer"` repoints the posting's counterparty (`TransferRule` below);
`"categorize"` proposes a category (`CategoryPattern` below). The two share
one table because they are the same matcher, and stay two pydantic models
because they are two API resources — see
`accounting.db.automation.CategorizationRule`.
"""


class TransferRule(BaseModel):
    """A user-maintained trigger/action pair for automatically resolving a posting's counterparty.

    Named specifically for what it's for — linking two of your own
    accounts together as an internal transfer — to avoid reading as the
    same thing as a `CategoryPattern` below, which only ever suggests a
    category and never resolves a counterparty. Resolving a counterparty
    into a real account you hold makes the transaction an internal
    transfer, which is never categorizable in the first place (see
    `dashboard.income_statement.real_income_expense_legs`) — so unlike
    `CategoryPattern`, this has no category fields of its own to set.

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
    `excluded_transaction_ids` opts specific, otherwise-matching
    transactions out of this one rule, without disabling it for anything
    else it correctly resolves — the excluded transaction simply falls
    back to whatever the next-matching rule (or no rule) would have done.
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str = Field(min_length=1)
    description_contains: str = Field(min_length=1)
    account_id: str | None = None
    counterparty_account_id: str | None = None
    priority: int = 0
    description: str = ""
    active: bool = True
    excluded_transaction_ids: list[str] = Field(default_factory=list)
    version: int = 1


TransferLinkSource = Literal["manual", "rule"]
"""Which mechanism confirmed a `TransferLink` — display-only, never read by resolution itself."""


class TransferLink(BaseModel):
    """A confirmed pairing of two transactions as the two sides of one real-world transfer.

    Neither transaction's own postings are ever changed to create this —
    each side's real leg (already on its own real account from import)
    stays exactly as it was; the link only changes classification, via
    `ledger.transfers.apply_transfer_links`: both transactions are excluded
    from income/expense regardless of what account either placeholder leg
    still points at. `link_id` is always derived from the two transaction
    ids sorted once (see `ledger.transfers.make_transfer_link`) — the same
    real-world pair links (and unlinks) under the same id no matter which
    side a caller names first. `source` is `"manual"` for a user's own
    "flag as transfer"/suggestion-panel pick, `"rule"` for one a
    `TransferRule` found a safe, unique match for at write time (see
    `ledger.transfers.reconcile_rule_links`) — display-only, never read by
    resolution itself. `rule_id`, set only when `source == "rule"`, names
    *which* rule found it. It used to be a plain historical label deliberately
    left un-foreign-keyed, on the theory that a link should keep remembering
    the rule that made it even after that rule is gone; it is a real foreign
    key now (DB-audit D7's "Keyless Entry"), because a label naming a row
    nobody can look up is not provenance. `ON DELETE SET NULL` keeps what was
    actually worth keeping: the link survives its rule, `source` still records
    that a rule rather than the user proposed it, and only the reference that
    no longer resolves is cleared.
    """

    model_config = ConfigDict(frozen=True)

    link_id: str = Field(min_length=1)
    transaction_id_a: str = Field(min_length=1)
    transaction_id_b: str = Field(min_length=1)
    source: TransferLinkSource = "manual"
    rule_id: str | None = None


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
    version: int = 1


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
    value: Money
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
    amount: Money
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

    **This is a shape, not a table.** It used to be both: `manual_transfers`
    was a parallel mini-ledger holding a date, two accounts, two amounts and
    a description — everything `transactions` plus two `postings` already
    express, expressed a second, incompatible way, which is why its rows had
    to be turned into postings by a resolution stage of their own before any
    balance could count them. A manual transfer is now stored as exactly
    what it is: one `Transaction` with `origin = "manual"` and its two
    balancing legs (see `repositories.accounts.insert_manual_transfers`,
    which writes them, and `load_manual_transfers`, which reads this shape
    back out of them). This model survives as the API's vocabulary for the
    pair — "money left here, money arrived there" — and as the one place
    the pair's own invariant lives.

    That invariant is the positivity of both legs. `Posting.amount` is
    signed by design (a debit is negative, a credit positive) and must stay
    unconstrained, so the constraint cannot live on the storage the legs now
    share with every imported posting; it lives here, on the only thing that
    still expresses "the *from* amount" and "the *to* amount" as distinct,
    directional quantities. `insert_manual_transfers` is what turns them
    into the signed pair (`-from_amount`, `+to_amount`), so a negative
    `from_amount` sneaking through would silently invert the transfer.
    """

    model_config = ConfigDict(frozen=True)

    transfer_id: str = Field(min_length=1)
    date: datetime
    from_account_id: str = Field(min_length=1)
    to_account_id: str = Field(min_length=1)
    from_amount: Money = Field(gt=0)
    """Strictly positive — the magnitude leaving `from_account_id`; see the class docstring."""
    to_amount: Money = Field(gt=0)
    """Strictly positive — the magnitude arriving at `to_account_id`; see the class docstring."""
    description: str = ""


class Budget(BaseModel):
    """One spending target for one top-level expense category, or one of its subcategories.

    `month` is what scopes the target: a `"YYYY-MM"` string targets that
    one month, and `None` is the *general* target — the standing amount
    that applies to every month alike, which the Budget page's "General"
    mode edits. Both live in the same list (and the same `budgets` table):
    a month target and a general target for the same category coexist as
    two rows, and neither falls back to or overwrites the other, so
    switching the page's mode never silently rewrites the other one.

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
    # The month segment is pinned to 01-12: `^\d{4}-\d{2}$` alone accepts
    # "2024-13" through "2024-99", which would persist and then sort and group
    # as if it were a real month.
    month: Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] | None = None
    category_id: str = Field(min_length=1)
    subcategory_id: str | None = None
    amount: Money
    currency: CurrencyCode = "USD"


CompoundingFrequency = Literal["annually", "monthly", "daily"]


class SimulatorScenario(BaseModel):
    """A saved set of inputs to the compound-interest projector (see `dashboard.simulator.project`).

    Every field here is one of the projector's five inputs, plus a name to
    tell saved scenarios apart — nothing here is itself computed; the
    projection is always run fresh from these inputs, never cached.
    """

    model_config = ConfigDict(frozen=True)

    scenario_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    initial_capital: Money
    monthly_contribution: Money
    horizon_years: Rate
    annual_rate_pct: Rate
    compounding_frequency: CompoundingFrequency = "monthly"
    currency: CurrencyCode = "USD"


class Goal(BaseModel):
    """A savings target — its balance is never stored here, only derived from its `GoalContribution`s.

    See `dashboard.goals.all_goal_balances`: the balance at any point in time
    is always the running sum of contributions up to that date, computed
    fresh, the same way an account's balance is never a cached figure
    (see `Budget`'s own docstring for the same reasoning applied to
    spending).
    """

    model_config = ConfigDict(frozen=True)

    goal_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    target_amount: Money
    target_currency: CurrencyCode = "USD"
    target_date: datetime
    color: str = Field(min_length=1)
    created_at: datetime
    version: int = 1


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

    `account_id` names the account the allocated money actually sits in
    ("envelope over balance"). It is plumbing only for now: nothing in
    `dashboard.goals` reads it, so no goal balance, net-worth figure, or
    unallocated-money computation changes because it is set — a later
    change makes the unallocated arithmetic account-aware.
    """

    model_config = ConfigDict(frozen=True)

    contribution_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    date: datetime
    amount: Money
    currency: CurrencyCode = "USD"
    note: str = ""
    account_id: str | None = None
    source_posting_id: str | None = None
    origin: GoalContributionOrigin = "manual"
    edited: bool = False


GoalAutomationDirection = Literal["contribution", "withdrawal"]
"""Which way an automation moves money: into a goal, or out of one.

The one thing that distinguishes the two kinds of `GoalAutomation`. A
`contribution` runs on a schedule (`start_date`/`frequency`/`end_date`)
and allocates unallocated money into its goal; a `withdrawal` is purely
an ordering, drawn on whenever unallocated money dips below zero, so it
carries no schedule at all.
"""

GoalAutomationMode = Literal["fixed_amount", "percent_of_unallocated", "remainder"]
GoalAutomationFrequency = Literal["daily", "weekly", "biweekly", "monthly"]


class GoalAutomation(BaseModel):
    """One ordered rule for automatically moving money into — or out of — a goal.

    `direction` is the discriminator, and it decides which of the fields
    below are set (enforced here *and* by the `goal_automations` table's
    own `schedule_matches_direction` CHECK, so neither layer can drift):

    - `direction="contribution"` carries the whole schedule —
      `start_date` + `frequency`, optionally bounded by `end_date`; see
      `ledger.goal_automations.next_recurring_occurrence` for how a due
      date is derived from those. For `frequency="monthly"`, the day of
      month is `start_date`'s own day, capped at 28 so every month
      actually has that day rather than silently skipping February on a
      day-30 schedule. `mode`/`value`/`currency` say how much it wants —
      and `currency` may only ever be `BASE_CURRENCY`, because the
      unallocated pool it is funded from is denominated in it and the
      contribution it writes is labelled with it
      (`api_models.BaseCurrencyOnly` refuses anything else at the write
      boundary; item A6).
    - `direction="withdrawal"` carries none of them. The withdrawal
      automation (`ledger.goal_automations.run_withdrawal_automation`) is
      event-driven — triggered whenever unallocated money dips below zero
      — not scheduled, so a withdrawal row is nothing but its goal and
      its place in the drawdown order.

    `priority` is the manually-set execution order (lowest first) the
    Goals page's drag-and-drop reorders, and means the corresponding
    thing in each direction: which contribution gets funded first (a
    `fixed_amount` row funded first can leave less, or nothing, for a
    lower-priority one when unallocated money runs out — see
    `ledger.goal_automations.run_recurring_additions`), and which goal
    gets drawn down first. `mode="remainder"` ("whatever's left after all
    the others") is only ever valid on the single lowest-priority
    contribution — enforced by the API that persists the list, not by
    this model.
    """

    model_config = ConfigDict(frozen=True)

    automation_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    direction: GoalAutomationDirection
    priority: int = 0
    start_date: date | None = None
    frequency: GoalAutomationFrequency | None = None
    end_date: date | None = None
    mode: GoalAutomationMode | None = None
    value: Money | None = Field(default=None, json_schema_extra={"default": None})
    currency: CurrencyCode | None = None

    @model_validator(mode="after")
    def _check_schedule_matches_direction(self) -> GoalAutomation:
        """Reject a `contribution` missing its schedule, or a `withdrawal` carrying one.

        The pydantic twin of the table's own `schedule_matches_direction`
        CHECK — stated in both places deliberately, so a request body is
        rejected with a 422 at the edge rather than an `IntegrityError`
        deep inside a transaction, and so a row hand-written straight into
        Postgres still can't reach the state this rejects.

        Returns
        -------
        GoalAutomation
            `self`, unchanged.

        Raises
        ------
        ValueError
            If the fields set don't match `direction`.
        """
        scheduled = (self.start_date, self.frequency, self.mode, self.value, self.currency)
        if self.direction == "contribution":
            if any(field is None for field in scheduled):
                message = "A 'contribution' automation needs start_date, frequency, mode, value and currency"
                raise ValueError(message)
        elif any(field is not None for field in (*scheduled, self.end_date)):
            message = "A 'withdrawal' automation carries no schedule — it is only a goal and a drawdown priority"
            raise ValueError(message)
        return self


class EarningsDeposit(BaseModel):
    """One destination a paystub's pay actually lands in — a wage deposit, or a separate reimbursement.

    `account_last4` is the bank account digits the paystub itself prints
    next to a deposit line, when it prints one at all — used to match
    against a real `Account.last_four` during reconciliation.
    """

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    account_last4: str | None = None
    amount: Money


class EarningsLineItem(BaseModel):
    """One named line under a paystub's reimbursements section, for one pay period.

    Unlike `EarningsDeposit`, this isn't tied to a bank account — it's a
    paystub's own breakdown of *why* money was paid out (a specific
    reimbursement), used to propose splitting a matched deposit into
    named legs (see `dashboard.paystub.propose_posting_splits`).
    """

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    amount: Money


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
    gross_pay: Money
    taxes_withheld: Money
    net_pay: Money
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

DismissedSuggestionKind = Literal["transfer", "duplicate"]
"""What a *detected* suggestion is about — a transfer pair, or a duplicate group.

Only these two are detected (and therefore dismissable); a `"category"`
suggestion is staged on a posting instead and resolved by accepting or
rejecting it, never dismissed. See `SuggestionKind`.
"""

SuggestionStatus = Literal["pending", "dismissed"]
"""Where one row of `accounting.suggestions` sits in its lifecycle.

`"pending"` is a staged category suggestion awaiting the user's accept or
reject — resolving it *deletes* the row, which is why there is no
`"accepted"`/`"rejected"` value here (see `ledger.pending`). `"dismissed"`
is a detected transfer/duplicate suggestion the user archived so it stops
being proposed; that one is kept, since restoring it has to be lossless.
"""

SuggestionKind = Literal["category", "transfer", "duplicate"]
"""What one row of `accounting.suggestions` is a suggestion *about*.

`"category"` belongs to the pending lifecycle (a category staged on one
posting); `"transfer"`/`"duplicate"` belong to the dismissed one
(`DismissedSuggestionKind`, a detector's proposal about a pair or a group).
The table's own `CheckConstraint` is what ties each kind to the status it
can appear with, rather than leaving `kind` the unconstrained free text the
old `dismissed_suggestions.kind` column was.
"""

SuggestionSource = Literal["ai", "pattern", "detector"]
"""What produced one row of `accounting.suggestions`.

`"ai"`/`"pattern"` are the two `PendingSuggestionSource` values, kept
distinct for the reason that type documents. `"detector"` is the
transfer/duplicate candidate search (see `api.get_transfer_suggestions`/
`get_duplicate_suggestions`), which had no stored source at all while
dismissals lived in their own table.
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

    amount: Money
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
    kind: DismissedSuggestionKind
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

    `posted_at` and `description` are the *transaction's*, not this leg's —
    they are stored once, on `db.core.Transaction`, and appear on every leg
    here because this model is the row shape of the analytics projection
    (`ledger.frame.LEDGER_FRAME_SCHEMA`), which is flat by design. An
    importer building a pair sets the same value on both legs
    (`importers.common.posting_pair`), and `importers.ingest.load_ledger`
    joins the one stored value back onto each leg on the way out. Nothing
    downstream can therefore observe two legs of one transaction disagreeing
    about either, which is what the storage move made structural rather than
    merely conventional.
    """

    model_config = ConfigDict(frozen=True)

    posting_id: str = Field(min_length=1)
    transaction_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    posted_at: datetime
    amount: Money
    currency: CurrencyCode
    category_id: str | None = None
    subcategory_id: str | None = None
    budget_id: str | None = None
    tag_ids: list[str] = Field(default_factory=list)
    description: str = ""
    meta: dict[str, str] = Field(default_factory=dict)
