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

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
from accounting.models import PendingSuggestionSource
from db.base import MONEY, Base, check_in_sql


class PostingOverride(Base):
    """A user's direct edit to one posting, always winning over whatever a rule would have produced.

    Split from the old combined `manual_overrides` table:
    `PostingPendingSuggestion` below now owns the transient
    "an automation suggested this, not yet confirmed" state — a
    correction here is persistent until the user changes it again, a
    different lifecycle from a pending suggestion that gets deleted
    outright once resolved, not left with a cleared set of columns.

    `tag_ids_override` stays a nullable array of tag `natural_key`s rather
    than a join table: unlike `PostingTag`, this is a sparse *patch* where
    `NULL` means "no override" and `[]` means "override to no tags" — not
    a canonical list of entities to join against.
    """

    __tablename__ = "posting_overrides"
    __table_args__ = (
        UniqueConstraint("user_id", "posting_id", name="uq_posting_overrides_user_posting"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.postings.id", ondelete="CASCADE")
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.accounts.id"), default=None
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    tag_ids_override: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=None)


class PostingPendingSuggestion(Base):
    """A not-yet-confirmed automated suggestion for one posting — deleted outright once resolved.

    Existence of a row here *is* "this posting is pending" — no more
    `pending_source IS NOT NULL` checks against a column that's also used
    for persistent corrections. Accepting a suggestion deletes this row
    (and upserts `PostingOverride` with the same category/subcategory);
    rejecting it deletes this row and touches nothing else, since the
    posting's real category was never changed to begin with (see
    `ledger.pending`).
    """

    __tablename__ = "posting_pending_suggestions"
    __table_args__ = (
        CheckConstraint(check_in_sql("source", get_args(PendingSuggestionSource)), name="source"),
        UniqueConstraint("user_id", "posting_id", name="uq_posting_pending_suggestions_user_posting"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.postings.id", ondelete="CASCADE")
    )
    source: Mapped[str]
    selected: Mapped[bool] = mapped_column(default=True)
    previous_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    previous_subcategory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )


class PostingSplit(Base):
    """A user's decision to break one posting into several legs, keyed by the original posting's id."""

    __tablename__ = "posting_splits"
    __table_args__ = (
        UniqueConstraint("user_id", "posting_id", name="uq_posting_splits_user_posting"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.postings.id", ondelete="CASCADE")
    )


class PostingSplitLeg(Base):
    """One piece of a posting split into several independently-categorized legs.

    `ordinal` preserves the legs' display order, since the amounts they
    were entered in matters to the user even though it's semantically
    unordered relative to `PostingSplitLeg.amount` summing to the original.
    """

    __tablename__ = "posting_split_legs"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    posting_split_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.posting_splits.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int]
    amount: Mapped[float] = mapped_column(MONEY)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    description: Mapped[str] = mapped_column(default="")


class PostingMerge(Base):
    """A user's decision that two or more imported transactions are the same real-world event, recorded twice."""

    __tablename__ = "posting_merges"
    __table_args__ = (
        UniqueConstraint("user_id", "natural_key", name="uq_posting_merges_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    kept_transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.transactions.id"))
    description: Mapped[str | None] = mapped_column(default=None)


class PostingMergeDuplicate(Base):
    """One transaction dropped from the resolved ledger because a `PostingMerge` kept a different one instead."""

    __tablename__ = "posting_merge_duplicates"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "merge_id", "duplicate_transaction_id", name="uq_posting_merge_duplicates_user_merge_txn"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    merge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.posting_merges.id", ondelete="CASCADE")
    )
    duplicate_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.transactions.id")
    )


class DismissedSuggestion(Base):
    """A user's decision that an auto-detected suggestion isn't relevant, archived rather than discarded.

    `natural_key` is a stable key derived from the suggestion's own
    content (see `api._transfer_suggestion_id`/`_duplicate_suggestion_id`),
    not a random choice — the same real-world pair or group always
    dismisses and restores under the same `id` via `db.base.derive_id`,
    regardless of how many times the detector recomputes it.
    """

    __tablename__ = "dismissed_suggestions"
    __table_args__ = (
        UniqueConstraint("user_id", "natural_key", name="uq_dismissed_suggestions_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    kind: Mapped[str]
    description: Mapped[str]
    dismissed_at: Mapped[datetime]
