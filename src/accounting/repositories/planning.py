"""The planning aggregate: budgets, goals, goal contributions, and the automations that move money between them.

Every write here is scoped to the rows it names. A whole-list `replace_*`
still exists where the client genuinely submits an ordering (the goals
page's drag-and-drop), but it only ever touches its own table — never the
fifteen others the old whole-store save swept up with it.

`Goal` carries a `version` column that `PATCH /goals/{goal_id}` bumps
through `db.base.check_and_bump_row_version`, so every other write to a
goal row (see `_upsert_goal`) leaves that column alone: creating one goal
must not invalidate a version a client already has in hand for another.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

import accounting.db as adb
from accounting.models import Budget, Goal, GoalAutomation, GoalAutomationDirection, GoalContribution
from db.base import check_and_bump_row_version, ids_by_natural_key, natural_keys_by_id

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterable, Mapping

    from sqlalchemy.orm import Session

_GOALS_TABLE = "accounting.goals"


def _optional_id(ids: Mapping[str, uuid.UUID], natural_key: str | None) -> uuid.UUID | None:
    """Resolve a *nullable* reference: `None` when there is no key, the key's id when there is.

    Same contract as `interpretation._optional_id` — see
    `db.base.ids_by_natural_key` for why a natural key that names no row
    raises here rather than resolving to `None`.

    Returns
    -------
    uuid.UUID or None
    """
    return ids[natural_key] if natural_key is not None else None


def budget_row_key(month: str | None, category_id: str, subcategory_id: str | None) -> str:
    """Build the natural key one budget's `(month, category_id, subcategory_id)` triple always maps to.

    `month=None` (the general, every-month-alike target) leaves the month
    segment empty, which no `"YYYY-MM"` month can produce — so a general
    budget and a month budget for the same category never collide on this
    key, exactly as they don't collide on the table's own unique index.

    A budget's identity *is* this triple, so anything that changes one of
    the three (a category merge, see `store.remap_category_ids`) has to
    rebuild the key rather than carry the old one forward.

    Returns
    -------
    str
    """
    month_segment = month or ""
    if subcategory_id is not None:
        return f"{month_segment}:{category_id}:{subcategory_id}"
    return f"{month_segment}:{category_id}"


# --------------------------------------------------------------------------
# Budgets
# --------------------------------------------------------------------------


def load_budgets(session: Session, user_id: uuid.UUID) -> list[Budget]:
    """Read every budget cell — per-month and general (`month is None`) alike.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose budgets to read.

    Returns
    -------
    list[Budget]
        Both kinds in one list; a general budget is the one whose `month` is `None`.
    """
    rows = list(session.query(adb.Budget).filter_by(user_id=user_id))
    category_natural_key_by_id = natural_keys_by_id(
        session, adb.Category, user_id, [row.category_id for row in rows] + [row.subcategory_id for row in rows]
    )
    return [
        Budget(
            budget_id=row.natural_key,
            month=row.month,
            category_id=category_natural_key_by_id[row.category_id],
            subcategory_id=category_natural_key_by_id.get(row.subcategory_id)
            if row.subcategory_id is not None
            else None,
            amount=row.amount,
            currency=row.currency,  # type: ignore[arg-type]
        )
        for row in rows
    ]


def replace_budgets(session: Session, user_id: uuid.UUID, budgets: Iterable[Budget]) -> None:
    """Replace this user's whole budget grid — every month plus the general targets — touching no other table.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose budgets these are.
    budgets
        The complete desired set of budget cells, general ones (`month is None`) included.
    """
    budgets = list(budgets)
    session.query(adb.Budget).filter_by(user_id=user_id).delete()
    session.flush()
    category_ids = ids_by_natural_key(
        session,
        adb.Category,
        user_id,
        [budget.category_id for budget in budgets] + [budget.subcategory_id for budget in budgets],
    )
    session.add_all(
        adb.Budget(
            user_id=user_id,
            natural_key=budget.budget_id,
            month=budget.month,
            category_id=category_ids[budget.category_id],
            subcategory_id=_optional_id(category_ids, budget.subcategory_id),
            amount=budget.amount,
            currency=budget.currency,
        )
        for budget in budgets
    )
    session.flush()


def upsert_budget(budget: Budget, session: Session, user_id: uuid.UUID) -> bool:
    """Insert-or-update one budget cell — per-month or general — touching no other.

    Keyed by `budget.budget_id` (derived from month+category+subcategory,
    with an empty month standing for the general target), so re-setting
    the same cell is last-write-wins — the intended semantics for a single
    amount.

    Parameters
    ----------
    budget
        The budget to persist.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose budget this is.

    Returns
    -------
    bool
        `True` if this call brought the row into existence, `False` if it
        replaced one already there. `POST /budgets` needs the difference to
        answer `201` versus `200` honestly, and it has to come from the
        write itself: a separate `SELECT` first would be a second round
        trip whose answer a concurrent writer could invalidate before the
        `INSERT` below ran. `xmax = 0` is Postgres' own record of which
        branch of `ON CONFLICT` this row took — zero on a fresh insert, the
        updating transaction's id on a conflict — so it is read out of the
        same statement that decided it.
    """
    category_ids = ids_by_natural_key(session, adb.Category, user_id, [budget.category_id, budget.subcategory_id])
    subcategory_id = _optional_id(category_ids, budget.subcategory_id)
    created = session.execute(
        text(
            """
            INSERT INTO accounting.budgets
                (user_id, natural_key, month, category_id, subcategory_id, amount, currency)
            VALUES
                (:user_id, :natural_key, :month, :category_id, :subcategory_id, :amount, :currency)
            ON CONFLICT (user_id, natural_key) DO UPDATE SET
                month = EXCLUDED.month,
                category_id = EXCLUDED.category_id,
                subcategory_id = EXCLUDED.subcategory_id,
                amount = EXCLUDED.amount,
                currency = EXCLUDED.currency
            RETURNING xmax = 0 AS created
            """
        ),
        {
            "user_id": str(user_id),
            "natural_key": budget.budget_id,
            "month": budget.month,
            "category_id": str(category_ids[budget.category_id]),
            "subcategory_id": str(subcategory_id) if subcategory_id is not None else None,
            "amount": budget.amount,
            "currency": budget.currency,
        },
    ).scalar_one()
    session.commit()
    return bool(created)


def remove_budget(session: Session, user_id: uuid.UUID, budget_id: str) -> bool:
    """Delete one budget cell — per-month or general — touching no other. Idempotent, no version check.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    deleted = session.query(adb.Budget).filter_by(user_id=user_id, natural_key=budget_id).delete()
    session.flush()
    return deleted > 0


