"""Goal endpoints — spans `accounting.dashboard.goals` (reads) and `accounting.ledger.goal_automations` (actions)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    GoalAutomationCreate,
    GoalAutomationIdResponse,
    GoalAutomationUpdate,
    GoalContributionCreate,
    GoalContributionIdResponse,
    GoalContributionUpdate,
    GoalCreate,
    GoalIdResponse,
    GoalsSummary,
    GoalUpdate,
    SimulateContributionRequest,
    SimulateContributionResult,
    WithdrawalAutomationResult,
)
from accounting.api.dependencies import _display_currency, _resolved_postings_and_store
from accounting.dashboard.goals import all_goal_balances, contributions_to_frame, unallocated_balance
from accounting.ledger.goal_automations import (
    next_recurring_occurrence,
    run_recurring_additions,
    run_withdrawal_automation,
)
from accounting.models import CurrencyCode, Goal, GoalAutomation, GoalAutomationDirection, GoalContribution
from accounting.repositories.planning import (
    delete_goal,
    goal_automation_exists,
    goal_contribution_exists,
    insert_goal,
    insert_goal_contributions,
    load_goal_automations,
    load_goals,
    remove_goal_automation,
    remove_goal_contribution,
    replace_goal_automations,
    replace_goal_contributions,
    replace_goals,
    update_goal,
    upsert_goal_automation,
    upsert_goal_contribution,
)
from accounting.store import next_available_color
from db.current_user import get_current_user_id
from db.money import quantize_money
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
    existing = load_goals(session, user_id)
    goal = Goal(
        goal_id=f"goal:{uuid.uuid4().hex}",
        name=request.name,
        target_amount=request.target_amount,
        target_currency=request.target_currency,
        target_date=request.target_date,
        color=next_available_color(goal.color for goal in existing.values()),
        created_at=datetime.now(tz=UTC),
    )
    insert_goal(session, user_id, goal)
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
    replace_goals(session, user_id, goals.values())
    session.commit()
    return goals


@router.patch("/goals/{goal_id}")
def patch_goal(
    goal_id: str,
    request: GoalUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Goal:
    """Update one existing goal in place, without touching any other goal already saved.

    A true per-resource write — see `repositories.planning.update_goal`.
    Guarded by `request.expected_version`, this goal's own row version,
    so an edit to this one goal can never spuriously conflict with — or
    be silently overwritten by — an unrelated save elsewhere.

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

    No version check — see `repositories.planning.delete_goal`'s own
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
    replace_goal_contributions(session, user_id, contributions.values())
    session.commit()
    return contributions


def _reject_wrong_direction(automations: list[GoalAutomation], direction: GoalAutomationDirection) -> None:
    """Refuse a whole-list replace that carries automations of the other direction.

    Both directions live in one table now, and each whole-list `PUT`
    deletes only its own direction's rows before reinserting — so a body
    mixing the two would silently drop the entries that don't match.
    A 400 at the edge says that, rather than letting
    `repositories.planning.replace_goal_automations` raise a `ValueError`
    into a 500.

    Raises
    ------
    HTTPException
        400 if any entry's `direction` isn't `direction`.
    """
    wrong = [automation.automation_id for automation in automations if automation.direction != direction]
    if wrong:
        raise HTTPException(status_code=400, detail=f"Every automation here must be a {direction!r}: {wrong}")


