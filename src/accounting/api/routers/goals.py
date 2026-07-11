"""Goal endpoints — spans `accounting.dashboard.goals` (reads) and `accounting.ledger.goal_automations` (actions)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    GoalsSummary,
    SimulateContributionRequest,
    SimulateContributionResult,
    WithdrawalAutomationResult,
)
from accounting.api.dependencies import _display_currency, _resolved_postings_and_store, state
from accounting.dashboard.goals import all_goal_balances, contributions_to_frame, unallocated_balance
from accounting.ledger.goal_automations import (
    next_recurring_occurrence,
    run_recurring_additions,
    run_withdrawal_automation,
)
from accounting.models import CurrencyCode, Goal, GoalContribution, RecurringAddition, WithdrawalPriorityEntry
from accounting.store import load_store, save_store
from db.session import get_db

router = APIRouter()


@router.put("/goals")
def put_goals(goals: dict[str, Goal], session: Annotated[Session, Depends(get_db)]) -> dict[str, Goal]:
    """Replace the whole goal list.

    Returns
    -------
    dict[str, Goal]
        The goals just persisted, keyed by `goal_id`.
    """
    store = load_store(session)
    store = store.model_copy(update={"goals": goals})
    save_store(store, session)
    return store.goals


@router.put("/goal-contributions")
def put_goal_contributions(
    contributions: dict[str, GoalContribution], session: Annotated[Session, Depends(get_db)]
) -> dict[str, GoalContribution]:
    """Replace the whole contribution ledger — every dated allocation into or withdrawal from every goal.

    Returns
    -------
    dict[str, GoalContribution]
        The contributions just persisted, keyed by `contribution_id`.
    """
    store = load_store(session)
    store = store.model_copy(update={"goal_contributions": contributions})
    save_store(store, session)
    return store.goal_contributions


@router.put("/recurring-additions")
def put_recurring_additions(
    additions: list[RecurringAddition], session: Annotated[Session, Depends(get_db)]
) -> list[RecurringAddition]:
    """Replace the whole recurring-addition list — the priority-ordered monthly allocation rules.

    Returns
    -------
    list[RecurringAddition]
        The additions just persisted.

    Raises
    ------
    HTTPException
        400 if more than one addition uses `mode="remainder"`, or one does but isn't the lowest-priority row.
    """
    remainder_additions = [addition for addition in additions if addition.mode == "remainder"]
    if len(remainder_additions) > 1:
        raise HTTPException(status_code=400, detail="Only one recurring addition may use mode='remainder'")
    if remainder_additions and remainder_additions[0].priority != max((a.priority for a in additions), default=0):
        raise HTTPException(status_code=400, detail="A 'remainder' addition must be the lowest-priority row")
    store = load_store(session)
    store = store.model_copy(update={"recurring_additions": additions})
    save_store(store, session)
    return store.recurring_additions


@router.put("/withdrawal-priorities")
def put_withdrawal_priorities(
    priorities: list[WithdrawalPriorityEntry], session: Annotated[Session, Depends(get_db)]
) -> list[WithdrawalPriorityEntry]:
    """Replace the whole withdrawal-priority list — the order goals are drawn down from when unallocated goes negative.

    Returns
    -------
    list[WithdrawalPriorityEntry]
        The priorities just persisted.
    """
    store = load_store(session)
    store = store.model_copy(update={"withdrawal_priorities": priorities})
    save_store(store, session)
    return store.withdrawal_priorities


@router.get("/goals/summary")
def get_goals_summary(
    *,
    as_of: date | None = None,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
) -> GoalsSummary:
    """Every goal's balance, plus unallocated money, as of `as_of` (today if omitted).

    Both are always recomputed fresh from postings and contributions —
    see `dashboard.goals` — never a stored figure.

    Returns
    -------
    GoalsSummary
    """
    postings, store = _resolved_postings_and_store(state.config, session)
    as_of_date = as_of or datetime.now(UTC).date()
    display = _display_currency(display_currency, store, as_of_date)
    contributions = contributions_to_frame(store.goal_contributions)
    balances = all_goal_balances(contributions, list(store.goals.keys()), as_of_date, display)
    unallocated = unallocated_balance(postings, store.accounts, contributions, as_of_date, display)
    return GoalsSummary(balances=balances, unallocated=unallocated)


def _next_contribution_id(existing_ids: set[str], prefix: str) -> str:
    """Build a contribution id that doesn't collide with anything already persisted.

    Returns
    -------
    str
    """
    candidate = prefix
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{prefix}:{suffix}"
        suffix += 1
    return candidate


@router.post("/goals/run-recurring-additions")
def post_run_recurring_additions(
    *, as_of: date | None = None, session: Annotated[Session, Depends(get_db)]
) -> list[GoalContribution]:
    """Run every recurring addition whose most recent scheduled occurrence hasn't already run.

    Idempotent by construction: each addition's occurrence writes a
    contribution under a deterministic id
    (`f"auto:{addition_id}:{occurrence.isoformat()}"`, see
    `ledger.goal_automations.next_recurring_occurrence`); calling this
    again before the next occurrence is a no-op for any addition that id
    already exists for. There is no background scheduler in this app —
    this is meant to be called when the Goals page loads, which is the
    natural moment a user would notice a change anyway.

    Returns
    -------
    list[GoalContribution]
        The new contributions just written (empty if nothing was due).
    """
    postings, store = _resolved_postings_and_store(state.config, session)
    as_of_date = as_of or datetime.now(UTC).date()
    existing_ids = set(store.goal_contributions.keys())

    occurrences: dict[str, date] = {}
    for addition in store.recurring_additions:
        occurrence = next_recurring_occurrence(addition, as_of_date)
        if occurrence is None:
            continue
        if f"auto:{addition.addition_id}:{occurrence.isoformat()}" in existing_ids:
            continue
        occurrences[addition.addition_id] = occurrence

    due = [addition for addition in store.recurring_additions if addition.addition_id in occurrences]
    if not due:
        return []

    contributions_frame = contributions_to_frame(store.goal_contributions)
    unallocated = unallocated_balance(postings, store.accounts, contributions_frame, as_of_date)
    funded = run_recurring_additions(due, unallocated)

    by_goal_addition = {addition.goal_id: addition for addition in due}
    new_contributions: dict[str, GoalContribution] = {}
    for goal_id, amount in funded:
        addition = by_goal_addition[goal_id]
        occurrence = occurrences[addition.addition_id]
        contribution_id = f"auto:{addition.addition_id}:{occurrence.isoformat()}"
        new_contributions[contribution_id] = GoalContribution(
            contribution_id=contribution_id,
            goal_id=goal_id,
            date=datetime.combine(occurrence, datetime.min.time()),
            amount=amount,
            currency=addition.currency,
            note="Recurring addition",
            origin="automation",
        )

    store = store.model_copy(update={"goal_contributions": {**store.goal_contributions, **new_contributions}})
    save_store(store, session)
    return list(new_contributions.values())


@router.post("/goals/run-withdrawal-automation")
def post_run_withdrawal_automation(
    *, as_of: date | None = None, session: Annotated[Session, Depends(get_db)]
) -> WithdrawalAutomationResult:
    """If unallocated money is negative as of today, draw down goals (by withdrawal priority) to cover it.

    Idempotent in effect (not by a stored id, unlike the recurring-addition
    case): a withdrawal always brings unallocated back to exactly zero or
    exhausts every goal, so calling this again immediately afterward finds
    nothing left to do — a *new* shortfall only ever appears from new
    postings/contributions arriving, at which point running this again is
    exactly what should happen.

    Returns
    -------
    WithdrawalAutomationResult
        The contributions just written, and however much of the shortfall
        (if any) no goal had enough left to cover.
    """
    postings, store = _resolved_postings_and_store(state.config, session)
    as_of_date = as_of or datetime.now(UTC).date()
    contributions_frame = contributions_to_frame(store.goal_contributions)
    unallocated = unallocated_balance(postings, store.accounts, contributions_frame, as_of_date)
    if unallocated >= 0:
        return WithdrawalAutomationResult(withdrawals=[], remaining_shortfall=0.0)

    shortfall = -unallocated
    balances = all_goal_balances(contributions_frame, list(store.goals.keys()), as_of_date)
    drawn = run_withdrawal_automation(store.withdrawal_priorities, balances, shortfall)

    existing_ids = set(store.goal_contributions.keys())
    new_contributions: dict[str, GoalContribution] = {}
    for goal_id, amount in drawn:
        contribution_id = _next_contribution_id(existing_ids, f"auto-withdrawal:{goal_id}:{as_of_date.isoformat()}")
        existing_ids.add(contribution_id)
        new_contributions[contribution_id] = GoalContribution(
            contribution_id=contribution_id,
            goal_id=goal_id,
            date=datetime(as_of_date.year, as_of_date.month, as_of_date.day),  # noqa: DTZ001  (ledger dates are naive)
            amount=amount,
            note="Withdrawal automation — unallocated went negative",
            origin="automation",
        )

    store = store.model_copy(update={"goal_contributions": {**store.goal_contributions, **new_contributions}})
    save_store(store, session)
    remaining_shortfall = max(0.0, shortfall - sum(-amount for _, amount in drawn))
    return WithdrawalAutomationResult(
        withdrawals=list(new_contributions.values()), remaining_shortfall=remaining_shortfall
    )


@router.post("/goals/simulate-contribution")
def post_simulate_contribution(
    payload: SimulateContributionRequest, session: Annotated[Session, Depends(get_db)]
) -> SimulateContributionResult:
    """Check a proposed manual contribution against unallocated money, and project the next automation run.

    Validates against the *running total as of `payload.date`* — since
    contributions are dated, not bucketed by month, a contribution backdated
    to a day with less unallocated money available than today can't be
    slipped in just because today's balance would cover it.

    Returns
    -------
    SimulateContributionResult
        The last two fields simulate every configured recurring addition
        running once more, with this contribution already applied, so the
        user can see if it sets up a shortfall soon after (non-blocking).
    """
    postings, store = _resolved_postings_and_store(state.config, session)
    contributions_frame = contributions_to_frame(store.goal_contributions)
    unallocated_as_of_date = unallocated_balance(postings, store.accounts, contributions_frame, payload.date)
    exceeds_unallocated = payload.amount > unallocated_as_of_date

    today = datetime.now(UTC).date()
    unallocated_today = unallocated_balance(postings, store.accounts, contributions_frame, today)
    projected_before_run = unallocated_today - payload.amount
    funded_next_run = run_recurring_additions(store.recurring_additions, max(projected_before_run, 0.0))
    projected_next_run_unallocated = projected_before_run - sum(amount for _, amount in funded_next_run)

    return SimulateContributionResult(
        unallocated_as_of_date=unallocated_as_of_date,
        exceeds_unallocated=exceeds_unallocated,
        projected_next_run_unallocated=projected_next_run_unallocated,
        would_go_negative=projected_next_run_unallocated < 0,
    )
