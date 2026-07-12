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

    `account_id`/`counterparty_account_id` are real foreign keys into
    `accounts`, exactly like `category_id`/`subcategory_id` — a rule can
    only ever name an account (real or virtual) that already exists.
    Creating a rule for a counterparty that doesn't exist yet requires
    creating that account first (see `Account`), the same way a rule's
    category must already exist; there is no forward-reference case left
    to accommodate (see migration that introduced this constraint for the
    rationale behind dropping the old, unenforced string columns).
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
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    counterparty_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.accounts.id"), default=None
    )
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