# --------------------------------------------------------------------------
# Goals
# --------------------------------------------------------------------------


def load_goals(session: Session, user_id: uuid.UUID) -> dict[str, Goal]:
    """Read every savings goal, keyed by its natural key.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose goals to read.

    Returns
    -------
    dict[str, Goal]
    """
    return {
        row.natural_key: Goal(
            goal_id=row.natural_key,
            name=row.name,
            target_amount=row.target_amount,
            target_currency=row.target_currency,  # type: ignore[arg-type]
            target_date=row.target_date,
            color=row.color,
            created_at=row.created_at,
            version=row.version,
        )
        for row in session.query(adb.Goal).filter_by(user_id=user_id)
    }


def _upsert_goal(session: Session, user_id: uuid.UUID, goal: Goal) -> None:
    """Insert-or-update one goal row without ever writing its `version` column.

    A raw `INSERT ... ON CONFLICT (user_id, natural_key) DO UPDATE` whose `SET` clause simply
    omits `version` is what keeps `PATCH /goals/{goal_id}`'s per-row
    optimistic concurrency intact: an existing row keeps whatever version
    `check_and_bump_row_version` last left it at, no matter how many times
    an unrelated create round-trips through here.
    """
    session.execute(
        text(
            """
            INSERT INTO accounting.goals
                (user_id, natural_key, name, target_amount, target_currency, target_date,
                 color, created_at, version)
            VALUES
                (:user_id, :natural_key, :name, :target_amount, :target_currency, :target_date,
                 :color, :created_at, 1)
            ON CONFLICT (user_id, natural_key) DO UPDATE SET
                name = EXCLUDED.name,
                target_amount = EXCLUDED.target_amount,
                target_currency = EXCLUDED.target_currency,
                target_date = EXCLUDED.target_date,
                color = EXCLUDED.color
            """
        ),
        {
            "user_id": str(user_id),
            "natural_key": goal.goal_id,
            "name": goal.name,
            "target_amount": goal.target_amount,
            "target_currency": goal.target_currency,
            "target_date": goal.target_date,
            "color": goal.color,
            "created_at": goal.created_at,
        },
    )


