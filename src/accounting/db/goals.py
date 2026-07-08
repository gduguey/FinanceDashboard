"""Savings goals, their contributions, and the automations that fund/draw them down."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
from accounting.models import (
    CurrencyCode,
    GoalContributionOrigin,
    RecurringAdditionFrequency,
    RecurringAdditionMode,
)
from db.base import MONEY, Base, check_in_sql


class Goal(Base):
    """A savings target — its balance is never stored here, only derived from its `GoalContribution`s."""

    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint(check_in_sql("target_currency", get_args(CurrencyCode)), name="target_currency"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    goal_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    target_amount: Mapped[float] = mapped_column(MONEY)
    target_currency: Mapped[str] = mapped_column(default="USD")
    target_date: Mapped[datetime]
    color: Mapped[str]
    created_at: Mapped[datetime]


class GoalContribution(Base):
    """One dated, signed allocation into (or withdrawal from) a goal — the only thing a goal's balance derives from.

    `goal_id` deliberately has no foreign key to `goals` — like
    `TransferRule`'s account fields, `PUT /goal-contributions` replaces its
    whole list wholesale with no server-side existence check today (see
    `api.put_goal_contributions`), so enforcing one here would reject
    requests the app itself has always accepted.
    """

    __tablename__ = "goal_contributions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "source_posting_id"], [f"{SCHEMA}.postings.user_id", f"{SCHEMA}.postings.posting_id"]
        ),
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        CheckConstraint(check_in_sql("origin", get_args(GoalContributionOrigin)), name="origin"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    contribution_id: Mapped[str] = mapped_column(primary_key=True)
    goal_id: Mapped[str]
    date: Mapped[datetime]
    amount: Mapped[float] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(default="USD")
    note: Mapped[str] = mapped_column(default="")
    source_posting_id: Mapped[str | None] = mapped_column(default=None)
    origin: Mapped[str] = mapped_column(default="manual")
    edited: Mapped[bool] = mapped_column(default=False)


class RecurringAddition(Base):
    """One ordered rule for automatically allocating unallocated money into a goal on a recurring schedule.

    `goal_id` has no foreign key to `goals` — see `GoalContribution`'s own
    docstring; `PUT /recurring-additions` is the same "replace the whole
    list, no existence check" shape.
    """

    __tablename__ = "recurring_additions"
    __table_args__ = (
        CheckConstraint(check_in_sql("frequency", get_args(RecurringAdditionFrequency)), name="frequency"),
        CheckConstraint(check_in_sql("mode", get_args(RecurringAdditionMode)), name="mode"),
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    addition_id: Mapped[str] = mapped_column(primary_key=True)
    goal_id: Mapped[str]
    start_date: Mapped[date]
    frequency: Mapped[str]
    end_date: Mapped[date | None] = mapped_column(default=None)
    mode: Mapped[str]
    value: Mapped[float] = mapped_column(MONEY, default=0)
    currency: Mapped[str] = mapped_column(default="USD")
    priority: Mapped[int] = mapped_column(default=0)


class WithdrawalPriorityEntry(Base):
    """One goal's place in the order goals are drawn down from when unallocated money goes negative.

    `goal_id` has no foreign key to `goals` — see `GoalContribution`'s own
    docstring; `PUT /withdrawal-priorities` is the same "replace the whole
    list, no existence check" shape.
    """

    __tablename__ = "withdrawal_priority_entries"
    __table_args__ = ({"schema": SCHEMA},)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    goal_id: Mapped[str] = mapped_column(primary_key=True)
    priority: Mapped[int] = mapped_column(default=0)
