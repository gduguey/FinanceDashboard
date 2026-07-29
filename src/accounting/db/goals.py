"""Savings goals, their contributions, and the automations that fund/draw them down."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
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
from db.base import MONEY, Base, Timestamped, check_in_sql


class Goal(Base, Timestamped):
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
    target_amount: Mapped[Decimal] = mapped_column(MONEY)
    target_currency: Mapped[str] = mapped_column(default="USD")
    target_date: Mapped[datetime]
    color: Mapped[str]
    version: Mapped[int] = mapped_column(default=1)
    """Bumped by `db.base.check_and_bump_row_version` on every `PATCH /goals/{goal_id}` — see that
    function's own docstring. Never touched by this table's own upsert path (see
    `accounting.repositories.planning._upsert_goal`, whose `ON CONFLICT ... DO UPDATE` deliberately
    omits this column), so an unrelated create or reorder never invalidates a version a client
    already has in hand."""


class GoalContribution(Base, Timestamped):
    """One dated, signed allocation into (or withdrawal from) a goal — the only thing a goal's balance derives from.

    `goal_id` is a real foreign key into `goals` — a contribution can only
    ever be recorded against a goal that already exists; create the goal
    first (see `Goal`), then record contributions against it. `PUT
    /goal-contributions` still replaces the whole list wholesale (see
    `api.put_goal_contributions`), but every entry in that list must now
    name a goal that's actually there.
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
    goal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.goals.id"))
    date: Mapped[datetime]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(default="USD")
    note: Mapped[str] = mapped_column(default="")
    # SET NULL, not CASCADE: `amount`/`date` are the real financial record
    # (see `models.GoalContribution`'s own docstring — "never read by any
    # balance or unallocated computation"), so a posting pruned by a
    # ledger rebuild (`importers.ingest._write_ledger`) must never take the
    # contribution down with it — only its traceability link.
    source_posting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.postings.id", ondelete="SET NULL"), default=None
    )
    origin: Mapped[str] = mapped_column(default="manual")
    edited: Mapped[bool] = mapped_column(default=False)


class RecurringAddition(Base, Timestamped):
    """One ordered rule for automatically allocating unallocated money into a goal on a recurring schedule.

    `goal_id` is a real foreign key into `goals` — see `GoalContribution`'s
    own docstring; `PUT /recurring-additions` is the same "replace the
    whole list, every entry must name a goal that already exists" shape.
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
    goal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.goals.id"))
    start_date: Mapped[date]
    frequency: Mapped[str]
    end_date: Mapped[date | None] = mapped_column(default=None)
    mode: Mapped[str]
    value: Mapped[Decimal] = mapped_column(MONEY, default=0)
    currency: Mapped[str] = mapped_column(default="USD")
    priority: Mapped[int] = mapped_column(default=0)


class WithdrawalPriorityEntry(Base, Timestamped):
    """One goal's place in the order goals are drawn down from when unallocated money goes negative.

    `goal_id` is a real foreign key into `goals` — see `GoalContribution`'s
    own docstring; `PUT /withdrawal-priorities` is the same "replace the
    whole list, every entry must name a goal that already exists" shape.

    Purely an ordering — the withdrawal automation itself
    (`ledger.goal_automations.run_withdrawal_automation`) is event-driven
    (triggered whenever unallocated dips below zero), not scheduled, so
    there's no schedule field here the way `RecurringAddition` has one.
    """

    __tablename__ = "withdrawal_priority_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "goal_id", name="uq_withdrawal_priority_entries_user_goal"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    goal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.goals.id"))
    priority: Mapped[int] = mapped_column(default=0)
