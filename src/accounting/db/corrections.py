"""User corrections layered on top of the ledger: overrides, splits, merges, and dismissed suggestions.

None of these are ever baked into a posting itself — re-importing a
statement or rebuilding from raw archives can never silently erase one,
matching the reasoning already documented on each pydantic model in
`accounting.models`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import get_args

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
from accounting.models import PendingSuggestionSource
from db.base import MONEY, Base, Timestamped, check_in_sql


class PostingOverride(Base, Timestamped):
    """A user's direct edit to one posting, always winning over whatever a rule would have produced.

    Split from the old combined `manual_overrides` table:
    `PostingPendingSuggestion` below now owns the transient
    "an automation suggested this, not yet confirmed" state — a
    correction here is persistent until the user changes it again, a
    different lifecycle from a pending suggestion that gets deleted
    outright once resolved, not left with a cleared set of columns.

    A posting's overridden tag set lives in `PostingOverrideTag` below, a
    real FK-enforced join table, not an array column here — unlike a
    single-valued field like `category_id`, "which tags" is inherently a
    set, and Postgres has no way to enforce "every element of an array
    references a real row" the way it enforces a scalar `ForeignKey`.
    `tags_overridden` exists on this row specifically because a join
    table's row *count* alone can't distinguish "no override" (0 rows,
    ignore this posting's tags entirely) from "overridden to no tags" (0
    rows, but deliberately so) — this flag is that missing bit; `True`
    with zero matching `PostingOverrideTag` rows is exactly the "override
    to no tags" case, `False` means "don't consult
    `PostingOverrideTag` for this posting at all."
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
    tags_overridden: Mapped[bool] = mapped_column(default=False)


class PostingOverrideTag(Base, Timestamped):
    """One tag in a posting override's overridden tag set — the FK-enforced replacement for a loose id array.

    Mirrors `accounting.db.core.PostingTag` exactly, one row per
    (override, tag) pair; see `PostingOverride.tags_overridden`'s own
    docstring for why the override's own "was this touched at all" state
    still needs a separate boolean rather than being inferred from
    whether any rows exist here.
    """

    __tablename__ = "posting_override_tags"
    __table_args__ = (
        UniqueConstraint("user_id", "override_id", "tag_id", name="uq_posting_override_tags_user_override_tag"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    override_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.posting_overrides.id", ondelete="CASCADE")
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tags.id", ondelete="CASCADE"))


class PostingPendingSuggestion(Base, Timestamped):
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


class PostingSplit(Base, Timestamped):
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


class PostingSplitLeg(Base, Timestamped):
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
    amount: Mapped[Decimal] = mapped_column(MONEY)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    description: Mapped[str] = mapped_column(default="")


class PostingMerge(Base, Timestamped):
    """A user's decision that two or more imported transactions are the same real-world event, recorded twice."""

    __tablename__ = "posting_merges"
    __table_args__ = (
        UniqueConstraint("user_id", "natural_key", name="uq_posting_merges_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    # CASCADE is safe here: this row itself references its kept transaction
    # directly (unlike TransferLink, which only relates to a transaction
    # through its TransferLinkedTransaction children) — pruning the kept
    # transaction in a ledger rebuild (see `importers.ingest._write_ledger`)
    # correctly deletes the whole merge decision, which itself cascades to
    # PostingMergeDuplicate below via merge_id.
    kept_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.transactions.id", ondelete="CASCADE")
    )
    description: Mapped[str | None] = mapped_column(default=None)


class PostingMergeDuplicate(Base, Timestamped):
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
    # CASCADE is safe: this row is a single duplicate's own membership in
    # the merge, not the merge's defining reference (that's
    # PostingMerge.kept_transaction_id, above) — pruning just one duplicate
    # transaction in a ledger rebuild should only ever drop its own row,
    # never the merge or its other duplicates.
    duplicate_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.transactions.id", ondelete="CASCADE")
    )


class DismissedSuggestion(Base, Timestamped):
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
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