def insert_goal(session: Session, user_id: uuid.UUID, goal: Goal) -> None:
    """Persist one new goal, touching no other goal already saved.

    Parameters
    ----------
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose goal this is.
    goal
        The goal to persist.
    """
    _upsert_goal(session, user_id, goal)
    session.commit()


def update_goal(session: Session, user_id: uuid.UUID, goal: Goal, expected_version: int | None) -> Goal | None:
    """Update one goal's fields in place, touching no other persisted entity.

    `goal.goal_id` identifies which row to update; every other field on
    `goal` (`goal.version` is never read here — only `expected_version`
    is) becomes that row's new state, guarded by
    `db.base.check_and_bump_row_version` so a stale client can't silently
    clobber a concurrent edit to the same goal.

    Returns
    -------
    Goal | None
        The goal as persisted after the update, or `None` if no goal with
        `goal.goal_id` exists for this user. Raises `db.base.VersionConflictError`
        (propagated straight from `check_and_bump_row_version`) if the goal
        exists but `expected_version` no longer matches what's stored.

    Raises
    ------
    RuntimeError
        If the row vanishes between the version check just above and this
        function's own read of it — the version check already proved the
        row exists inside this same transaction, so this is only a
        defensive invariant, never expected to actually happen.
    """
    row_id = ids_by_natural_key(session, adb.Goal, user_id, [goal.goal_id]).get(goal.goal_id)
    if row_id is None:
        return None
    new_version = check_and_bump_row_version(session, _GOALS_TABLE, row_id, user_id, expected_version)
    if new_version is None:
        return None
    row = session.get(adb.Goal, row_id)
    if row is None:
        message = f"goals row {row_id} vanished between its version check and this read"
        raise RuntimeError(message)
    row.name = goal.name
    row.target_amount = goal.target_amount
    row.target_currency = goal.target_currency
    row.target_date = goal.target_date
    row.color = goal.color
    session.flush()
    return Goal(
        goal_id=goal.goal_id,
        name=row.name,
        target_amount=row.target_amount,
        target_currency=row.target_currency,
        target_date=row.target_date,
        color=row.color,
        created_at=row.created_at,
        version=new_version,
    )


