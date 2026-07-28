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
from datetime import UTC, datetime
from typing import get_args

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.models import AccountKind, CategoryClassification, CurrencyCode
from db.base import MONEY, Base, check_in_sql

SCHEMA = "accounting"


class Account(Base):
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


class Category(Base):
    """One node in the two-level category tree: a top-level category, or a subcategory of one."""

    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint(check_in_sql("classification", get_args(CategoryClassification)), name="classification"),
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


class Tag(Base):
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


class Transaction(Base):
    """One economic event, grouping the postings that are its legs.

    Doesn't exist as a pydantic model today — `transaction_id` is just a
    string `Posting`s happen to share. Reified here so it's a real
    foreign-key target instead of an unenforced convention.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "natural_key", name="uq_transactions_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class Posting(Base):
    """One leg of one economic event — one row, like `trades.db.LedgerEvent`.

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
    amount: Mapped[float] = mapped_column(MONEY)
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


class PostingTag(Base):
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


class OpeningBalance(Base):
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
    amount: Mapped[float] = mapped_column(MONEY)
    as_of_date: Mapped[datetime]


class ManualTransfer(Base):
    """A user-recorded transfer between two of their own accounts, never derived from an import."""

    __tablename__ = "manual_transfers"
    __table_args__ = (
        UniqueConstraint("user_id", "natural_key", name="uq_manual_transfers_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    date: Mapped[datetime]
    from_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.accounts.id"))
    to_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.accounts.id"))
    from_amount: Mapped[float] = mapped_column(MONEY)
    to_amount: Mapped[float] = mapped_column(MONEY)
    description: Mapped[str] = mapped_column(default="")


class OtherAsset(Base):
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
    value: Mapped[float] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(default="USD")
    note: Mapped[str] = mapped_column(default="")
