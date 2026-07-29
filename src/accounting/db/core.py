"""The core ledger: accounts, categories, tags, transactions, and their postings.

Every table's primary key is a surrogate `id`, never `(user_id, ..._id)` —
see `db.base.derive_id`'s docstring for why a deterministic hash of the old
human-chosen string, not a random default, is what makes that safe for
tables `accounting.store` rewrites wholesale on every save. `natural_key`
is that human-chosen string (what used to be `account_id`, `category_id`,
...), kept as a plain column with a `UNIQUE(user_id, natural_key)`
constraint instead of being the primary key itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import get_args

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.models import AccountKind, CategoryClassification, CurrencyCode, TransactionOrigin
from accounting.precedence import OverlayStage
from db.base import MONEY, Base, Timestamped, check_in_sql

SCHEMA = "accounting"


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
    """One place money can sit or be attributed to — a real account, a vault, or a virtual counterparty."""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(check_in_sql("kind", get_args(AccountKind)), name="kind"),
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        UniqueConstraint("user_id", "natural_key", name="uq_accounts_user_natural_key"),
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
    parent_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.accounts.id"), default=None
    )
    external_ref: Mapped[str | None] = mapped_column(default=None)
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
    """

    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint(check_in_sql("classification", get_args(CategoryClassification)), name="classification"),
        CheckConstraint(
            "superseded_by_category_id IS NULL OR retired_at IS NOT NULL", name="successor_requires_retirement"
        ),
        UniqueConstraint("user_id", "natural_key", name="uq_categories_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    name: Mapped[str]
    classification: Mapped[str]
    parent_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
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
    """One economic event, grouping the postings that are its legs.

    Doesn't exist as a pydantic model today — `transaction_id` is just a
    string `Posting`s happen to share. Reified here so it's a real
    foreign-key target instead of an unenforced convention.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(check_in_sql("origin", get_args(TransactionOrigin)), name="origin"),
        UniqueConstraint("user_id", "natural_key", name="uq_transactions_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
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
        UniqueConstraint("user_id", "natural_key", name="uq_postings_user_natural_key"),
        Index("ix_postings_user_posted_at", "user_id", "posted_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.transactions.id", ondelete="CASCADE")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.accounts.id"))
    posted_at: Mapped[datetime]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str]
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    budget_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.budgets.id"), default=None
    )
    description: Mapped[str] = mapped_column(default="")
    meta: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)


class PostingTag(Base, Timestamped):
    """One (posting, tag) pairing — the normalized replacement for `Posting.tag_ids`."""

    __tablename__ = "posting_tags"
    __table_args__ = (
        UniqueConstraint("user_id", "posting_id", "tag_id", name="uq_posting_tags_user_posting_tag"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.postings.id", ondelete="CASCADE")
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tags.id", ondelete="CASCADE"))


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
    """A manually-entered net-worth line with no transaction history — property, a car, etc."""

    __tablename__ = "other_assets"
    __table_args__ = (
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
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