def delete_goal(session: Session, user_id: uuid.UUID, goal_id: str) -> bool:
    """Delete one goal, without touching any other persisted entity.

    Idempotent by design: no version check, since a goal that's already
    gone has nothing left to conflict with. Fails loudly (an
    `IntegrityError`, uncaught) if the goal still has real
    `GoalContribution`/`GoalAutomation` rows referencing it.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    deleted = session.query(adb.Goal).filter_by(user_id=user_id, natural_key=goal_id).delete()
    session.flush()
    return deleted > 0


# --------------------------------------------------------------------------
# Goal contributions
# --------------------------------------------------------------------------


def load_goal_contributions(session: Session, user_id: uuid.UUID) -> dict[str, GoalContribution]:
    """Read every dated allocation into (or withdrawal from) every goal.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose contributions to read.

    Returns
    -------
    dict[str, GoalContribution]
        Keyed by `contribution_id`.
    """
    rows = list(session.query(adb.GoalContribution).filter_by(user_id=user_id))
    goal_natural_key_by_id = natural_keys_by_id(session, adb.Goal, user_id, [row.goal_id for row in rows])
    posting_natural_key_by_id = natural_keys_by_id(
        session, adb.Posting, user_id, [row.source_posting_id for row in rows]
    )
    account_natural_key_by_id = natural_keys_by_id(session, adb.Account, user_id, [row.account_id for row in rows])
    return {
        row.natural_key: GoalContribution(
            contribution_id=row.natural_key,
            goal_id=goal_natural_key_by_id[row.goal_id],
            date=row.date,
            amount=row.amount,
            currency=row.currency,  # type: ignore[arg-type]
            note=row.note,
            account_id=account_natural_key_by_id.get(row.account_id) if row.account_id is not None else None,
            source_posting_id=posting_natural_key_by_id.get(row.source_posting_id)
            if row.source_posting_id is not None
            else None,
            origin=row.origin,  # type: ignore[arg-type]
            edited=row.edited,
        )
        for row in rows
    }


def _contribution_references(
    session: Session, user_id: uuid.UUID, contributions: list[GoalContribution]
) -> tuple[Mapping[str, uuid.UUID], Mapping[str, uuid.UUID], Mapping[str, uuid.UUID]]:
    """Resolve every goal, account, and posting a batch of contributions references, in three queries.

    Returns
    -------
    tuple[Mapping[str, uuid.UUID], Mapping[str, uuid.UUID], Mapping[str, uuid.UUID]]
        Goal ids, account ids, and posting ids, each keyed by natural key.
    """
    return (
        ids_by_natural_key(session, adb.Goal, user_id, [row.goal_id for row in contributions]),
        ids_by_natural_key(session, adb.Account, user_id, [row.account_id for row in contributions]),
        ids_by_natural_key(session, adb.Posting, user_id, [row.source_posting_id for row in contributions]),
    )


def _goal_contribution_row(
    user_id: uuid.UUID,
    contribution: GoalContribution,
    goal_ids: Mapping[str, uuid.UUID],
    account_ids: Mapping[str, uuid.UUID],
    posting_ids: Mapping[str, uuid.UUID],
) -> adb.GoalContribution:
    """Build the ORM row for one contribution, resolving its three references through the given maps.

    Parameters
    ----------
    user_id
        Whose contribution this is.
    contribution
        The pydantic contribution to convert.
    goal_ids
        Goal natural key to row id, covering `contribution.goal_id`.
    account_ids
        Account natural key to row id, covering `contribution.account_id`.
    posting_ids
        Posting natural key to row id, covering `contribution.source_posting_id`.

    Returns
    -------
    accounting.db.GoalContribution
    """
    return adb.GoalContribution(
        user_id=user_id,
        natural_key=contribution.contribution_id,
        goal_id=goal_ids[contribution.goal_id],
        date=contribution.date,
        amount=contribution.amount,
        currency=contribution.currency,
        note=contribution.note,
        account_id=_optional_id(account_ids, contribution.account_id),
        source_posting_id=_optional_id(posting_ids, contribution.source_posting_id),
        origin=contribution.origin,
        edited=contribution.edited,
    )


def insert_goal_contributions(session: Session, user_id: uuid.UUID, contributions: Iterable[GoalContribution]) -> None:
    """Add contributions additively, touching none already recorded.

    Used by the two goal automations, which each mint their own
    deterministic ids and must never disturb a contribution they didn't
    create.

    Parameters
    ----------
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose contributions these are.
    contributions
        The contributions to add.
    """
    contributions = list(contributions)
    references = _contribution_references(session, user_id, contributions)
    session.add_all(_goal_contribution_row(user_id, contribution, *references) for contribution in contributions)
    session.commit()


def upsert_goal_contribution(contribution: GoalContribution, session: Session, user_id: uuid.UUID) -> None:
    """Insert-or-update one goal contribution, touching no other contribution.

    Keyed by `contribution.contribution_id`, so re-saving the same
    contribution is last-write-wins — the intended semantics for an edit of
    a single dated allocation.

    Parameters
    ----------
    contribution
        The contribution to persist.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose contribution this is.
    """
    goal_ids, account_ids, posting_ids = _contribution_references(session, user_id, [contribution])
    session.execute(
        text(
            """
            INSERT INTO accounting.goal_contributions
                (user_id, natural_key, goal_id, date, amount, currency, note, account_id, source_posting_id,
                 origin, edited)
            VALUES
                (:user_id, :natural_key, :goal_id, :date, :amount, :currency, :note, :account_id,
                 :source_posting_id, :origin, :edited)
            ON CONFLICT (user_id, natural_key) DO UPDATE SET
                goal_id = EXCLUDED.goal_id,
                date = EXCLUDED.date,
                amount = EXCLUDED.amount,
                currency = EXCLUDED.currency,
                note = EXCLUDED.note,
                account_id = EXCLUDED.account_id,
                source_posting_id = EXCLUDED.source_posting_id,
                origin = EXCLUDED.origin,
                edited = EXCLUDED.edited
            """
        ),
        {
            "user_id": str(user_id),
            "natural_key": contribution.contribution_id,
            "goal_id": str(goal_ids[contribution.goal_id]),
            "date": contribution.date,
            "amount": contribution.amount,
            "currency": contribution.currency,
            "note": contribution.note,
            "account_id": str(account_id)
            if (account_id := _optional_id(account_ids, contribution.account_id))
            else None,
            "source_posting_id": str(posting_id)
            if (posting_id := _optional_id(posting_ids, contribution.source_posting_id)) is not None
            else None,
            "origin": contribution.origin,
            "edited": contribution.edited,
        },
    )
    session.commit()


def goal_contribution_exists(session: Session, user_id: uuid.UUID, contribution_id: str) -> bool:
    """Whether one contribution row exists.

    Returns
    -------
    bool
    """
    return (
        session.query(adb.GoalContribution.id).filter_by(user_id=user_id, natural_key=contribution_id).first()
        is not None
    )


def remove_goal_contribution(session: Session, user_id: uuid.UUID, contribution_id: str) -> bool:
    """Delete one goal contribution, touching no other. Idempotent, no version check.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    deleted = session.query(adb.GoalContribution).filter_by(user_id=user_id, natural_key=contribution_id).delete()
    session.flush()
    return deleted > 0


