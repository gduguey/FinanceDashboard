"""Savings goals, their contributions, and the automations that fund/draw them down."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
from accounting.models import (
    GoalAutomationDirection,
    GoalAutomationFrequency,
    GoalAutomationMode,
    GoalContributionOrigin,
)
from db.base import MONEY, UUID7_DEFAULT, Base, Timestamped, check_in_sql
from db.models import CURRENCY_CODE_COLUMN


class Goal(Base, Timestamped):
    """A savings target — its balance is never stored here, only derived from its `GoalContribution`s."""

    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint("target_amount > 0", name="target_amount_is_positive"),
        UniqueConstraint("user_id", "natural_key", name="uq_goals_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID7_DEFAULT)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    name: Mapped[str]
    target_amount: Mapped[Decimal] = mapped_column(MONEY)
    """Strictly positive — a goal of zero (or less) is already met by definition and has nothing to progress towards."""
    target_currency: Mapped[str] = mapped_column(ForeignKey(CURRENCY_CODE_COLUMN), default="USD")
    """The currency the target is expressed in — the one `currency` column in this schema under another name.

    Its own `CHECK (target_currency IN (...))` was a ninth copy of the same
    restated list; it is the same `currencies` reference as every other."""
    target_date: Mapped[datetime]
    color: Mapped[str]
    version: Mapped[int] = mapped_column(default=1)
    """Bumped by `db.base.check_and_bump_row_version` on every `PATCH /goals/{goal_id}` — see that
    function's own docstring. Never touched by this table's own upsert path (see
    `accounting.repositories.planning._upsert_goal`, whose `ON CONFLICT ... DO UPDATE` deliberately
    omits this column), so an unrelated create never invalidates a version a client
    already has in hand."""


class GoalContribution(Base, Timestamped):
    """One dated, signed allocation into (or withdrawal from) a goal — the only thing a goal's balance derives from.

    `goal_id` is a real foreign key into `goals` — a contribution can only
    ever be recorded against a goal that already exists; create the goal
    first (see `Goal`), then record contributions against it. Nothing
    validates that at the API edge, so a `POST /goal-contributions`
    naming a goal that isn't there surfaces as an `IntegrityError`
    reaching the client as a 500, like every other foreign-key violation
    in this app.
    """

    __tablename__ = "goal_contributions"
    __table_args__ = (
        CheckConstraint(check_in_sql("origin", get_args(GoalContributionOrigin)), name="origin"),
        UniqueConstraint("user_id", "natural_key", name="uq_goal_contributions_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID7_DEFAULT)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    goal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.goals.id"))
    date: Mapped[datetime]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(ForeignKey(CURRENCY_CODE_COLUMN), default="USD")
    note: Mapped[str] = mapped_column(default="")
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.accounts.id"), default=None
    )
    """Which account the money allocated to the goal actually sits in — nullable, and *not yet* used in any math.

    Envelope-over-balance: a goal's balance is an earmark on real money
    that lives in a real account, and this is that account. Plumbing
    only for now — nothing in `dashboard.goals` reads this column, so
    setting it changes no goal balance, no net-worth figure, and no
    unallocated-money total. A later change makes the unallocated
    arithmetic account-aware; until then this exists so the fact can be
    recorded, not acted on.
    """
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


_SCHEDULE_COLUMNS = ("start_date", "frequency", "mode", "value", "currency")
"""The columns only a `contribution` automation uses — see `GoalAutomation`'s CHECK below."""

_SCHEDULE_MATCHES_DIRECTION = (
    "(direction = 'contribution' AND "
    + " AND ".join(f"{column} IS NOT NULL" for column in _SCHEDULE_COLUMNS)
    + ") OR (direction = 'withdrawal' AND "
    + " AND ".join(f"{column} IS NULL" for column in (*_SCHEDULE_COLUMNS, "end_date"))
    + ")"
)
"""SQL for the cross-column CHECK that keeps a merged automation row honest.

Merging two tables into one behind a `direction` discriminator is only
safe if the discriminator actually decides which columns are populated —
otherwise the nullability the merge introduces is a hole, and a
`withdrawal` row carrying half a schedule (or a `contribution` row
missing one) becomes representable. `end_date` is the one column that is
optional even for a `contribution` (an open-ended schedule), so it is
only constrained on the `withdrawal` side.
"""


class GoalAutomation(Base, Timestamped):
    """One ordered rule for automatically moving money into — or out of — a goal.

    Merges what used to be `recurring_additions` (money in, on a
    schedule) and `withdrawal_priority_entries` (money out, when
    unallocated goes negative): the same "an automation moves money
    in/out of this goal, in this order" fact, told twice. `direction`
    tells them apart, and `schedule_matches_direction` enforces that a
    `contribution` row carries the whole schedule while a `withdrawal`
    row carries none of it.

    `goal_id` is a real foreign key into `goals` — see `GoalContribution`'s
    own docstring; both `PUT /goal-automations/contributions` and
    `PUT /goal-automations/withdrawals` are the same "replace the whole
    list, every entry must name a goal that already exists" shape, each
    scoped to its own `direction`.

    The partial unique index is what `withdrawal_priority_entries`'
    `UNIQUE (user_id, goal_id)` becomes: a goal appears at most once in
    the drawdown order, while it may legitimately have several
    contribution schedules funding it.

    A second partial unique index carries the "one remainder" rule.
    `mode = "remainder"` means "fund this goal with whatever is left after
    every other automation has run" (see `ledger.goal_automations`), which
    is only meaningful once: two of them would each claim the same leftover,
    and whichever ran second would find nothing. `api.routers.goals`
    rejected the second one with a 400, and nothing stopped a write that
    did not go through that endpoint. `UNIQUE (user_id) WHERE mode =
    'remainder'` is that rule, now held by the engine. The endpoint's other
    rule — that a `remainder` row is the *lowest-priority* one — stays in
    Python: it is a statement about the ordering of the whole list, which no
    index can express.
    """

    __tablename__ = "goal_automations"
    __table_args__ = (
        CheckConstraint(check_in_sql("direction", get_args(GoalAutomationDirection)), name="direction"),
        CheckConstraint(check_in_sql("frequency", get_args(GoalAutomationFrequency)), name="frequency"),
        CheckConstraint(check_in_sql("mode", get_args(GoalAutomationMode)), name="mode"),
        CheckConstraint(_SCHEDULE_MATCHES_DIRECTION, name="schedule_matches_direction"),
        UniqueConstraint("user_id", "natural_key", name="uq_goal_automations_user_natural_key"),
        Index(
            "uq_goal_automations_user_withdrawal_goal",
            "user_id",
            "goal_id",
            unique=True,
            postgresql_where=text("direction = 'withdrawal'"),
        ),
        Index(
            "uq_goal_automations_user_remainder",
            "user_id",
            unique=True,
            postgresql_where=text("mode = 'remainder'"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID7_DEFAULT)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    goal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.goals.id"))
    direction: Mapped[str]
    priority: Mapped[int] = mapped_column(default=0)
    start_date: Mapped[date | None] = mapped_column(default=None)
    frequency: Mapped[str | None] = mapped_column(default=None)
    end_date: Mapped[date | None] = mapped_column(default=None)
    mode: Mapped[str | None] = mapped_column(default=None)
    value: Mapped[Decimal | None] = mapped_column(MONEY, default=None)
    currency: Mapped[str | None] = mapped_column(ForeignKey(CURRENCY_CODE_COLUMN), default=None)
