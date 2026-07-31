"""Goal endpoints — spans `accounting.dashboard.goals` (reads) and `accounting.ledger.goal_automations` (actions)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Annotated

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    AutomationOrder,
    GoalAutomationCreate,
    GoalAutomationUpdate,
    GoalContributionCreate,
    GoalContributionUpdate,
    GoalCreate,
    GoalsSummary,
    GoalUpdate,
    SimulateContributionRequest,
    SimulateContributionResult,
    WithdrawalAutomationCreate,
    WithdrawalAutomationResult,
)
from accounting.api.dependencies import (
    _currencies_in_use,
    _display_currency,
    _rates_by_date,
    _resolved_postings,
)
from accounting.api.entities import Goal, GoalAutomation, GoalContribution
from accounting.api.locations import CREATED_WITH_LOCATION, created_or_replaced, location_of
from accounting.dashboard.goals import all_goal_balances, contributions_to_frame, unallocated_balance
from accounting.ledger.goal_automations import (
    next_recurring_occurrence,
    run_recurring_additions,
    run_withdrawal_automation,
)
from accounting.models import CurrencyCode, GoalAutomationDirection
from accounting.models import Goal as DomainGoal
from accounting.models import GoalAutomation as DomainGoalAutomation
from accounting.models import GoalContribution as DomainGoalContribution
from accounting.repositories.accounts import load_opening_balances
from accounting.repositories.planning import (
    delete_goal,
    goal_automation_exists,
    goal_contribution_exists,
    insert_goal,
    insert_goal_contributions,
    load_goal_automations,
    load_goal_contributions,
    load_goals,
    remove_goal_automation,
    remove_goal_contribution,
    replace_goal_automations,
    update_goal,
    upsert_goal_automation,
    upsert_goal_contribution,
    withdrawal_automation_id,
)
from accounting.taxonomy import next_available_color, seeded_accounts
from db.current_user import get_current_user_id
from db.money import quantize_money
from db.session import get_db

if TYPE_CHECKING:
    from accounting.ledger.currency import DisplayCurrency
    from accounting.models import Account, OpeningBalance

router = APIRouter()


@dataclass(frozen=True)
class _UnallocatedBasis:
    """Everything `dashboard.goals.unallocated_balance` reads, loaded once per request.

    Three of the four endpoints that ask for unallocated money used to
    call it with no `DisplayCurrency` at all, which meant the default
    (`{"USD": 1.0}`) — and both the posting and the contribution
    conversions *left*-join their rate table, so a EUR row got a null rate,
    a null amount, and was silently skipped by the sum. The figure that
    gates a contribution therefore disagreed with the one
    `GET /goals/summary` displayed, by however much non-USD money the user
    held. Loading the currencies in use here, once, is what makes every
    caller's answer the same answer.

    Held as a bundle rather than re-derived per call because
    `post_simulate_contribution` needs the figure on two different dates
    from the same request, and the ledger read is by far the most
    expensive part of it. `rates_by_date` is held for the same reason and
    is date-independent anyway; only the scalar rates the opening-balance
    term uses are rebuilt per date.
    """

    postings: pl.DataFrame
    accounts: dict[str, Account]
    opening_balances: dict[str, OpeningBalance]
    currencies: set[CurrencyCode]
    display_currency: CurrencyCode
    rates_by_date: pl.DataFrame | None = field(default=None, compare=False)

    def display_at(self, as_of: date) -> DisplayCurrency:
        """Build the rates every goal figure on this request shares: dated for flows, `as_of` for stocks.

        Every goal figure means both of them — `all_goal_balances` sums the
        same dated contributions `unallocated_balance` subtracts, so a
        balance and the residual it is subtracted from have to be built
        from one rate table or the two do not add up.

        Propagates `_display_currency`'s 400 when a currency in use has no
        rate history, rather than quietly dropping that currency's rows.

        Returns
        -------
        accounting.ledger.currency.DisplayCurrency
        """
        return replace(
            _display_currency(self.display_currency, self.currencies, as_of), rates_by_date=self.rates_by_date
        )

    def at(self, contributions: pl.DataFrame, as_of: date) -> float:
        """Unallocated money as of one date: dated flows at their own rates, opening balances at `as_of`'s.

        Returns
        -------
        float
        """
        return unallocated_balance(
            self.postings, self.accounts, contributions, as_of, self.display_at(as_of), self.opening_balances
        )


def _unallocated_basis(
    session: Session, user_id: uuid.UUID, display_currency: CurrencyCode = "USD"
) -> _UnallocatedBasis:
    """Read everything unallocated money is derived from.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose ledger, accounts, opening balances and currencies to read.
    display_currency
        The currency to answer in. The three automation/simulation
        endpoints take no such query parameter and get the same `"USD"`
        default every other endpoint's does, since their answer feeds a
        comparison rather than a display.

    Returns
    -------
    _UnallocatedBasis
    """
    currencies = _currencies_in_use(session, user_id)
    return _UnallocatedBasis(
        postings=_resolved_postings(session, user_id),
        accounts=seeded_accounts(session, user_id),
        opening_balances=load_opening_balances(session, user_id),
        currencies=currencies,
        display_currency=display_currency,
        rates_by_date=_rates_by_date(display_currency, currencies),
    )


@router.post("/goals", status_code=201, responses=CREATED_WITH_LOCATION)
def post_goal(
    request: GoalCreate,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Goal:
    """Create one new goal, without touching any other goal already saved.

    `goal_id` is server-minted — two goals can validly share a name, so
    there's no natural key two "the same" goal would collide on, which is
    why the `201` is unconditional. `color`
    is picked to be distinct from every color already assigned to an
    existing goal, the same `taxonomy.next_available_color` helper
    categories already use for the same purpose.

    Returns
    -------
    Goal
        The goal just persisted.
    """
    existing = load_goals(session, user_id)
    goal = DomainGoal(
        goal_id=f"goal:{uuid.uuid4().hex}",
        name=request.name,
        target_amount=request.target_amount,
        target_currency=request.target_currency,
        target_date=request.target_date,
        color=next_available_color(goal.color for goal in existing.values()),
        created_at=datetime.now(tz=UTC),
    )
    insert_goal(session, user_id, goal)
    location_of(http_request, response, "get_goal", goal_id=goal.goal_id)
    return Goal.from_domain(goal)


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
    goal = DomainGoal(
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
    return Goal.from_domain(updated)


@router.delete("/goals/{goal_id}", status_code=204)
def delete_goal_route(
    goal_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Delete one goal, without touching any other goal already saved.

    No version check — see `repositories.planning.delete_goal`'s own
    docstring for why deleting an already-gone goal is a plain 404, not a
    409: there's nothing left to conflict with.


    Raises
    ------
    HTTPException
        404 if no goal with `goal_id` exists.
    """
    deleted = delete_goal(session, user_id, goal_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id!r} not found")
    session.commit()