# --------------------------------------------------------------------------
# Goal automations (contributions in, withdrawals out)
# --------------------------------------------------------------------------


def withdrawal_automation_id(goal_id: str) -> str:
    """Build the natural key the one withdrawal automation for `goal_id` always maps to.

    A withdrawal automation has no identity beyond its goal — a goal
    appears at most once in the drawdown order (the
    `uq_goal_automations_user_withdrawal_goal` partial index) — so its id
    is derived rather than minted, the way a general budget's used to be.
    The `withdrawal:` prefix is what keeps it from ever colliding with a
    contribution automation's server-minted `addition:...` id in the
    table's shared `(user_id, natural_key)` unique constraint.

    Returns
    -------
    str
    """
    return f"withdrawal:{goal_id}"


def load_goal_automations(session: Session, user_id: uuid.UUID) -> list[GoalAutomation]:
    """Read every goal automation — both scheduled contributions in and drawdown-ordered withdrawals out.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose automations to read.

    Returns
    -------
    list[GoalAutomation]
        Both directions in one list; `direction` tells them apart.
    """
    rows = list(session.query(adb.GoalAutomation).filter_by(user_id=user_id))
    goal_natural_key_by_id = natural_keys_by_id(session, adb.Goal, user_id, [row.goal_id for row in rows])
    return [
        GoalAutomation(
            automation_id=row.natural_key,
            goal_id=goal_natural_key_by_id[row.goal_id],
            direction=row.direction,  # type: ignore[arg-type]
            priority=row.priority,
            start_date=row.start_date,
            frequency=row.frequency,  # type: ignore[arg-type]
            end_date=row.end_date,
            mode=row.mode,  # type: ignore[arg-type]
            value=row.value,
            currency=row.currency,  # type: ignore[arg-type]
        )
        for row in rows
    ]


def _goal_automation_row(
    user_id: uuid.UUID, automation: GoalAutomation, goal_ids: Mapping[str, uuid.UUID]
) -> adb.GoalAutomation:
    """Build the ORM row for one automation, resolving its goal reference through `goal_ids`.

    Parameters
    ----------
    user_id
        Whose automation this is.
    automation
        The pydantic automation to convert.
    goal_ids
        Goal natural key to row id, covering `automation.goal_id`.

    Returns
    -------
    accounting.db.GoalAutomation
    """
    return adb.GoalAutomation(
        user_id=user_id,
        natural_key=automation.automation_id,
        goal_id=goal_ids[automation.goal_id],
        direction=automation.direction,
        priority=automation.priority,
        start_date=automation.start_date,
        frequency=automation.frequency,
        end_date=automation.end_date,
        mode=automation.mode,
        value=automation.value,
        currency=automation.currency,
    )


