"""Spending targets — per-month, or general (every-month-alike) when `month` is `NULL`."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA, child_of_category_columns
from accounting.models import CurrencyCode
from db.base import MONEY, Base, Timestamped, check_in_sql

_NIL_SUBCATEGORY = "00000000-0000-0000-0000-000000000000"
"""Sentinel `coalesce`d in place of a `NULL` `subcategory_id` in the unique index below.

Postgres treats every `NULL` as distinct, so a plain `UNIQUE` on a nullable
column would silently allow two "whole category, no subcategory" budgets
for the same month. Coalescing to this fixed, never-real UUID inside the
index closes that gap — same technique the original string-keyed schema
used with `coalesce(subcategory_id, '')`, and the same reason `month` is
coalesced to `''` there too.
"""


class Budget(Base, Timestamped):
    """One spending target for one top-level expense category, or one of its subcategories.

    A `NULL` `month` is the *general* target — the standing amount that
    applies to every month alike — and a `"YYYY-MM"` month scopes the
    target to that one month. They used to be two tables (`budgets` and
    `general_budgets`) holding the same four columns; they are one table
    with a nullable `month` now.

    The unique index coalesces `month` to `''` alongside `subcategory_id`
    to its sentinel, so a month target and a general target for the same
    `(category, subcategory)` pair coexist as two rows while neither kind
    can be duplicated.
    """

    __tablename__ = "budgets"
    __table_args__ = (
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        CheckConstraint("amount >= 0", name="amount_is_not_negative"),
        *child_of_category_columns("category_id", "subcategory_id"),
        UniqueConstraint("user_id", "natural_key", name="uq_budgets_user_natural_key"),
        Index(
            "uq_budgets_user_month_category",
            "user_id",
            text("coalesce(month, '')"),
            "category_id",
            text(f"coalesce(subcategory_id, '{_NIL_SUBCATEGORY}'::uuid)"),
            unique=True,
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    month: Mapped[str | None] = mapped_column(default=None)
    """`"YYYY-MM"` for one month's target; `NULL` for the general, every-month-alike one."""
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"))
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    """A spending target, so never negative — a budget of "minus fifty dollars" is not a thing to plan for."""
    currency: Mapped[str] = mapped_column(default="USD")
