"""Decide how much money moves in/out of each goal for one automation run — never writes anything itself.

Both functions here are pure: given the money available and the current
ordering, they return `(goal_id, amount)` pairs for the caller to persist
as ordinary `GoalContribution` rows (see `api.post_run_recurring_additions`/
`api.post_run_withdrawal_automation`) — keeping the decision (how much
goes where) separate from the effect (writing a dated, signed row),
the same way `dashboard.paystub.propose_posting_splits` only ever proposes
a split for the caller to apply.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from accounting.models import RecurringAddition, WithdrawalPriorityEntry

_FREQUENCY_STEP_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14}
_MAX_DAY_OF_MONTH = 28


def _monthly_occurrence_on_or_before(start_date: date, as_of: date) -> date | None:
    day = min(start_date.day, _MAX_DAY_OF_MONTH)
    year, month = as_of.year, as_of.month
    if as_of.day < day:
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    candidate = date(year, month, day)
    return None if candidate < start_date else candidate


def next_recurring_occurrence(addition: RecurringAddition, as_of: date) -> date | None:
    """Return the most recent scheduled occurrence of `addition` on or before `as_of`, if any.

    `frequency="daily"`/`"weekly"`/`"biweekly"` step forward from
    `start_date` in fixed-size increments — the occurrence is whichever
    step lands on or immediately before `as_of`. `frequency="monthly"`
    instead reuses `start_date`'s own day of month (capped at 28, so
    every month actually has that day), landing on the most recent month
    whose day has already arrived.

    Parameters
    ----------
    addition
        The recurring addition, whose `start_date`/`frequency`/`end_date` define the schedule.
    as_of
        The date to compute the most recent due occurrence relative to.

    Returns
    -------
    datetime.date or None
        `None` if `as_of` is before `start_date`, or before the first
        occurrence would land within `start_date`'s own month (for
        `frequency="monthly"`) — otherwise the occurrence date, clamped to
        `end_date` if `as_of` is past it.
    """
    effective_as_of = min(as_of, addition.end_date) if addition.end_date is not None else as_of
    if effective_as_of < addition.start_date:
        return None
    if addition.frequency == "monthly":
        return _monthly_occurrence_on_or_before(addition.start_date, effective_as_of)
    step = _FREQUENCY_STEP_DAYS[addition.frequency]
    elapsed_periods = (effective_as_of - addition.start_date).days // step
    return addition.start_date + timedelta(days=elapsed_periods * step)


def run_recurring_additions(additions: list[RecurringAddition], unallocated: float) -> list[tuple[str, float]]:
    """Allocate `unallocated` money across `additions` in priority order, funding each in turn until it runs out.

    Each addition's own `value`/`mode` determines how much it wants:
    `fixed_amount` wants exactly `value`; `percent_of_unallocated` wants
    `value`% of `unallocated` as it stood *before this run started* (not
    the shrinking remainder — so two 10%-mode additions each get 10% of
    the original balance, not 10% then 10% of what's left); `remainder`
    ("whatever's left after all the others") wants the entire shrinking
    remainder at the point it runs, and is only ever meaningful on the
    lowest-priority addition. If the remainder can't fully fund an
    addition, it gets whatever's left (a partial amount), and every
    addition after it in priority order gets nothing.

    Parameters
    ----------
    additions
        Every recurring addition due to run, in any order — sorted here by `priority` (lowest first).
    unallocated
        The unallocated balance available before this run.

    Returns
    -------
    list[tuple[str, float]]
        `(goal_id, amount)` pairs, in funding order — only for additions
        that actually received a nonzero amount.
    """
    remaining = unallocated
    funded: list[tuple[str, float]] = []
    for addition in sorted(additions, key=lambda a: a.priority):
        if remaining <= 0:
            break
        if addition.mode == "fixed_amount":
            wanted = addition.value
        elif addition.mode == "percent_of_unallocated":
            wanted = unallocated * (addition.value / 100.0)
        else:
            wanted = remaining
        amount = min(wanted, remaining)
        if amount <= 0:
            continue
        funded.append((addition.goal_id, amount))
        remaining -= amount
    return funded


def run_withdrawal_automation(
    priorities: list[WithdrawalPriorityEntry], goal_balances: dict[str, float], shortfall: float
) -> list[tuple[str, float]]:
    """Draw down goals in priority order to cover a negative-unallocated `shortfall`, never taking a goal below zero.

    Moves on to the next-priority goal once one is fully exhausted. If
    every goal is drawn down before `shortfall` is covered, the returned
    withdrawals simply don't sum to the full shortfall — this never
    raises; the caller is expected to show a warning banner instead (per
    the spec: "just leave it negative and show a warning banner").

    Parameters
    ----------
    priorities
        Every goal's withdrawal priority, in any order — sorted here by `priority` (lowest first).
    goal_balances
        Each goal's current balance, keyed by `goal_id`.
    shortfall
        How much unallocated needs to be brought back up by (a positive number).

    Returns
    -------
    list[tuple[str, float]]
        `(goal_id, -amount)` pairs (negative — a withdrawal), in the order drawn.
    """
    remaining = shortfall
    withdrawals: list[tuple[str, float]] = []
    for entry in sorted(priorities, key=lambda p: p.priority):
        if remaining <= 0:
            break
        available = goal_balances.get(entry.goal_id, 0.0)
        if available <= 0:
            continue
        amount = min(available, remaining)
        withdrawals.append((entry.goal_id, -amount))
        remaining -= amount
    return withdrawals