def _validate_remainder_invariant(automations: list[GoalAutomation]) -> None:
    """Enforce the whole-list `remainder` rules against a full contribution-automation set.

    Shared by the whole-list `PUT` and the single-row `PATCH` so both reject
    the same illegal states: a single-row edit is validated against the list it
    would produce, never in isolation — otherwise a `PATCH` could create a
    second `remainder` row, or move the `remainder` row off the lowest
    priority, a state `PUT` itself refuses.

    The first of the two rules is *also* structural now — `goal_automations`
    carries `UNIQUE (user_id) WHERE mode = 'remainder'` (see
    `db.goals.GoalAutomation`) — and this check stays on top of it anyway,
    because the index's only vocabulary is a unique violation, which reaches
    a client as a 500. The second rule cannot be an index at all: "is the
    lowest-priority row" is a statement about the whole list's ordering, not
    about any one row.

    Raises
    ------
    HTTPException
        400 if more than one automation uses `mode="remainder"`, or one does but isn't the lowest-priority row.
    """
    remainder = [automation for automation in automations if automation.mode == "remainder"]
    if len(remainder) > 1:
        raise HTTPException(status_code=400, detail="Only one contribution automation may use mode='remainder'")
    if remainder and remainder[0].priority != max((a.priority for a in automations), default=0):
        raise HTTPException(status_code=400, detail="A 'remainder' automation must be the lowest-priority row")


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
        account_id=request.account_id,
        source_posting_id=request.source_posting_id,
        origin=request.origin,
        edited=request.edited,
    )
    upsert_goal_contribution(contribution, session, user_id)
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
    if not goal_contribution_exists(session, user_id, contribution_id):
        raise HTTPException(status_code=404, detail=f"Goal contribution {contribution_id!r} not found")
    contribution = GoalContribution(
        contribution_id=contribution_id,
        goal_id=request.goal_id,
        date=request.date,
        amount=request.amount,
        currency=request.currency,
        note=request.note,
        account_id=request.account_id,
        source_posting_id=request.source_posting_id,
        origin=request.origin,
        edited=request.edited,
    )
    upsert_goal_contribution(contribution, session, user_id)
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
    if not remove_goal_contribution(session, user_id, contribution_id):
        raise HTTPException(status_code=404, detail=f"Goal contribution {contribution_id!r} not found")
    session.commit()
    return GoalContributionIdResponse(contribution_id=contribution_id)


