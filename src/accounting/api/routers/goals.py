"""Goal endpoints — spans `accounting.dashboard.goals` (reads) and `accounting.ledger.goal_automations` (actions)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    GoalContributionCreate,
    GoalContributionIdResponse,
    GoalContributionUpdate,
    GoalCreate,
    GoalIdResponse,
    GoalsSummary,
    GoalUpdate,
    RecurringAdditionCreate,
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
from accounting.store import delete_goal, load_store, next_available_color, save_store, update_goal
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()


@router.post("/goals")
def post_goal(
    request: GoalCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Goal:
    """Create one new goal, without touching any other goal already saved.

    `goal_id` is server-minted — two goals can validly share a name, so
    there's no natural key two "the same" goal would collide on. `color`
    is picked to be distinct from every color already assigned to an
    existing goal, the same `store.next_available_color` helper
    categories already use for the same purpose.

    Returns
    -------
    Goal
        The goal just persisted.
    """
    store = load_store(session, user_id)
    goal = Goal(
        goal_id=f"goal:{uuid.uuid4().hex}",
        name=request.name,
        target_amount=request.target_amount,
        target_currency=request.target_currency,
        target_date=request.target_date,
        color=next_available_color(goal.color for goal in store.goals.values()),
        created_at=datetime.now(tz=UTC),
    )
    store = store.model_copy(update={"goals": {**store.goals, goal.goal_id: goal}})
    save_store(store, session, user_id)
    return goal


@router.put("/goals")
def put_goals(
    goals: dict[str, Goal],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> dict[str, Goal]:
    """Replace the whole goal list.

    Returns
    -------
    dict[str, Goal]
        The goals just persisted, keyed by `goal_id`.
    """
    store = load_store(session, user_id)
    store = store.model_copy(update={"goals": goals})
    save_store(store, session, user_id)
    return store.goals


@router.patch("/goals/{goal_id}")
def patch_goal(
    goal_id: str,
    request: GoalUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Goal:
    """Update one existing goal in place, without touching any other goal already saved.

    A true per-resource write — unlike `PUT /goals`, this never
    round-trips through `load_store`/`save_store` (which deletes and
    reinserts every persisted entity for the user); see
    `accounting.store.update_goal`. Guarded by `request.expected_version`
    instead of the whole-store `X-Expected-Store-Version` header, so an
    edit to this one goal can never spuriously conflict with — or be
    silently overwritten by — an unrelated save elsewhere in the store.

    Returns
    -------
    Goal
        The goal as persisted after the update.

    Raises
    ------
    HTTPException
        404 if no goal with `goal_id` exists.
    """
    # `created_at` is a required field on `Goal` but `update_goal` never
    # touches it — it always returns the row's real, untouched value.
    goal = Goal(
        goal_id=goal_id,
        name=request.name,
        target_amount=request.target_amount,
        target_currency=request.target_currency,
        target_date=request.target_date,
        color=request.color,
        created_at=datetime.now(tz=UTC),
    )
    updated = update_goal(session, user_id, goal, request.expected_version)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id!r} not found")
    session.commit()
    return updated


@router.delete("/goals/{goal_id}")
def delete_goal_route(
    goal_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalIdResponse:
    """Delete one goal, without touching any other goal already saved.

    No version check — see `accounting.store.delete_goal`'s own
    docstring for why deleting an already-gone goal is a plain 404, not a
    409: there's nothing left to conflict with.

    Returns
    -------
    GoalIdResponse
        The goal id just deleted.

    Raises
    ------
    HTTPException
        404 if no goal with `goal_id` exists.
    """
    deleted = delete_goal(session, user_id, goal_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id!r} not found")
    session.commit()
    return GoalIdResponse(goal_id=goal_id)


@router.put("/goal-contributions")
def put_goal_contributions(
    contributions: dict[str, GoalContribution],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> dict[str, GoalContribution]:
    """Replace the whole contribution ledger — every dated allocation into or withdrawal from every goal.

    Returns
    -------
    dict[str, GoalContribution]
        The contributions just persisted, keyed by `contribution_id`.
    """
    store = load_store(session, user_id)
    store = store.model_copy(update={"goal_contributions": contributions})
    save_store(store, session, user_id)
    return store.goal_contributions


@router.post("/goal-contributions")
def post_goal_contribution(
    request: GoalContributionCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalContribution:
    """Record one new dated allocation, without touching any other contribution already recorded.

    Unlike a budget's `(month, category_id)`, a contribution is an
    arbitrary event with no natural key to derive an id from, so the
    server generates an opaque one — two contributions with identical
    fields (e.g. the same goal, date, and amount entered twice) are
    distinct rows, not a collision.

    Returns
    -------
    GoalContribution
        The contribution just persisted.
    """
    contribution = GoalContribution(
        contribution_id=f"manual:{uuid.uuid4().hex}",
        goal_id=request.goal_id,
        date=request.date,
        amount=request.amount,
        currency=request.currency,
        note=request.note,
        source_posting_id=request.source_posting_id,
        origin=request.origin,
        edited=request.edited,
    )
    store = load_store(session, user_id)
    store = store.model_copy(
        update={"goal_contributions": {**store.goal_contributions, contribution.contribution_id: contribution}}
    )
    save_store(store, session, user_id)
    return contribution


@router.put("/goal-contributions/{contribution_id}")
def put_goal_contribution(
    contribution_id: str,
    request: GoalContributionUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalContribution:
    """Replace one contribution's fields, without touching any other contribution.

    Every field is required — the caller (`ContributionLedgerTable.tsx`'s
    `update()`) already merges its patch into the existing row
    client-side before sending, so there's no partial-update ambiguity to
    resolve here.

    Returns
    -------
    GoalContribution
        The contribution just persisted.

    Raises
    ------
    HTTPException
        404 if no contribution with this id exists.
    """
    store = load_store(session, user_id)
    if contribution_id not in store.goal_contributions:
        raise HTTPException(status_code=404, detail=f"Goal contribution {contribution_id!r} not found")
    contribution = GoalContribution(
        contribution_id=contribution_id,
        goal_id=request.goal_id,
        date=request.date,
        amount=request.amount,
        currency=request.currency,
        note=request.note,
        source_posting_id=request.source_posting_id,
        origin=request.origin,
        edited=request.edited,
    )
    store = store.model_copy(update={"goal_contributions": {**store.goal_contributions, contribution_id: contribution}})
    save_store(store, session, user_id)
    return contribution


@router.delete("/goal-contributions/{contribution_id}")
def delete_goal_contribution(
    contribution_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalContributionIdResponse:
    """Remove one contribution, without touching any other.

    Returns
    -------
    GoalContributionIdResponse

    Raises
    ------
    HTTPException
        404 if no contribution with this id exists.
    """
    store = load_store(session, user_id)
    if contribution_id not in store.goal_contributions:
        raise HTTPException(status_code=404, detail=f"Goal contribution {contribution_id!r} not found")
    remaining = {cid: c for cid, c in store.goal_contributions.items() if cid != contribution_id}
    store = store.model_copy(update={"goal_contributions": remaining})
    save_store(store, session, user_id)
    return GoalContributionIdResponse(contribution_id=contribution_id)


@router.post("/recurring-additions")
def post_recurring_addition(
    request: RecurringAdditionCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> RecurringAddition:
    """Create one new recurring-addition rule, appended after every rule already saved.

    `addition_id` is server-minted — two rules can validly share every
    other field. `priority` is never taken from the client: this always
    goes after the current lowest-priority rule, matching the Goals
    page's own "append at the end of the ordered list" behavior.
    Drag-and-drop reordering still goes through `PUT /recurring-additions`.

    Returns
    -------
    RecurringAddition
        The addition just persisted.
    """
    store = load_store(session, user_id)
    addition = RecurringAddition(
        addition_id=f"addition:{uuid.uuid4().hex}",
        goal_id=request.goal_id,
        start_date=request.start_date,
        frequency=request.frequency,
        end_date=request.end_date,
        mode=request.mode,
        value=request.value,
        currency=request.currency,
        priority=len(store.recurring_additions),
    )
    store = store.model_copy(update={"recurring_additions": [*store.recurring_additions, addition]})
    save_store(store, session, user_id)
    return addition


@router.put("/recurring-additions")
def put_recurring_additions(
    additions: list[RecurringAddition],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
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
    store = load_store(session, user_id)
    store = store.model_copy(update={"recurring_additions": additions})
    save_store(store, session, user_id)
    return store.recurring_additions


@router.put("/withdrawal-priorities")
def put_withdrawal_priorities(
    priorities: list[WithdrawalPriorityEntry],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[WithdrawalPriorityEntry]:
    """Replace the whole withdrawal-priority list — the order goals are drawn down from when unallocated goes negative.

    Returns
    -------
    list[WithdrawalPriorityEntry]
        The priorities just persisted.
    """
    store = load_store(session, user_id)
    store = store.model_copy(update={"withdrawal_priorities": priorities})
    save_store(store, session, user_id)
    return store.withdrawal_priorities


@router.get("/goals/summary")
def get_goals_summary(
    *,
    as_of: date | None = None,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalsSummary:
    """Every goal's balance, plus unallocated money, as of `as_of` (today if omitted).

    Both are always recomputed fresh from postings and contributions —
    see `dashboard.goals` — never a stored figure.

    Returns
    -------
    GoalsSummary
    """
    postings, store = _resolved_postings_and_store(state.config, session, user_id)
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
    *,
    as_of: date | None = None,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
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
    postings, store = _resolved_postings_and_store(state.config, session, user_id)
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
    save_store(store, session, user_id)
    return list(new_contributions.values())


@router.post("/goals/run-withdrawal-automation")
def post_run_withdrawal_automation(
    *,
    as_of: date | None = None,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
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
    postings, store = _resolved_postings_and_store(state.config, session, user_id)
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
    save_store(store, session, user_id)
    remaining_shortfall = max(0.0, shortfall - sum(-amount for _, amount in drawn))
    return WithdrawalAutomationResult(
        withdrawals=list(new_contributions.values()), remaining_shortfall=remaining_shortfall
    )


@router.post("/goals/simulate-contribution")
def post_simulate_contribution(
    payload: SimulateContributionRequest,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
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
    postings, store = _resolved_postings_and_store(state.config, session, user_id)
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
