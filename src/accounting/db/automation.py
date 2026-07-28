"""Rules and patterns that automatically resolve a posting's counterparty, or suggest a category."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
from db.base import Base, Timestamped


class TransferRule(Base, Timestamped):
    """A user-maintained trigger/action pair for automatically resolving a posting's counterparty.

    `account_id`/`counterparty_account_id` are real foreign keys into
    `accounts` — a rule can only ever name an account (real or virtual)
    that already exists. Creating a rule for a counterparty that doesn't
    exist yet requires creating that account first (see `Account`); there
    is no forward-reference case left to accommodate (see migration that
    introduced this constraint for the rationale behind dropping the old,
    unenforced string columns).
    """

    __tablename__ = "transfer_rules"
    __table_args__ = (
        UniqueConstraint("user_id", "natural_key", name="uq_transfer_rules_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    description_contains: Mapped[str]
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.accounts.id"), default=None
    )
    counterparty_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.accounts.id"), default=None
    )
    priority: Mapped[int] = mapped_column(default=0)
    description: Mapped[str] = mapped_column(default="")
    active: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=1)
    """Bumped by `db.base.check_and_bump_row_version` on every `PATCH /transfer-rules/{rule_id}` — see that
    function's own docstring. Never touched by `save_store`'s upsert path for this table (see
    `accounting.store._upsert_transfer_rules_and_prune`), so an unrelated create/reorder elsewhere never
    invalidates a version a client already has in hand."""


class TransferRuleExclusion(Base, Timestamped):
    """One transaction opted out of matching one otherwise-applicable `TransferRule`.

    Mirrors `PostingMergeDuplicate`'s own shape (a join table with a real
    foreign key into `transactions`, not a JSON array of ids) for the same
    reason: `transaction_id` values are deterministic (`db.base.derive_id`),
    so a lasting reference into `transactions` survives a ledger rebuild.
    """

    __tablename__ = "transfer_rule_exclusions"
    __table_args__ = (
        UniqueConstraint("user_id", "rule_id", "transaction_id", name="uq_transfer_rule_exclusions_user_rule_txn"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.transfer_rules.id", ondelete="CASCADE")
    )
    # CASCADE here is safe (unlike TransferLinkedTransaction/PostingMerge's
    # kept_transaction_id): this row is a single, standalone exclusion, not
    # one half of a pair — nothing else needs to go with it when its
    # transaction is pruned by a ledger rebuild (see
    # `importers.ingest._write_ledger`).
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.transactions.id", ondelete="CASCADE")
    )


class CategoryPattern(Base, Timestamped):
    """A user-maintained description-match pattern that *suggests* a category — never applies one silently."""

    __tablename__ = "category_patterns"
    __table_args__ = (
        UniqueConstraint("user_id", "natural_key", name="uq_category_patterns_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    description_contains: Mapped[str]
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"))
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    priority: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=1)
    """Bumped by `db.base.check_and_bump_row_version` on every `PATCH /category-patterns/{pattern_id}` —
    never touched by `save_store`'s upsert path (see `accounting.store._upsert_category_patterns_and_prune`),
    the same shape `TransferRule.version` follows."""
