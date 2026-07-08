"""The core ledger: accounts, categories, tags, transactions, and their postings.

Mirrors `accounting.models`' `Account`/`Category`/`Tag`/`Posting` one for
one — see `DATABASE_SCHEMA.md` for the full design rationale (composite
`(user_id, ..._id)` primary keys, `transactions` reified as a real table,
`tag_ids` normalized into a join table instead of an array).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from accounting.models import AccountKind, CategoryClassification, CurrencyCode
from db.base import MONEY, Base, check_in_sql

SCHEMA = "accounting"


class Account(Base):
    """One place money can sit or be attributed to — a real account, a vault, or a virtual counterparty."""

    __tablename__ = "accounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "parent_account_id"], [f"{SCHEMA}.accounts.user_id", f"{SCHEMA}.accounts.account_id"]
        ),
        CheckConstraint(check_in_sql("kind", get_args(AccountKind)), name="kind"),
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    kind: Mapped[str]
    institution: Mapped[str]
    currency: Mapped[str]
    parent_account_id: Mapped[str | None] = mapped_column(default=None)
    external_ref: Mapped[str | None] = mapped_column(default=None)
    meta: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    closed: Mapped[bool] = mapped_column(default=False)


class Category(Base):
    """One node in the two-level category tree: a top-level category, or a subcategory of one."""

    __tablename__ = "categories"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "parent_category_id"], [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"]
        ),
        CheckConstraint(check_in_sql("classification", get_args(CategoryClassification)), name="classification"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    classification: Mapped[str]
    parent_category_id: Mapped[str | None] = mapped_column(default=None)
    color: Mapped[str]


class Tag(Base):
    """A cross-cutting label — a trip, a move, an event — independent of the category tree."""

    __tablename__ = "tags"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]


class Transaction(Base):
    """One economic event, grouping the postings that are its legs.

    Doesn't exist as a pydantic model today — `transaction_id` is just a
    string `Posting`s happen to share. Reified here so it's a real
    foreign-key target instead of an unenforced convention (see
    `DATABASE_SCHEMA.md`).
    """

    __tablename__ = "transactions"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    transaction_id: Mapped[str] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class Posting(Base):
    """One leg of one economic event — one row, like `trades.db.LedgerEvent`."""

    __tablename__ = "postings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "transaction_id"], [f"{SCHEMA}.transactions.user_id", f"{SCHEMA}.transactions.transaction_id"]
        ),
        ForeignKeyConstraint(
            ["user_id", "account_id"], [f"{SCHEMA}.accounts.user_id", f"{SCHEMA}.accounts.account_id"]
        ),
        ForeignKeyConstraint(
            ["user_id", "category_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_postings_category_id"),
        ),
        ForeignKeyConstraint(
            ["user_id", "subcategory_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_postings_subcategory_id"),
        ),
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    posting_id: Mapped[str] = mapped_column(primary_key=True)
    transaction_id: Mapped[str]
    account_id: Mapped[str]
    posted_at: Mapped[datetime]
    amount: Mapped[float] = mapped_column(MONEY)
    currency: Mapped[str]
    category_id: Mapped[str | None] = mapped_column(default=None)
    subcategory_id: Mapped[str | None] = mapped_column(default=None)
    budget_id: Mapped[str | None] = mapped_column(default=None)
    description: Mapped[str] = mapped_column(default="")
    meta: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)


class PostingTag(Base):
    """One (posting, tag) pairing — the normalized replacement for `Posting.tag_ids`."""

    __tablename__ = "posting_tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "posting_id"], [f"{SCHEMA}.postings.user_id", f"{SCHEMA}.postings.posting_id"]
        ),
        ForeignKeyConstraint(["user_id", "tag_id"], [f"{SCHEMA}.tags.user_id", f"{SCHEMA}.tags.tag_id"]),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    posting_id: Mapped[str] = mapped_column(primary_key=True)
    tag_id: Mapped[str] = mapped_column(primary_key=True)


class OpeningBalance(Base):
    """The balance a real account already had the day before its postings start."""

    __tablename__ = "opening_balances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "account_id"], [f"{SCHEMA}.accounts.user_id", f"{SCHEMA}.accounts.account_id"]
        ),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[str] = mapped_column(primary_key=True)
    amount: Mapped[float] = mapped_column(MONEY)
    as_of_date: Mapped[datetime]


class ManualTransfer(Base):
    """A user-recorded transfer between two of their own accounts, never derived from an import."""

    __tablename__ = "manual_transfers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "from_account_id"],
            [f"{SCHEMA}.accounts.user_id", f"{SCHEMA}.accounts.account_id"],
            name=conv("fk_manual_transfers_from_account_id"),
        ),
        ForeignKeyConstraint(
            ["user_id", "to_account_id"],
            [f"{SCHEMA}.accounts.user_id", f"{SCHEMA}.accounts.account_id"],
            name=conv("fk_manual_transfers_to_account_id"),
        ),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    transfer_id: Mapped[str] = mapped_column(primary_key=True)
    date: Mapped[datetime]
    from_account_id: Mapped[str]
    to_account_id: Mapped[str]
    from_amount: Mapped[float] = mapped_column(MONEY)
    to_amount: Mapped[float] = mapped_column(MONEY)
    description: Mapped[str] = mapped_column(default="")


class OtherAsset(Base):
    """A manually-entered net-worth line with no transaction history — property, a car, etc."""

    __tablename__ = "other_assets"
    __table_args__ = (
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    asset_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    value: Mapped[float] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(default="USD")
    note: Mapped[str] = mapped_column(default="")
