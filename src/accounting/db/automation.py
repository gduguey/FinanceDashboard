"""Rules and patterns that automatically resolve or suggest a posting's counterparty/category."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, ForeignKeyConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from accounting.db.core import SCHEMA
from db.base import Base


class TransferRule(Base):
    """A user-maintained trigger/action pair for automatically resolving a posting's counterparty and category.

    `account_id`/`counterparty_account_id` deliberately have no foreign key
    to `accounts`, unlike everywhere else an account id is stored: the UI
    supports creating a rule that names a counterparty account (e.g.
    `"employer:eqore"`) *before* that account exists yet, matched against
    postings only once both the rule and the account are eventually there
    (see `ledger.categorization.apply_rules`) — a real, intentional forward
    reference, not a data-integrity gap to close.
    """

    __tablename__ = "transfer_rules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "category_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_transfer_rules_category_id"),
        ),
        ForeignKeyConstraint(
            ["user_id", "subcategory_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_transfer_rules_subcategory_id"),
        ),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    rule_id: Mapped[str] = mapped_column(primary_key=True)
    description_contains: Mapped[str]
    account_id: Mapped[str | None] = mapped_column(default=None)
    category_id: Mapped[str | None] = mapped_column(default=None)
    subcategory_id: Mapped[str | None] = mapped_column(default=None)
    counterparty_account_id: Mapped[str | None] = mapped_column(default=None)
    priority: Mapped[int] = mapped_column(default=0)
    description: Mapped[str] = mapped_column(default="")
    active: Mapped[bool] = mapped_column(default=True)


class CategoryPattern(Base):
    """A user-maintained description-match pattern that *suggests* a category — never applies one silently."""

    __tablename__ = "category_patterns"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "category_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_category_patterns_category_id"),
        ),
        ForeignKeyConstraint(
            ["user_id", "subcategory_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_category_patterns_subcategory_id"),
        ),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    pattern_id: Mapped[str] = mapped_column(primary_key=True)
    description_contains: Mapped[str]
    category_id: Mapped[str]
    subcategory_id: Mapped[str | None] = mapped_column(default=None)
    priority: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(default=True)
