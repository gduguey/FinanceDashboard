"""Savings goals, their contributions, and the automations that fund/draw them down."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
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
        UniqueConstraint("user_id", "natural_key", name="uq_goals_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    name: Mapped[str]
    target_amount: Mapped[float] = mapped_column(MONEY)
    target_currency: Mapped[str] = mapped_column(default="USD")
    target_date: Mapped[datetime]
    color: Mapped[str]
    created_at: Mapped[datetime]


class GoalContribution(Base):
    """One dated, signed allocation into (or withdrawal from) a goal — the only thing a goal's balance derives from.

    `goal_id` deliberately has no foreign key to `goals` — like
    `TransferRule.account_id`, `PUT /goal-contributions` replaces its
    whole list wholesale with no server-side existence check today (see
    `api.put_goal_contributions`), so enforcing one here would reject
    requests the app itself has always accepted. Stays a bare string
    holding the goal's `natural_key` directly.
    """

    __tablename__ = "goal_contributions"
    __table_args__ = (
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        CheckConstraint(check_in_sql("origin", get_args(GoalContributionOrigin)), name="origin"),
        UniqueConstraint("user_id", "natural_key", name="uq_goal_contributions_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    goal_id: Mapped[str]
    date: Mapped[datetime]
    amount: Mapped[float] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(default="USD")
    note: Mapped[str] = mapped_column(default="")
    source_posting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.postings.id"), default=None
    )
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
        UniqueConstraint("user_id", "natural_key", name="uq_recurring_additions_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
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
    __table_args__ = (
        UniqueConstraint("user_id", "goal_id", name="uq_withdrawal_priority_entries_user_goal"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    goal_id: Mapped[str]
    priority: Mapped[int] = mapped_column(default=0)