def _reorder_automations(
    session: Session, user_id: uuid.UUID, automation_ids: list[str], direction: GoalAutomationDirection
) -> list[DomainGoalAutomation]:
    """Renumber one direction's automations into `automation_ids`' order, changing nothing else about them.

    The submitted id set must equal the persisted one exactly — not a
    subset, not a superset. That equality is what makes this a reorder
    instead of a whole-list replace under a new name: an insertion, a
    deletion or a field edit has nowhere to hide in a body that carries no
    fields and may name no id the server isn't already storing. It also
    subsumes the direction check the old whole-list `PUT` needed, since the
    other direction's ids are simply not in this direction's set.

    `priority` comes from list position, 0-based, matching what
    `post_goal_automation` assigns a newly appended rule.

    Parameters
    ----------
    session
        An open database session; committed here on success.
    user_id
        Whose automations to reorder.
    automation_ids
        The ids persisted for `direction`, in the desired order.
    direction
        Which of the two orderings is being submitted.

    Returns
    -------
    list[DomainGoalAutomation]
        The same automations, renumbered, in the submitted order.

    Raises
    ------
    HTTPException
        400 if `automation_ids` repeats an id, or is not exactly the set
        persisted for `direction`, or would leave a `remainder` rule
        somewhere other than last.
    """
    persisted = {
        automation.automation_id: automation
        for automation in load_goal_automations(session, user_id)
        if automation.direction == direction
    }
    submitted = set(automation_ids)
    if len(submitted) != len(automation_ids):
        duplicated = sorted({name for name in automation_ids if automation_ids.count(name) > 1})
        raise HTTPException(status_code=400, detail=f"An automation cannot appear twice in one order: {duplicated}")
    if submitted != set(persisted):
        missing = sorted(set(persisted) - submitted)
        unknown = sorted(submitted - set(persisted))
        raise HTTPException(
            status_code=400,
            detail=(
                f"An order must list every {direction!r} automation exactly once and no others — "
                f"missing: {missing}, not a persisted {direction!r}: {unknown}"
            ),
        )
    reordered = [
        persisted[automation_id].model_copy(update={"priority": priority})
        for priority, automation_id in enumerate(automation_ids)
    ]
    _validate_remainder_invariant(reordered)
    replace_goal_automations(session, user_id, reordered, direction)
    session.commit()
    return reordered


