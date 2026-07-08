"""User corrections layered on top of the ledger: overrides, splits, merges, and dismissed suggestions.

None of these are ever baked into a posting itself — re-importing a
statement or rebuilding from raw archives can never silently erase one,
matching the reasoning already documented on each pydantic model in
`accounting.models`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import get_args

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, ForeignKeyConstraint, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from accounting.db.core import SCHEMA
from accounting.models import PendingSuggestionSource
from db.base import MONEY, Base, check_in_sql


class ManualOverride(Base):
    """A user's direct edit to one posting, always winning over whatever a rule would have produced.

    `tag_ids_override` stays a nullable array rather than a join table:
    unlike `PostingTag`, this is a sparse *patch* where `NULL` means "no
    override" and `[]` means "override to no tags" — not a canonical list
    of entities to join against.
    """

    __tablename__ = "manual_overrides"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "posting_id"], [f"{SCHEMA}.postings.user_id", f"{SCHEMA}.postings.posting_id"]
        ),
        ForeignKeyConstraint(
            ["user_id", "account_id"], [f"{SCHEMA}.accounts.user_id", f"{SCHEMA}.accounts.account_id"]
        ),
        ForeignKeyConstraint(
            ["user_id", "category_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_manual_overrides_category_id"),
        ),
        ForeignKeyConstraint(
            ["user_id", "subcategory_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_manual_overrides_subcategory_id"),
        ),
        CheckConstraint(check_in_sql("pending_source", get_args(PendingSuggestionSource)), name="pending_source"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    posting_id: Mapped[str] = mapped_column(primary_key=True)
    account_id: Mapped[str | None] = mapped_column(default=None)
    category_id: Mapped[str | None] = mapped_column(default=None)
    subcategory_id: Mapped[str | None] = mapped_column(default=None)
    tag_ids_override: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=None)
    pending_source: Mapped[str | None] = mapped_column(default=None)
    pending_selected: Mapped[bool] = mapped_column(default=True)
    pending_previous_category_id: Mapped[str | None] = mapped_column(default=None)
    pending_previous_subcategory_id: Mapped[str | None] = mapped_column(default=None)


class PostingSplit(Base):
    """A user's decision to break one posting into several legs, keyed by the original posting's id."""

    __tablename__ = "posting_splits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "posting_id"], [f"{SCHEMA}.postings.user_id", f"{SCHEMA}.postings.posting_id"]
        ),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    posting_id: Mapped[str] = mapped_column(primary_key=True)


class PostingSplitLeg(Base):
    """One piece of a posting split into several independently-categorized legs.

    `ordinal` preserves the legs' display order, since the amounts they
    were entered in matters to the user even though it's semantically
    unordered relative to `PostingSplitLeg.amount` summing to the original.
    """

    __tablename__ = "posting_split_legs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "posting_id"], [f"{SCHEMA}.posting_splits.user_id", f"{SCHEMA}.posting_splits.posting_id"]
        ),
        ForeignKeyConstraint(
            ["user_id", "category_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_posting_split_legs_category_id"),
        ),
        ForeignKeyConstraint(
            ["user_id", "subcategory_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_posting_split_legs_subcategory_id"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    posting_id: Mapped[str]
    ordinal: Mapped[int]
    amount: Mapped[float] = mapped_column(MONEY)
    category_id: Mapped[str | None] = mapped_column(default=None)
    subcategory_id: Mapped[str | None] = mapped_column(default=None)
    description: Mapped[str] = mapped_column(default="")


class PostingMerge(Base):
    """A user's decision that two or more imported transactions are the same real-world event, recorded twice."""

    __tablename__ = "posting_merges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "kept_transaction_id"],
            [f"{SCHEMA}.transactions.user_id", f"{SCHEMA}.transactions.transaction_id"],
        ),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    merge_id: Mapped[str] = mapped_column(primary_key=True)
    kept_transaction_id: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)


class PostingMergeDuplicate(Base):
    """One transaction dropped from the resolved ledger because a `PostingMerge` kept a different one instead."""

    __tablename__ = "posting_merge_duplicates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "merge_id"], [f"{SCHEMA}.posting_merges.user_id", f"{SCHEMA}.posting_merges.merge_id"]
        ),
        ForeignKeyConstraint(
            ["user_id", "duplicate_transaction_id"],
            [f"{SCHEMA}.transactions.user_id", f"{SCHEMA}.transactions.transaction_id"],
        ),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    merge_id: Mapped[str] = mapped_column(primary_key=True)
    duplicate_transaction_id: Mapped[str] = mapped_column(primary_key=True)


class DismissedSuggestion(Base):
    """A user's decision that an auto-detected suggestion isn't relevant, archived rather than discarded."""

    __tablename__ = "dismissed_suggestions"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    suggestion_id: Mapped[str] = mapped_column(primary_key=True)
    kind: Mapped[str]
    description: Mapped[str]
    dismissed_at: Mapped[datetime]
