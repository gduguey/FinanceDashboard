"""The core ledger: accounts, categories, tags, transactions, and their postings.

Every table's primary key is a surrogate `id`, never `(user_id, ..._id)` —
see `db.base.derive_id`'s docstring for why a deterministic hash of the old
human-chosen string, not a random default, is what makes that safe for
tables `accounting.repositories` rewrites wholesale. `natural_key`
is that human-chosen string (what used to be `account_id`, `category_id`,
...), kept as a plain column with a `UNIQUE(user_id, natural_key)`
constraint instead of being the primary key itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import get_args

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.models import AccountKind, CategoryClassification, CurrencyCode, TransactionOrigin
from accounting.precedence import OverlayStage
from db.base import MONEY, Base, Timestamped, check_in_sql

SCHEMA = "accounting"

TRADES_SCHEMA = "trades"
"""The other ledger's schema, named here because exactly one column reaches into it.

`Account.broker_connection_id` is the whole of the seam between the two
ledgers (DB-audit move #1). It is a real foreign key rather than the string
`external_ref = "trades"` it replaces, which means `accounting`'s DDL now
depends on `trades.broker_connections` existing — the one place the two
otherwise-independent packages touch. That dependency is the point: a
brokerage account pointing at a connection that was never created, or that
has since been deleted, has to be impossible rather than merely unlikely.
"""


def two_level_depth(parent_column: str) -> Computed:
    """Build the `depth` expression Postgres computes for a two-level tree's row from its own parent column.

    Half of the depth guard described on `Category`: generated rather than
    written, so no insert can claim a depth its `parent_*_id` doesn't
    actually imply.

    Parameters
    ----------
    parent_column
        The self-referencing column, e.g. `"parent_category_id"`.

    Returns
    -------
    sqlalchemy.Computed
    """
    return Computed(f"CASE WHEN {parent_column} IS NULL THEN 1 ELSE 2 END", persisted=True)


def parent_must_be_a_root(parent_column: str) -> Computed:
    """Build the constant-`1` expression a two-level tree's composite parent foreign key points at.

    The other half of the depth guard: this is the column that makes
    `FOREIGN KEY (parent_x_id, parent_depth) REFERENCES x (id, depth)`
    expressible at all, since a foreign key may only name columns, never
    the literal a plain `CHECK` would compare against.

    Parameters
    ----------
    parent_column
        The self-referencing column, e.g. `"parent_category_id"`.

    Returns
    -------
    sqlalchemy.Computed
    """
    return Computed(f"CASE WHEN {parent_column} IS NULL THEN NULL ELSE 1 END", persisted=True)


def child_of_category_columns(
    category_column: str, subcategory_column: str
) -> tuple[ForeignKeyConstraint, CheckConstraint]:
    """Make one table's `(category_id, subcategory_id)` pair a coherent pair, not two independent slots.

    Six tables carry the pair (DB-audit D10, correctness finding E3), and
    every one of them used to allow a `subcategory_id` belonging to some
    *other* category — or a subcategory with no category beside it at all.
    Both are closed here, structurally, and in the only two ways they can
    be:

    - **"is actually a child of"** is a real foreign key, because
      `categories` carries `UNIQUE (id, parent_category_id)` for exactly
      this purpose. `(subcategory_id, category_id)` referencing
      `(id, parent_category_id)` says precisely "the row named by
      `subcategory_id` is a child of the row named by `category_id`" — the
      engine checks it on every write and on every reparent, where a
      trigger would have had to be written, tested, and remembered.
    - **"a subcategory implies a category"** cannot ride on that foreign
      key: the default `MATCH SIMPLE` skips the check entirely as soon as
      *any* referencing column is `NULL`, so a subcategory with a `NULL`
      category slips straight through. `MATCH FULL` would close it, but it
      would also forbid the legitimate "filed at the top level only" row
      (`category_id` set, `subcategory_id` `NULL`). So it is a `CHECK`.

    Parameters
    ----------
    category_column
        The column naming the top-level category.
    subcategory_column
        The column naming the subcategory.

    Returns
    -------
    tuple[sqlalchemy.ForeignKeyConstraint, sqlalchemy.CheckConstraint]
        Ready to splat into a table's `__table_args__`.
    """
    return (
        ForeignKeyConstraint(
            [subcategory_column, category_column],
            [f"{SCHEMA}.categories.id", f"{SCHEMA}.categories.parent_category_id"],
        ),
        CheckConstraint(
            f"{subcategory_column} IS NULL OR {category_column} IS NOT NULL", name="subcategory_needs_category"
        ),
    )


def stage_constraint(stage: OverlayStage) -> CheckConstraint:
    """Pin an overlay table's `stage` column to the single stage that table is applied at.

    Every overlay table stores which stage of the resolution pipeline its
    rows are applied at, so the resolver can read its running order out of
    the schema (see `accounting.precedence`). For all but
    `categorization_rules` — whose stage follows its `effect` — that value
    is the same on every row of the table, and this is what stops it from
    silently becoming something else: a row claiming a stage its own table
    is never applied at would be an overlay that quietly never runs.

    Parameters
    ----------
    stage
        The one stage rows of this table may declare.

    Returns
    -------
    sqlalchemy.CheckConstraint
    """
    return CheckConstraint(check_in_sql("stage", [stage]), name="stage")


class Account(Base, Timestamped):
    """One place money can sit or be attributed to — a real account, a vault, or a virtual counterparty.

    Two levels deep and no deeper, like `Category`: a `vault` names the
    savings account it is a sub-balance of, and that savings account names
    nothing. The `depth`/`parent_depth` pair below is what makes a
    vault-of-a-vault unrepresentable rather than merely unexpected — see
    `Category`'s docstring for the mechanism and why it beats the
    alternatives.

    `broker_connection_id` is the seam between the two ledgers. It replaces
    `external_ref`, a bare `String` set to the literal `"trades"` that
    `dashboard.net_worth` string-matched on to decide whose value came from
    the investment portfolio (DB-audit D7/move #1). Now the account names
    the `trades.broker_connections` row its value is pulled from, as a real
    typed foreign key, so "this account mirrors a connection that doesn't
    exist" is not a state the database can hold — and `ON DELETE SET NULL`
    means removing the connection degrades the account to a
    manually-valued one instead of leaving a dangling reference behind.
    """

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(check_in_sql("kind", get_args(AccountKind)), name="kind"),
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        CheckConstraint(
            "broker_connection_id IS NULL OR kind = 'external_investment'", name="broker_link_is_an_investment"
        ),
        UniqueConstraint("user_id", "natural_key", name="uq_accounts_user_natural_key"),
        UniqueConstraint("id", "depth", name="uq_accounts_id_depth"),
        ForeignKeyConstraint(
            ["parent_account_id", "parent_depth"], [f"{SCHEMA}.accounts.id", f"{SCHEMA}.accounts.depth"]
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    name: Mapped[str]
    kind: Mapped[str]
    institution: Mapped[str]
    currency: Mapped[str]
    last_four: Mapped[str | None] = mapped_column(default=None)
    parent_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    depth: Mapped[int] = mapped_column(SmallInteger, two_level_depth("parent_account_id"))
    """Generated, never written — `1` for an account with no parent, `2` for a vault. See `Category.depth`."""
    parent_depth: Mapped[int | None] = mapped_column(SmallInteger, parent_must_be_a_root("parent_account_id"))
    """Generated, never written — the `1` this row's composite parent foreign key requires its parent to be at."""
    broker_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{TRADES_SCHEMA}.broker_connections.id", ondelete="SET NULL"),
        default=None,
    )
    """Which `trades` broker connection this account's value is pulled from, or `NULL` if it is valued locally."""
    meta: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    closed: Mapped[bool] = mapped_column(default=False)


class Category(Base, Timestamped):
    """One node in the two-level category tree: a top-level category, or a subcategory of one.

    A category is **retired, never deleted**, once anything has been filed
    under it. `postings.category_id`/`subcategory_id` are real foreign keys
    holding the category a statement was *imported* with — raw provenance,
    written once and never rewritten (DB-audit D14) — so a `DELETE` of the
    row they point at is not something application code can arrange by
    clearing those columns first; it has to be impossible. Retirement is
    what makes it impossible: `retired_at` takes the category out of the
    live tree (`repositories.taxonomy.load_categories` returns only live
    rows) while the row itself stays put, so every posting's foreign key
    stays valid forever and no posting is touched.

    The two retirement shapes are what a merge and a delete each leave
    behind, and `superseded_by_category_id` is the difference:

    - **merged** — `retired_at` set, `superseded_by_category_id` naming the
      category it folded into. Every posting imported under this one
      resolves to that successor
      (`repositories.taxonomy.load_category_redirects`).
    - **deleted** — `retired_at` set, no successor. Postings imported under
      it resolve to uncategorized, exactly as if they had never carried a
      category at all.

    Writing a category again clears its retirement (see
    `repositories.taxonomy._category_row`): re-creating "Dining" by name,
    or importing a file that mints it again, brings the same row back to
    life rather than minting a second row its `UNIQUE(user_id,
    natural_key)` would reject.

    ## Exactly two levels, enforced by the engine

    `parent_category_id` is an adjacency list — Karwin's "Naive Trees"
    (DB-audit D8) — and for a tree this shallow that is the *right* shape.
    What was missing is the depth bound: nothing stopped a
    subcategory-of-a-subcategory (correctness finding E1), even though
    every reader in this codebase assumes two levels.

    A plain `CHECK` cannot express it, because a `CHECK` only ever sees its
    own row and the fact in question is about the parent's row. Of the two
    mechanisms that can, this uses the composite foreign key rather than a
    `CONSTRAINT TRIGGER`:

    - `depth` is `1` for a root and `2` for a child, and `parent_depth` is
      `1` whenever there is a parent. Both are `GENERATED ALWAYS AS ...
      STORED`, so neither is something an insert supplies, gets wrong, or
      forgets — they are functions of `parent_category_id` and nothing else.
    - `UNIQUE (id, depth)` makes `(id, depth)` a legal foreign-key target,
      and `FOREIGN KEY (parent_category_id, parent_depth) REFERENCES
      categories (id, depth)` then reads exactly as the rule does: *my
      parent must be a row that sits at depth 1*. A grandchild fails on
      insert, and re-parenting a row that already has children fails too,
      because it would have to leave depth 1 while a child still references
      it there.

    A `CONSTRAINT TRIGGER` would enforce the same thing, but as procedural
    code: it has to be written in PL/pgSQL, kept in step with the model by
    hand, and it is only as good as the events it happens to be declared
    for. The composite foreign key is enforced by the same machinery as
    every other reference in this schema, is visible in a `psql` table description,
    and cannot be bypassed by a code path nobody thought of. The trigger is
    reserved for the one invariant no key can express — the cross-row
    zero-sum check in `db.triggers`.

    `UNIQUE (id, parent_category_id)` is the *other* foreign-key target this
    table publishes, and it is what makes a `(category_id, subcategory_id)`
    pair elsewhere in the schema a real reference rather than two hopeful
    columns — see `child_of_category_columns`.
    """

    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint(check_in_sql("classification", get_args(CategoryClassification)), name="classification"),
        CheckConstraint(
            "superseded_by_category_id IS NULL OR retired_at IS NOT NULL", name="successor_requires_retirement"
        ),
        UniqueConstraint("user_id", "natural_key", name="uq_categories_user_natural_key"),
        UniqueConstraint("id", "depth", name="uq_categories_id_depth"),
        UniqueConstraint("id", "parent_category_id", name="uq_categories_id_parent_category_id"),
        ForeignKeyConstraint(
            ["parent_category_id", "parent_depth"], [f"{SCHEMA}.categories.id", f"{SCHEMA}.categories.depth"]
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    name: Mapped[str]
    classification: Mapped[str]
    parent_category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    depth: Mapped[int] = mapped_column(SmallInteger, two_level_depth("parent_category_id"))
    """Generated, never written — `1` for a top-level category, `2` for a subcategory. See the class docstring."""
    parent_depth: Mapped[int | None] = mapped_column(SmallInteger, parent_must_be_a_root("parent_category_id"))
    """Generated, never written — the `1` this row's composite parent foreign key requires its parent to be at."""
    color: Mapped[str]
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    """When this category left the live tree, or `NULL` while it is still in it."""
    superseded_by_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id", ondelete="SET NULL"), default=None
    )
    """The category this one merged into, or `NULL` — never set on a live row (see the class docstring).

    `ON DELETE SET NULL` rather than the schema's usual restrict: if the
    successor is itself hard-deleted later, a tombstone pointing at nothing
    is exactly the "deleted" shape, so the engine can degrade a merge into a
    delete without anyone having to remember to."""


class Tag(Base, Timestamped):
    """A cross-cutting label — a trip, a move, an event — independent of the category tree."""

    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("user_id", "natural_key", name="uq_tags_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    name: Mapped[str]


class Transaction(Base, Timestamped):
    """One economic event, grouping the postings that are its legs — and owning its date, description, and balance.

    Doesn't exist as a pydantic model today — `transaction_id` is just a
    string `Posting`s happen to share. Reified here so it's a real
    foreign-key target instead of an unenforced convention.

    ## The three facts about the event, not about a leg

    `posted_at` and `description` are properties of *what happened*, not of
    either side of it: one purchase happened on one day and says one thing
    on the statement, however many legs record it. They used to be columns
    on `postings`, written identically to every leg of the same transaction
    by every path that produces one (`importers.common.posting_pair`, the
    canonical importer, and `repositories.accounts.insert_manual_transfers`
    all set both legs from the same source row). That made "the two legs of
    one purchase, dated two days apart" and "one transaction with two
    different descriptions" states the schema could hold and no reader
    could mean — the duplication was the bug, not the storage cost.

    Moving them here is the third of the same move `db.triggers` made for
    the zero-sum rule: the balancing invariant, the date, and the
    description are all statements about the *set* of postings, so they
    belong to the row that set is grouped by. A posting is now purely a
    leg: which account, how much, in what currency, filed under what.

    Nothing per-leg was lost. The one place a leg genuinely carries text of
    its own is a user's split of one posting into several
    (`corrections.PostingSplitLeg.description`), which is an overlay row,
    not a posting — and a merge's own replacement text lives on
    `corrections.PostingMerge.description`, keyed by the transaction it
    keeps. Neither was ever written back onto `postings`.

    `ix_transactions_user_posted_at` moved here with the column it indexes.
    Every date-range read in this package (`importers.ingest.load_ledger`'s
    `since`/`until`, and every `as_of` filter downstream of it) now filters
    this table, and it is a strictly better index than the `postings` one
    it replaces: one entry per event rather than one per leg.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(check_in_sql("origin", get_args(TransactionOrigin)), name="origin"),
        UniqueConstraint("user_id", "natural_key", name="uq_transactions_user_natural_key"),
        Index("ix_transactions_user_posted_at", "user_id", "posted_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    posted_at: Mapped[datetime]
    """The one day this event happened on — naive, like every other ledger date in this schema."""
    description: Mapped[str] = mapped_column(default="")
    """What the statement (or the person) said this event was. See the class docstring."""
    origin: Mapped[str] = mapped_column(default="imported")
    """Where this transaction came from — `imported` from a statement, or `manual`, entered by hand.

    The discriminator that lets one `postings` table hold both without a
    parallel mini-ledger beside it (`manual_transfers`, retired). It is
    what `importers.ingest._write_ledger` reads to know which rows a
    rebuild owns: replaying every archived statement recomputes the whole
    `imported` half and prunes whatever it no longer produces, and must
    leave the `manual` half — postings no statement will ever describe
    (see `models.ManualTransfer`) — untouched.

    An `imported` transaction's provenance reference is its archived
    statement, addressed by
    `utils.statement_archive.StatementArchive` under
    `statements/{user_id}/{institution}/{account_id}/{timestamp}` — the
    archive is the source of truth `rebuild_from_raw_statements` and
    `last_import_at` already read, so there is no metadata table
    shadowing it. A `manual` transaction has no provenance reference at
    all, which is the whole of what this column has to distinguish."""


class Posting(Base, Timestamped):
    """One leg of one economic event — one row, like `trades.db.LedgerEvent`.

    Purely a leg: which account, how much, in what currency, filed under
    what. When the event happened and what it said are on the
    `Transaction` this leg belongs to, never repeated here — see that
    class's docstring for why a per-leg copy of either was a state nothing
    could mean.

    Every column here is raw: what the statement said, or what the user
    typed into a manual transfer. Nothing derived or resolved is written
    back onto a posting, which is why re-importing a statement can never
    silently revert an interpretation. `category_id`/`subcategory_id` are
    part of that raw record rather than an exception to it — they are the
    category the *file itself* named (only the canonical importer sets
    them; see `importers.canonical.csv`), written once at import and never
    rewritten. What a posting currently resolves to is a different
    question, answered by the taxonomy's own redirects
    (`repositories.taxonomy.load_category_redirects`) layered over these,
    so a category rename, merge, or delete rewrites no posting at all.

    `budget_id` is a real foreign key into `budgets`, like `category_id`/
    `subcategory_id` — a posting can only ever be attributed to a budget
    that already exists. Nothing in the live app sets this to a non-null
    value today (see `importers.ingest.load_ledger`/`_write_ledger`, the
    only place this column is read or written), but it's kept a real FK
    for the same reason every other natural-key reference in this schema
    is, and so it's ready to use without a follow-up migration if a caller
    eventually needs it.
    """

    __tablename__ = "postings"
    __table_args__ = (
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        *child_of_category_columns("category_id", "subcategory_id"),
        UniqueConstraint("user_id", "natural_key", name="uq_postings_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.transactions.id", ondelete="CASCADE")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.accounts.id"))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str]
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    budget_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.budgets.id"), default=None
    )
    meta: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)


class PostingTag(Base, Timestamped):
    """One (posting, tag) pairing — the normalized replacement for `Posting.tag_ids`.

    A pure association table, so `(user_id, posting_id, tag_id)` *is* its
    primary key. It used to carry a surrogate `uuid id` on top of a
    `UNIQUE` over the same three columns — Karwin's "ID Required"
    (DB-audit D9): a second index to maintain, buying nothing, since
    nothing ever references a pairing by an id of its own.
    """

    __tablename__ = "posting_tags"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.postings.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tags.id", ondelete="CASCADE"), primary_key=True
    )


class OpeningBalance(Base, Timestamped):
    """The balance a real account already had the day before its postings start."""

    __tablename__ = "opening_balances"
    __table_args__ = (
        UniqueConstraint("user_id", "account_id", name="uq_opening_balances_user_account"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.accounts.id", ondelete="CASCADE")
    )
    amount: Mapped[Decimal] = mapped_column(MONEY)
    as_of_date: Mapped[datetime]


class OtherAsset(Base, Timestamped):
    """A manually-entered net-worth line with no transaction history — property, a car, etc.

    `value` is checked non-negative. This is an *asset* line — a house, a
    car, a painting — and never a liability: the schema already models
    those as `credit_card`/`loan` accounts with their own signed postings
    (see `dashboard.net_worth`, which adds every one of these to the asset
    side unconditionally). A negative value here would be silently
    subtracted from assets rather than counted as a debt, so it is the
    kind of "false statement" the engine should refuse. The check is
    scoped deliberately: `postings.amount` is signed by design and gets no
    such constraint.
    """

    __tablename__ = "other_assets"
    __table_args__ = (
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        CheckConstraint("value >= 0", name="value_is_not_negative"),
        UniqueConstraint("user_id", "natural_key", name="uq_other_assets_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    name: Mapped[str]
    value: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(default="USD")
    note: Mapped[str] = mapped_column(default="")
