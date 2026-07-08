"""Per-month and general (every-month-alike) spending targets."""

from __future__ import annotations

import uuid
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from accounting.db.core import SCHEMA
from accounting.models import CurrencyCode
from db.base import MONEY, Base, check_in_sql


class Budget(Base):
    """One month's spending target for one top-level expense category, or one of its subcategories."""

    __tablename__ = "budgets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "category_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_budgets_category_id"),
        ),
        ForeignKeyConstraint(
            ["user_id", "subcategory_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_budgets_subcategory_id"),
        ),
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        # A plain UniqueConstraint on a nullable column doesn't work here: Postgres
        # treats every NULL as distinct, so two "whole category, no subcategory"
        # budgets for the same month would silently both be allowed. Coalescing
        # subcategory_id to '' inside the index closes that gap.
        Index(
            "uq_budgets_user_month_category",
            "user_id",
            "month",
            "category_id",
            text("coalesce(subcategory_id, '')"),
            unique=True,
        ),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    budget_id: Mapped[str] = mapped_column(primary_key=True)
    month: Mapped[str]
    category_id: Mapped[str]
    subcategory_id: Mapped[str | None] = mapped_column(default=None)
    amount: Mapped[float] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(default="USD")


class GeneralBudget(Base):
    """A category's (or subcategory's) spending target applied to every month alike.

    Uses a surrogate `id` rather than `(user_id, category_id, subcategory_id)`
    as its primary key because `subcategory_id` is nullable, and a primary
    key can't contain `NULL` — see `DATABASE_SCHEMA.md`. The unique index
    below (coalescing `subcategory_id` to `''`) is what actually enforces
    "one general budget per category/subcategory".
    """

    __tablename__ = "general_budgets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "category_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_general_budgets_category_id"),
        ),
        ForeignKeyConstraint(
            ["user_id", "subcategory_id"],
            [f"{SCHEMA}.categories.user_id", f"{SCHEMA}.categories.category_id"],
            name=conv("fk_general_budgets_subcategory_id"),
        ),
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        Index(
            "uq_general_budgets_user_category_subcategory",
            "user_id",
            "category_id",
            text("coalesce(subcategory_id, '')"),
            unique=True,
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    category_id: Mapped[str]
    subcategory_id: Mapped[str | None] = mapped_column(default=None)
    amount: Mapped[float] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(default="USD")