@router.post("/goal-automations/contributions")
def post_goal_automation(
    request: GoalAutomationCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalAutomation:
    """Create one new scheduled contribution automation, appended after every one already saved.

    `automation_id` is server-minted — two rules can validly share every
    other field. `priority` is never taken from the client: this always
    goes after the current lowest-priority contribution, matching the
    Goals page's own "append at the end of the ordered list" behavior.
    Drag-and-drop reordering still goes through
    `PUT /goal-automations/contributions`.

    Returns
    -------
    GoalAutomation
        The automation just persisted.
    """
    existing = [
        automation for automation in load_goal_automations(session, user_id) if automation.direction == "contribution"
    ]
    automation = GoalAutomation(
        automation_id=f"addition:{uuid.uuid4().hex}",
        goal_id=request.goal_id,
        direction="contribution",
        priority=len(existing),
        start_date=request.start_date,
        frequency=request.frequency,
        end_date=request.end_date,
        mode=request.mode,
        value=request.value,
        currency=request.currency,
    )
    upsert_goal_automation(automation, session, user_id)
    return automation


@router.put("/goal-automations/contributions")
def put_goal_contribution_automations(
    automations: list[GoalAutomation],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[GoalAutomation]:
    """Replace the whole contribution-automation list — the priority-ordered allocation rules.

    Scoped to `direction="contribution"`: the withdrawal ordering lives in
    the same table now but is replaced by its own endpoint, so neither
    list can wipe the other.

    Rejects an illegal list with a 400 via `_validate_remainder_invariant`
    (more than one `mode="remainder"`, or a `remainder` row that isn't the
    lowest priority) — the same check the single-row `PATCH` enforces.

    Returns
    -------
    list[GoalAutomation]
        The automations just persisted. Answers 400 if any entry is not a
        `contribution`, or if the `remainder` invariant is broken.
    """
    _reject_wrong_direction(automations, "contribution")
    _validate_remainder_invariant(automations)
    replace_goal_automations(session, user_id, automations, "contribution")
    session.commit()
    return automations


@router.patch("/goal-automations/{automation_id}")
def patch_goal_automation(
    automation_id: str,
    request: GoalAutomationUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalAutomation:
    """Edit one contribution automation in place, without touching any other. Scoped, last-write-wins.

    A single-rule field edit no longer round-trips through the whole-list
    `PUT` (which blanket-reinserts every rule and could revert a concurrent
    edit to a different one); see `repositories.planning.upsert_goal_automation`.

    Returns
    -------
    GoalAutomation
        The rule as persisted after the edit.

    Raises
    ------
    HTTPException
        404 if no automation with `automation_id` exists.
    """
    if not goal_automation_exists(session, user_id, automation_id):
        raise HTTPException(status_code=404, detail=f"Goal automation {automation_id!r} not found")
    automation = GoalAutomation(
        automation_id=automation_id,
        goal_id=request.goal_id,
        direction="contribution",
        priority=request.priority,
        start_date=request.start_date,
        frequency=request.frequency,
        end_date=request.end_date,
        mode=request.mode,
        value=request.value,
        currency=request.currency,
    )
    # Validate against the whole list this edit would produce, not the row in
    # isolation — the single-row PATCH must not be able to reach a state the
    # whole-list PUT would reject (a second `remainder`, or one out of order).
    effective = [
        automation if existing.automation_id == automation_id else existing
        for existing in load_goal_automations(session, user_id)
        if existing.direction == "contribution" or existing.automation_id == automation_id
    ]
    _validate_remainder_invariant(effective)
    upsert_goal_automation(automation, session, user_id)
    return automation


@router.delete("/goal-automations/{automation_id}")
def delete_goal_automation_route(
    automation_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalAutomationIdResponse:
    """Delete one goal automation, without touching any other. Idempotent, no version check.

    Returns
    -------
    GoalAutomationIdResponse
        The automation id just deleted.

    Raises
    ------
    HTTPException
        404 if no automation with `automation_id` exists.
    """
    if not remove_goal_automation(session, user_id, automation_id):
        raise HTTPException(status_code=404, detail=f"Goal automation {automation_id!r} not found")
    session.commit()
    return GoalAutomationIdResponse(automation_id=automation_id)


@router.put("/goal-automations/withdrawals")
def put_goal_withdrawal_automations(
    automations: list[GoalAutomation],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[GoalAutomation]:
    """Replace the whole withdrawal ordering — which goals are drawn down, and in what order, when unallocated dips.

    A pure ordering + set-membership operation (no free text, schedule, or
    amount anywhere — a withdrawal automation carries none), so it's
    last-write-wins by nature: whichever ordering was submitted last is
    the intended one. Scoped to `direction="withdrawal"`, so it never
    touches the contribution automations sharing the table.

    Returns
    -------
    list[GoalAutomation]
        The withdrawal automations just persisted. Answers 400 if any
        entry is not a `withdrawal`.
    """
    _reject_wrong_direction(automations, "withdrawal")
    replace_goal_automations(session, user_id, automations, "withdrawal")
    session.commit()
    return automations


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
    postings, store = _resolved_postings_and_store(session, user_id)
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
    """Run every contribution automation whose most recent scheduled occurrence hasn't already run.

    Idempotent by construction: each automation's occurrence writes a
    contribution under a deterministic id
    (`f"auto:{automation_id}:{occurrence.isoformat()}"`, see
    `ledger.goal_automations.next_recurring_occurrence`); calling this
    again before the next occurrence is a no-op for any automation that id
    already exists for. There is no background scheduler in this app —
    this is meant to be called when the Goals page loads, which is the
    natural moment a user would notice a change anyway.

    Returns
    -------
    list[GoalContribution]
        The new contributions just written (empty if nothing was due).
    """
    postings, store = _resolved_postings_and_store(session, user_id)
    as_of_date = as_of or datetime.now(UTC).date()
    existing_ids = set(store.goal_contributions.keys())
    scheduled = [automation for automation in store.goal_automations if automation.direction == "contribution"]

    occurrences: dict[str, date] = {}
    for automation in scheduled:
        occurrence = next_recurring_occurrence(automation, as_of_date)
        if occurrence is None:
            continue
        if f"auto:{automation.automation_id}:{occurrence.isoformat()}" in existing_ids:
            continue
        occurrences[automation.automation_id] = occurrence

    due = [automation for automation in scheduled if automation.automation_id in occurrences]
    if not due:
        return []

    contributions_frame = contributions_to_frame(store.goal_contributions)
    unallocated = unallocated_balance(postings, store.accounts, contributions_frame, as_of_date)
    funded = run_recurring_additions(due, unallocated)

    by_goal_automation = {automation.goal_id: automation for automation in due}
    new_contributions: dict[str, GoalContribution] = {}
    for goal_id, amount in funded:
        automation = by_goal_automation[goal_id]
        occurrence = occurrences[automation.automation_id]
        contribution_id = f"auto:{automation.automation_id}:{occurrence.isoformat()}"
        new_contributions[contribution_id] = GoalContribution(
            contribution_id=contribution_id,
            goal_id=goal_id,
            date=datetime.combine(occurrence, datetime.min.time()),
            # Computed in the float analytics projection; re-quantized here
            # because it is about to be stored. See `accounting.ledger.frame`.
            amount=quantize_money(amount),
            currency=automation.currency or "USD",
            note="Recurring addition",
            origin="automation",
        )

    insert_goal_contributions(session, user_id, new_contributions.values())
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
    postings, store = _resolved_postings_and_store(session, user_id)
    as_of_date = as_of or datetime.now(UTC).date()
    contributions_frame = contributions_to_frame(store.goal_contributions)
    unallocated = unallocated_balance(postings, store.accounts, contributions_frame, as_of_date)
    if unallocated >= 0:
        return WithdrawalAutomationResult(withdrawals=[], remaining_shortfall=0.0)

    shortfall = -unallocated
    balances = all_goal_balances(contributions_frame, list(store.goals.keys()), as_of_date)
    withdrawals = [automation for automation in store.goal_automations if automation.direction == "withdrawal"]
    drawn = run_withdrawal_automation(withdrawals, balances, shortfall)

    existing_ids = set(store.goal_contributions.keys())
    new_contributions: dict[str, GoalContribution] = {}
    for goal_id, amount in drawn:
        contribution_id = _next_contribution_id(existing_ids, f"auto-withdrawal:{goal_id}:{as_of_date.isoformat()}")
        existing_ids.add(contribution_id)
        new_contributions[contribution_id] = GoalContribution(
            contribution_id=contribution_id,
            goal_id=goal_id,
            date=datetime(as_of_date.year, as_of_date.month, as_of_date.day),  # noqa: DTZ001  (ledger dates are naive)
            # Re-quantized on the way into storage, as above.
            amount=quantize_money(amount),
            note="Withdrawal automation — unallocated went negative",
            origin="automation",
        )

    insert_goal_contributions(session, user_id, new_contributions.values())
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
    postings, store = _resolved_postings_and_store(session, user_id)
    contributions_frame = contributions_to_frame(store.goal_contributions)
    unallocated_as_of_date = unallocated_balance(postings, store.accounts, contributions_frame, payload.date)
    exceeds_unallocated = payload.amount > unallocated_as_of_date

    today = datetime.now(UTC).date()
    unallocated_today = unallocated_balance(postings, store.accounts, contributions_frame, today)
    projected_before_run = unallocated_today - payload.amount
    scheduled = [automation for automation in store.goal_automations if automation.direction == "contribution"]
    funded_next_run = run_recurring_additions(scheduled, max(projected_before_run, 0.0))
    projected_next_run_unallocated = projected_before_run - sum(amount for _, amount in funded_next_run)

    return SimulateContributionResult(
        unallocated_as_of_date=unallocated_as_of_date,
        exceeds_unallocated=exceeds_unallocated,
        projected_next_run_unallocated=projected_next_run_unallocated,
        would_go_negative=projected_next_run_unallocated < 0,
    )