def _validate_remainder_invariant(automations: list[DomainGoalAutomation]) -> None:
    """Enforce the `remainder` rules against a full contribution-automation set.

    Shared by the reorder and the single-row `PATCH` so both reject the
    same illegal states: a single-row edit is validated against the list it
    would produce, never in isolation — otherwise a `PATCH` could create a
    second `remainder` row, or move the `remainder` row off the lowest
    priority, a state a reorder itself refuses.

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


@router.get("/goal-contributions/{contribution_id}")
def get_goal_contribution(
    contribution_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalContribution:
    """Return one dated allocation by id — the address `post_goal_contribution` advertises.

    Returns
    -------
    GoalContribution

    Raises
    ------
    HTTPException
        404 if no contribution has this id.
    """
    contribution = load_goal_contributions(session, user_id).get(contribution_id)
    if contribution is None:
        raise HTTPException(status_code=404, detail=f"Goal contribution {contribution_id!r} not found")
    return GoalContribution.from_domain(contribution)


@router.post("/goal-contributions", status_code=201, responses=CREATED_WITH_LOCATION)
def post_goal_contribution(
    request: GoalContributionCreate,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalContribution:
    """Record one new dated allocation, without touching any other contribution already recorded.

    Unlike a budget's `(month, category_id)`, a contribution is an
    arbitrary event with no natural key to derive an id from, so the
    server generates an opaque one — two contributions with identical
    fields (e.g. the same goal, date, and amount entered twice) are
    distinct rows, not a collision. So the `201` is unconditional even
    though the write below goes through an upsert: the id it upserts on
    was minted moments earlier and cannot already exist.

    Returns
    -------
    GoalContribution
        The contribution just persisted.
    """
    contribution = DomainGoalContribution(
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
    location_of(http_request, response, "get_goal_contribution", contribution_id=contribution.contribution_id)
    return GoalContribution.from_domain(contribution)


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
    contribution = DomainGoalContribution(
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
    return GoalContribution.from_domain(contribution)


@router.delete("/goal-contributions/{contribution_id}", status_code=204)
def delete_goal_contribution(
    contribution_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Remove one contribution, without touching any other.

    Raises
    ------
    HTTPException
        404 if no contribution with this id exists.
    """
    if not remove_goal_contribution(session, user_id, contribution_id):
        raise HTTPException(status_code=404, detail=f"Goal contribution {contribution_id!r} not found")
    session.commit()


