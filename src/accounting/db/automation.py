"""Rules and patterns that automatically resolve or suggest a posting's counterparty/category."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
from db.base import Base


class TransferRule(Base):
    """A user-maintained trigger/action pair for automatically resolving a posting's counterparty and category.

    `account_id`/`counterparty_account_id` deliberately have no foreign key
    to `accounts`, unlike `category_id`/`subcategory_id`: the UI supports
    creating a rule that names a counterparty account (e.g.
    `"employer:eqore"`) *before* that account exists yet, matched against
    postings only once both the rule and the account are eventually there
    (see `ledger.categorization.apply_rules`) — a real, intentional forward
    reference that may span separate requests entirely, which even a
    `DEFERRABLE` constraint can't accommodate (that only reorders checks
    within one transaction, not across two). So these two stay bare
    strings holding an account's `natural_key` directly, never a derived
    FK column.
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
    account_id: Mapped[str | None] = mapped_column(default=None)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    counterparty_account_id: Mapped[str | None] = mapped_column(default=None)
    priority: Mapped[int] = mapped_column(default=0)
    description: Mapped[str] = mapped_column(default="")
    active: Mapped[bool] = mapped_column(default=True)


class CategoryPattern(Base):
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