def replace_goal_automations(
    session: Session, user_id: uuid.UUID, automations: Iterable[GoalAutomation], direction: GoalAutomationDirection
) -> None:
    """Replace this user's whole automation list *for one direction*, touching no other table.

    Scoped to `direction` because the two directions are edited by two
    independent bits of UI (the recurring-additions list and the
    withdrawal ordering): replacing one must never wipe the other, even
    though they now share a table.

    Its one caller is `api.routers.goals._reorder_automations`, which has
    already checked that `automations` is exactly the persisted set for
    `direction` — so the delete-and-reinsert below only ever changes
    `priority`. Nothing else in the API replaces a list wholesale.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose automations these are.
    automations
        The complete desired set for `direction`, in priority order.
    direction
        Which direction is being replaced; every entry in `automations` must match it.

    Raises
    ------
    ValueError
        If any entry's own `direction` differs from `direction` — that
        would delete rows of one direction and insert rows of another.
    """
    rows = list(automations)
    mismatched = [row.automation_id for row in rows if row.direction != direction]
    if mismatched:
        message = f"Cannot replace the {direction!r} automations with rows of another direction: {mismatched}"
        raise ValueError(message)
    session.query(adb.GoalAutomation).filter_by(user_id=user_id, direction=direction).delete()
    session.flush()
    goal_ids = ids_by_natural_key(session, adb.Goal, user_id, [row.goal_id for row in rows])
    session.add_all(_goal_automation_row(user_id, automation, goal_ids) for automation in rows)
    session.flush()


def upsert_goal_automation(automation: GoalAutomation, session: Session, user_id: uuid.UUID) -> None:
    """Insert-or-update one goal automation, touching no other.

    A single-field edit of one rule (its amount, dates, frequency, mode)
    no longer blanket-reinserts every rule for the user, so it can't
    revert a concurrent edit to a different one.

    Parameters
    ----------
    automation
        The automation to persist.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose automation this is.
    """
    goal_ids = ids_by_natural_key(session, adb.Goal, user_id, [automation.goal_id])
    session.execute(
        text(
            """
            INSERT INTO accounting.goal_automations
                (user_id, natural_key, goal_id, direction, priority, start_date, frequency, end_date,
                 mode, value, currency)
            VALUES
                (:user_id, :natural_key, :goal_id, :direction, :priority, :start_date, :frequency, :end_date,
                 :mode, :value, :currency)
            ON CONFLICT (user_id, natural_key) DO UPDATE SET
                goal_id = EXCLUDED.goal_id,
                direction = EXCLUDED.direction,
                priority = EXCLUDED.priority,
                start_date = EXCLUDED.start_date,
                frequency = EXCLUDED.frequency,
                end_date = EXCLUDED.end_date,
                mode = EXCLUDED.mode,
                value = EXCLUDED.value,
                currency = EXCLUDED.currency
            """
        ),
        {
            "user_id": str(user_id),
            "natural_key": automation.automation_id,
            "goal_id": str(goal_ids[automation.goal_id]),
            "direction": automation.direction,
            "priority": automation.priority,
            "start_date": automation.start_date,
            "frequency": automation.frequency,
            "end_date": automation.end_date,
            "mode": automation.mode,
            "value": automation.value,
            "currency": automation.currency,
        },
    )
    session.commit()


def goal_automation_exists(
    session: Session, user_id: uuid.UUID, automation_id: str, *, direction: GoalAutomationDirection | None = None
) -> bool:
    """Whether one goal-automation row exists, optionally only in one direction.

    `direction` matters because the two directions are separate resources
    over one table. An existence check that ignores it lets a caller
    holding a withdrawal's id reach a contribution-shaped write path and
    silently flip the row's direction — see `api.routers.goals.patch_goal_automation`.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose automation to look for.
    automation_id
        The automation's natural key.
    direction
        Restrict the check to automations funding (`"contribution"`) or
        draining (`"withdrawal"`) a goal. `None` matches either, which is
        what a direction-agnostic caller such as a delete wants.

    Returns
    -------
    bool
    """
    query = session.query(adb.GoalAutomation.id).filter_by(user_id=user_id, natural_key=automation_id)
    if direction is not None:
        query = query.filter_by(direction=direction)
    return query.first() is not None


def remove_goal_automation(session: Session, user_id: uuid.UUID, automation_id: str) -> bool:
    """Delete one goal automation, touching no other. Idempotent, no version check.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    deleted = session.query(adb.GoalAutomation).filter_by(user_id=user_id, natural_key=automation_id).delete()
    session.flush()
    return deleted > 0
