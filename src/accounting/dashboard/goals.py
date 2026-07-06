"""Derived quantities for savings goals — balances and unallocated money are always computed fresh, never stored.

A goal's balance and the amount of money not yet allocated to any goal
are both pure derivations over `GoalContribution` rows (see
`models.GoalContribution`) and the resolved posting ledger — never a
cached figure that could drift out of sync with the contributions or
postings it's computed from, the same reasoning `models.Budget`'s own
docstring gives for spending actuals. "Unallocated" is deliberately never
written as a goal row of its own — it is this module's residual, always
recomputed, per the user's own spec ("It's the residual, computed, not written.").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from accounting.dashboard.income_statement import net_income_expense_total

if TYPE_CHECKING:
    from datetime import date

    from accounting.models import Account, GoalContribution

CONTRIBUTION_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "contribution_id": pl.Utf8,
    "goal_id": pl.Utf8,
    "date": pl.Datetime("us"),
    "amount": pl.Float64,
    "currency": pl.Utf8,
    "note": pl.Utf8,
    "source_posting_id": pl.Utf8,
    "origin": pl.Utf8,
    "edited": pl.Boolean,
}


def contributions_to_frame(contributions: dict[str, GoalContribution]) -> pl.DataFrame:
    """Turn persisted `GoalContribution`s into the flat frame shape every function below reads.

    Parameters
    ----------
    contributions
        Every contribution, keyed by `contribution_id`.

    Returns
    -------
    polars.DataFrame
        Sorted by `date` — the order every balance computation assumes.
    """
    if not contributions:
        return pl.DataFrame(schema=CONTRIBUTION_SCHEMA)
    return pl.DataFrame([c.model_dump() for c in contributions.values()], schema=CONTRIBUTION_SCHEMA).sort("date")


def goal_balance(contributions: pl.DataFrame, goal_id: str, as_of: date) -> float:
    """Return the goal's running balance at `as_of` — the sum of its own contributions up to and including that day.

    Parameters
    ----------
    contributions
        As `contributions_to_frame` returns.
    goal_id
        Which goal to sum.
    as_of
        Last day to include, inclusive.

    Returns
    -------
    float
        `0.0` for a goal with no contributions yet.
    """
    legs = contributions.filter((pl.col("goal_id") == goal_id) & (pl.col("date").dt.date() <= as_of))
    if legs.is_empty():
        return 0.0
    return float(legs["amount"].sum())


def all_goal_balances(contributions: pl.DataFrame, goal_ids: list[str], as_of: date) -> dict[str, float]:
    """`goal_balance` for every id in `goal_ids` at once.

    Parameters
    ----------
    contributions
        As `contributions_to_frame` returns.
    goal_ids
        Every goal to report a balance for.
    as_of
        Last day to include, inclusive.

    Returns
    -------
    dict[str, float]
        One entry per id in `goal_ids`, `0.0` for a goal with no contributions yet.
    """
    return {goal_id: goal_balance(contributions, goal_id, as_of) for goal_id in goal_ids}


def unallocated_balance(
    postings: pl.DataFrame, accounts: dict[str, Account], contributions: pl.DataFrame, as_of: date
) -> float:
    """Money that's neither gone toward a real expense nor been earmarked into any goal yet, as of `as_of`.

    `(real income - real expense, cumulative through as_of)` minus
    `(every goal's contributions, cumulative through as_of)`.

    Parameters
    ----------
    postings
        The full, resolved posting ledger.
    accounts
        Every known account, keyed by `account_id`.
    contributions
        As `contributions_to_frame` returns.
    as_of
        Last day to include, inclusive.

    Returns
    -------
    float
    """
    net_income = net_income_expense_total(postings, accounts, as_of)
    dated = contributions.filter(pl.col("date").dt.date() <= as_of)
    total_contributed = 0.0 if dated.is_empty() else float(dated["amount"].sum())
    return net_income - total_contributed
