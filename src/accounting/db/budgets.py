"""Per-month and general (every-month-alike) spending targets."""

from __future__ import annotations

import uuid
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
from accounting.models import CurrencyCode
from db.base import MONEY, Base, check_in_sql

_NIL_SUBCATEGORY = "00000000-0000-0000-0000-000000000000"
"""Sentinel `coalesce`d in place of a `NULL` `subcategory_id` in the unique indexes below.

Postgres treats every `NULL` as distinct, so a plain `UNIQUE` on a nullable
column would silently allow two "whole category, no subcategory" budgets
for the same month. Coalescing to this fixed, never-real UUID inside the
index closes that gap — same technique the original string-keyed schema
used with `coalesce(subcategory_id, '')`.
"""


class Budget(Base):
    """One month's spending target for one top-level expense category, or one of its subcategories."""

    __tablename__ = "budgets"
    __table_args__ = (
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        UniqueConstraint("user_id", "natural_key", name="uq_budgets_user_natural_key"),
        Index(
            "uq_budgets_user_month_category",
            "user_id",
            "month",
            "category_id",
            text(f"coalesce(subcategory_id, '{_NIL_SUBCATEGORY}'::uuid)"),
            unique=True,
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    month: Mapped[str]
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"))
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    amount: Mapped[float] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(default="USD")


class GeneralBudget(Base):
    """A category's (or subcategory's) spending target applied to every month alike.

    Uses a random surrogate `id` rather than deriving one from
    `(category_id, subcategory_id)`: nothing else ever foreign-keys
    against a `GeneralBudget` row, so there's no cross-reference that
    needs it to stay stable across a rewrite — the unique index below
    (coalescing `subcategory_id` to a fixed sentinel) is what actually
    enforces "one general budget per category/subcategory".
    """

    __tablename__ = "general_budgets"
    __table_args__ = (
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        Index(
            "uq_general_budgets_user_category_subcategory",
            "user_id",
            "category_id",
            text(f"coalesce(subcategory_id, '{_NIL_SUBCATEGORY}'::uuid)"),
            unique=True,
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"))
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    amount: Mapped[float] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(default="USD")