@router.get("/goal-automations/{automation_id}")
def get_goal_automation(
    automation_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalAutomation:
    """Return one automation by id, either direction — the address `post_goal_automation` advertises.

    Not direction-scoped, matching `PATCH` and `DELETE` on this same path:
    an automation is addressed by its id alone, and `contribution` versus
    `withdrawal` is a field on it, not part of its address.

    Returns
    -------
    GoalAutomation

    Raises
    ------
    HTTPException
        404 if no automation has this id.
    """
    automation = next(
        (a for a in load_goal_automations(session, user_id) if a.automation_id == automation_id),
        None,
    )
    if automation is None:
        raise HTTPException(status_code=404, detail=f"Goal automation {automation_id!r} not found")
    return GoalAutomation.from_domain(automation)


@router.post("/goal-automations/contributions", status_code=201, responses=CREATED_WITH_LOCATION)
def post_goal_automation(
    request: GoalAutomationCreate,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalAutomation:
    """Create one new scheduled contribution automation, appended after every one already saved.

    `automation_id` is server-minted, so the `201` is unconditional — two
    rules can validly share every other field. Its `Location` points at
    `GET /goal-automations/{automation_id}`, dropping the `contributions`
    segment: the direction picks which collection this posts *to*, and is
    not part of the created row's own address. `priority` is never taken
    from the client: this always
    goes after the current lowest-priority contribution, matching the
    Goals page's own "append at the end of the ordered list" behavior.
    Drag-and-drop reordering goes through
    `PUT /goal-automations/contributions/order`.

    A body that would break a `remainder` rule is a 400 from
    `_validate_remainder_invariant`, the same check the reorder and the
    single-row `PATCH` run.

    Returns
    -------
    GoalAutomation
        The automation just persisted.
    """
    existing = [
        automation for automation in load_goal_automations(session, user_id) if automation.direction == "contribution"
    ]
    automation = DomainGoalAutomation(
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
    # Validated against the list this would produce, exactly as `patch_goal_automation`
    # and the reorder are. Skipping it here left the `remainder` rules to the
    # `goal_automations` partial unique index alone, whose only vocabulary is a
    # unique violation — so a second `remainder` reached the client as a 500
    # instead of the documented 400, and appending an ordinary rule *after* a
    # `remainder` one persisted a state the reorder would refuse.
    _validate_remainder_invariant([*existing, automation])
    upsert_goal_automation(automation, session, user_id)
    location_of(http_request, response, "get_goal_automation", automation_id=automation.automation_id)
    return GoalAutomation.from_domain(automation)


@router.put("/goal-automations/contributions/order")
def put_goal_contribution_automation_order(
    order: AutomationOrder,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[GoalAutomation]:
    """Set the order the contribution automations run in — the priority a fixed-amount rule funds ahead of a lower one.

    A named operation on a real single resource (this collection's
    *ordering*), which is why it stays a `PUT` and not a `PATCH` per
    automation: a reorder is atomic across every row in the list, and n
    separate `PATCH`es of `priority` can only approximate that, passing
    through states with two rules at the same priority. Idempotent —
    submitting the same order twice lands the same priorities.

    Scoped to `direction="contribution"`: the withdrawal ordering shares
    the table but has its own route, so neither can renumber the other.

    Returns
    -------
    list[GoalAutomation]
        The contribution automations, renumbered, in the submitted order.
        Answers 400 (from `_reorder_automations`) if the submitted ids
        aren't exactly the persisted contribution automations, or if the
        order would leave a `mode="remainder"` rule anywhere but last.
    """
    return [
        GoalAutomation.from_domain(automation)
        for automation in _reorder_automations(session, user_id, order.automation_ids, "contribution")
    ]


@router.patch("/goal-automations/{automation_id}")
def patch_goal_automation(
    automation_id: str,
    request: GoalAutomationUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalAutomation:
    """Edit one contribution automation in place, without touching any other. Scoped, last-write-wins.

    A single-rule field edit does not round-trip through a whole-list
    write (which blanket-reinserted every rule and could revert a
    concurrent edit to a different one); see
    `repositories.planning.upsert_goal_automation`. `priority` is carried
    unchanged — the reorder route owns it.

    Returns
    -------
    GoalAutomation
        The rule as persisted after the edit.

    Raises
    ------
    HTTPException
        404 if no *contribution* automation with `automation_id` exists.
    """
    # Direction-scoped on purpose. The two directions are separate resources
    # over one table, and this handler hard-codes `direction="contribution"`
    # below — so a direction-blind existence check would let a withdrawal's id
    # through and silently rewrite that row into a contribution, giving it a
    # funding schedule that moves money the opposite way and dropping it out of
    # the drawdown order. A withdrawal id names no contribution, so 404 is the
    # honest answer.
    if not goal_automation_exists(session, user_id, automation_id, direction="contribution"):
        raise HTTPException(status_code=404, detail=f"Goal automation {automation_id!r} not found")
    automation = DomainGoalAutomation(
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
    # reorder route would reject (a second `remainder`, or one out of order).
    effective = [
        automation if existing.automation_id == automation_id else existing
        for existing in load_goal_automations(session, user_id)
        if existing.direction == "contribution"
    ]
    _validate_remainder_invariant(effective)
    upsert_goal_automation(automation, session, user_id)
    return GoalAutomation.from_domain(automation)


@router.delete("/goal-automations/{automation_id}", status_code=204)
def delete_goal_automation_route(
    automation_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Delete one goal automation, without touching any other. Idempotent, no version check.

    Raises
    ------
    HTTPException
        404 if no automation with `automation_id` exists.
    """
    if not remove_goal_automation(session, user_id, automation_id):
        raise HTTPException(status_code=404, detail=f"Goal automation {automation_id!r} not found")
    session.commit()


@router.post("/goal-automations/withdrawals", status_code=201, responses=created_or_replaced(GoalAutomation))
def post_withdrawal_automation(
    request: WithdrawalAutomationCreate,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GoalAutomation:
    """Put one goal into the drawdown order, appended last, without touching any other entry.

    The counterpart to `post_goal_automation` for the other direction, and
    the reason the withdrawal ordering no longer needs a whole-list write:
    adding a goal is a create, dropping one is
    `DELETE /goal-automations/{automation_id}`, and rearranging them is
    `PUT /goal-automations/withdrawals/order`.

    Unlike a contribution's minted id, this one is derived from the goal
    (`repositories.planning.withdrawal_automation_id`) because a goal sits
    at most once in the order — so re-adding a goal already in it is a
    replace, not a second row, and answers `200` with the entry's existing
    place rather than `201`. Re-adding therefore never silently moves a
    goal to the bottom of the drawdown order.

    Returns
    -------
    GoalAutomation
        The drawdown entry just persisted.
    """
    existing = [
        automation for automation in load_goal_automations(session, user_id) if automation.direction == "withdrawal"
    ]
    automation_id = withdrawal_automation_id(request.goal_id)
    already_ordered = next((a for a in existing if a.automation_id == automation_id), None)
    automation = DomainGoalAutomation(
        automation_id=automation_id,
        goal_id=request.goal_id,
        direction="withdrawal",
        priority=already_ordered.priority if already_ordered else len(existing),
    )
    upsert_goal_automation(automation, session, user_id)
    if already_ordered is None:
        location_of(http_request, response, "get_goal_automation", automation_id=automation_id)
    else:
        response.status_code = 200
    return GoalAutomation.from_domain(automation)


@router.put("/goal-automations/withdrawals/order")
def put_goal_withdrawal_automation_order(
    order: AutomationOrder,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[GoalAutomation]:
    """Set the order goals are drawn down in when unallocated money dips below zero.

    A withdrawal automation carries nothing but its goal and its place in
    this order, so the order *is* the whole resource here — which makes the
    id-set check below load-bearing rather than defensive: it is the only
    thing separating "rearrange the drawdown order" from "replace it".
    Membership changes go through `post_withdrawal_automation` and
    `DELETE /goal-automations/{automation_id}`.

    Scoped to `direction="withdrawal"`, so it never renumbers the
    contribution automations sharing the table.

    Returns
    -------
    list[GoalAutomation]
        The drawdown entries, renumbered, in the submitted order. Answers
        400 (from `_reorder_automations`) if the submitted ids aren't
        exactly the persisted withdrawal automations.
    """
    return [
        GoalAutomation.from_domain(automation)
        for automation in _reorder_automations(session, user_id, order.automation_ids, "withdrawal")
    ]


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
    as_of_date = as_of or datetime.now(UTC).date()
    basis = _unallocated_basis(session, user_id, display_currency)
    contributions = contributions_to_frame(load_goal_contributions(session, user_id))
    balances = all_goal_balances(
        contributions, list(load_goals(session, user_id).keys()), as_of_date, basis.display_at(as_of_date)
    )
    return GoalsSummary(balances=balances, unallocated=basis.at(contributions, as_of_date))


# Registered *after* `GET /goals/summary`, and that ordering is load-bearing:
# Starlette matches routes in registration order, and `/goals/summary` matches
# `/goals/{goal_id}` on a GET just as well as a real goal id does. Declared
# first, this route would swallow the summary endpoint and answer 404 for
# `goal_id="summary"`. Nothing enforces the order but this comment, so a
# future `GET /goals/<literal>` has to go above here too.
@router.get("/goals/{goal_id}")
def get_goal(
    goal_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Goal:
    """Return one goal by id — the address `post_goal` advertises.

    Returns
    -------
    Goal

    Raises
    ------
    HTTPException
        404 if no goal has this id.
    """
    goal = load_goals(session, user_id).get(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id!r} not found")
    return Goal.from_domain(goal)


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
    as_of_date = as_of or datetime.now(UTC).date()
    contributions = load_goal_contributions(session, user_id)
    existing_ids = set(contributions.keys())
    scheduled = [
        automation for automation in load_goal_automations(session, user_id) if automation.direction == "contribution"
    ]

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

    contributions_frame = contributions_to_frame(contributions)
    unallocated = _unallocated_basis(session, user_id).at(contributions_frame, as_of_date)
    funded = run_recurring_additions(due, unallocated)

    # Keyed by `automation_id`, not `goal_id`: a goal may legitimately have
    # several contribution schedules (only *withdrawals* are one-per-goal, see
    # `db.goals`' partial unique index). Keying by goal made the map
    # non-injective, so two funded schedules on one goal both resolved to
    # whichever automation came last, minted the same `contribution_id`, and the
    # second silently overwrote the first here — while `run_recurring_additions`
    # had already counted both against unallocated.
    by_automation_id = {automation.automation_id: automation for automation in due}
    new_contributions: dict[str, DomainGoalContribution] = {}
    for automation_id, amount in funded:
        automation = by_automation_id[automation_id]
        occurrence = occurrences[automation.automation_id]
        contribution_id = f"auto:{automation.automation_id}:{occurrence.isoformat()}"
        new_contributions[contribution_id] = DomainGoalContribution(
            contribution_id=contribution_id,
            goal_id=automation.goal_id,
            date=datetime.combine(occurrence, datetime.min.time()),
            # Computed in the float analytics projection; re-quantized here
            # because it is about to be stored. See `accounting.ledger.frame`.
            amount=quantize_money(amount),
            currency=automation.currency or "USD",
            note="Recurring addition",
            origin="automation",
        )

    insert_goal_contributions(session, user_id, new_contributions.values())
    return [GoalContribution.from_domain(contribution) for contribution in new_contributions.values()]


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
    as_of_date = as_of or datetime.now(UTC).date()
    contributions = load_goal_contributions(session, user_id)
    contributions_frame = contributions_to_frame(contributions)
    basis = _unallocated_basis(session, user_id)
    unallocated = basis.at(contributions_frame, as_of_date)
    if unallocated >= 0:
        return WithdrawalAutomationResult(withdrawals=[], remaining_shortfall=0.0)

    shortfall = -unallocated
    # The same rates the shortfall was computed with, so a drawdown never
    # draws a balance measured in one currency against a gap measured in
    # another — this used to take the default `{"USD": 1.0}` and skip every
    # non-USD contribution, the same defect the basis exists to close.
    balances = all_goal_balances(
        contributions_frame, list(load_goals(session, user_id).keys()), as_of_date, basis.display_at(as_of_date)
    )
    withdrawals = [
        automation for automation in load_goal_automations(session, user_id) if automation.direction == "withdrawal"
    ]
    drawn = run_withdrawal_automation(withdrawals, balances, shortfall)

    existing_ids = set(contributions.keys())
    new_contributions: dict[str, DomainGoalContribution] = {}
    for goal_id, amount in drawn:
        contribution_id = _next_contribution_id(existing_ids, f"auto-withdrawal:{goal_id}:{as_of_date.isoformat()}")
        existing_ids.add(contribution_id)
        new_contributions[contribution_id] = DomainGoalContribution(
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
        withdrawals=[GoalContribution.from_domain(contribution) for contribution in new_contributions.values()],
        remaining_shortfall=remaining_shortfall,
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
    basis = _unallocated_basis(session, user_id)
    contributions_frame = contributions_to_frame(load_goal_contributions(session, user_id))
    unallocated_as_of_date = basis.at(contributions_frame, payload.date)
    exceeds_unallocated = payload.amount > unallocated_as_of_date

    today = datetime.now(UTC).date()
    unallocated_today = basis.at(contributions_frame, today)
    projected_before_run = unallocated_today - payload.amount
    scheduled = [
        automation for automation in load_goal_automations(session, user_id) if automation.direction == "contribution"
    ]
    funded_next_run = run_recurring_additions(scheduled, max(projected_before_run, 0.0))
    projected_next_run_unallocated = projected_before_run - sum(amount for _, amount in funded_next_run)

    return SimulateContributionResult(
        unallocated_as_of_date=unallocated_as_of_date,
        exceeds_unallocated=exceeds_unallocated,
        projected_next_run_unallocated=projected_next_run_unallocated,
        would_go_negative=projected_next_run_unallocated < 0,
    )
