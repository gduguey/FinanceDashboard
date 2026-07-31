"""Derived quantities for savings goals — balances and unallocated money are always computed fresh, never stored.

A goal's balance and the amount of money not yet allocated to any goal
are both pure derivations over `GoalContribution` rows (see
`models.GoalContribution`), the resolved posting ledger, and — for
unallocated money — the opening balances accounts already held before
their first posting — never a
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
from accounting.ledger.currency import DisplayCurrency, convert, with_converted_amount
from accounting.models import VIRTUAL_ACCOUNT_KINDS
from db.money import to_analytics_float

if TYPE_CHECKING:
    from datetime import date

    from accounting.models import Account, GoalContribution, OpeningBalance

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


def _with_converted_amount(frame: pl.LazyFrame, display: DisplayCurrency) -> pl.LazyFrame:
    """Return `frame` with `amount` replaced by its `display.code`-converted value, at each row's own date.

    The contributions-frame binding of
    `ledger.currency.with_converted_amount` — a contribution keeps its own
    currency at rest (see `models.GoalContribution`) exactly like a posting
    does, so converting it for aggregation here never mutates the
    persisted row, and it is a dated flow exactly like a posting, so it
    converts at its own date's rate rather than the report date's. Stays a
    `LazyFrame` in and out, so a caller can keep composing (a `group_by`,
    a further `filter`) before collecting once at its own boundary.

    Parameters
    ----------
    frame
        Any lazy frame with `amount`, `currency` and `date` columns — a contributions frame here.
    display
        The currency (and rates) every row's amount is converted into.

    Returns
    -------
    polars.LazyFrame
        `frame`, with `amount` converted into `display.code`.
    """
    return with_converted_amount(frame, display, "currency", "date")


def all_goal_balances(
    contributions: pl.DataFrame | pl.LazyFrame,
    goal_ids: list[str],
    as_of: date,
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
) -> dict[str, float]:
    """Every id in `goal_ids`'s running balance at `as_of`, in one pass over `contributions`.

    One `group_by` rather than filtering and summing per id one at a time
    (which would re-filter and re-convert the whole `contributions` frame
    `len(goal_ids)` times) — the same reasoning
    `ledger.replay.account_balances_over_time` gives for not calling
    `account_balances` once per date.

    Parameters
    ----------
    contributions
        As `contributions_to_frame` returns.
    goal_ids
        Every goal to report a balance for.
    as_of
        Last day to include, inclusive.
    display
        The currency (and rate) every contribution's amount is converted into before summing.

    Returns
    -------
    dict[str, float]
        One entry per id in `goal_ids`, `0.0` for a goal with no contributions yet.
    """
    dated = contributions.lazy().filter(pl.col("date").dt.date() <= as_of)
    totals = _with_converted_amount(dated, display).group_by("goal_id").agg(amount=pl.col("amount").sum()).collect()
    total_by_goal = dict(zip(totals["goal_id"].to_list(), totals["amount"].to_list(), strict=True))
    return {goal_id: total_by_goal.get(goal_id, 0.0) for goal_id in goal_ids}


def _opening_balance_total(
    accounts: dict[str, Account],
    opening_balances: dict[str, OpeningBalance],
    as_of: date,
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
) -> float:
    """Every real account's manually-entered starting balance, summed and converted, as of `as_of`.

    Signed and summed exactly the way `dashboard.net_worth.net_worth_summary`
    treats the same rows, rather than inventing a second convention for
    them, which means matching it on all four of its rules: a liability
    account's opening balance is negative and therefore subtracts; an
    opening balance contributes nothing until `as_of` reaches its own
    `as_of_date`; a virtual counterparty account
    (`models.VIRTUAL_ACCOUNT_KINDS`) is excluded, its "balance" never being
    money that is anywhere; and a broker-linked account is excluded too,
    because `net_worth_summary.base_balance` takes that account's whole
    value from `external_investment_value` and never adds its opening
    balance to it. Counting one here that net worth does not count would
    put money into unallocated that appears in no other total.

    Converted at the `as_of` rate, again like net worth: this is a stock
    held on one date, not a flow that happened across many, so it has no
    per-posting dates to convert at.

    An opening balance keyed to an account that no longer exists is
    skipped. The row is deleted with its account (`opening_balances`
    cascades on `account_id`), so this only ever sees one mid-request.

    Parameters
    ----------
    accounts
        Every known account, keyed by `account_id`.
    opening_balances
        Manually-entered starting balances, keyed by `account_id` (see
        `repositories.accounts.load_opening_balances`).
    as_of
        Last day to include, inclusive.
    display
        The currency (and rate) every balance is converted into before summing.

    Returns
    -------
    float
    """
    total = 0.0
    for account_id, opening in opening_balances.items():
        account = accounts.get(account_id)
        if account is None or account.kind in VIRTUAL_ACCOUNT_KINDS or account.broker_connection_id is not None:
            continue
        if as_of < opening.as_of_date.date():
            continue
        total += convert(to_analytics_float(opening.amount), account.currency, display.code, display.rates_to_base)
    return total


def unallocated_balance(
    postings: pl.DataFrame | pl.LazyFrame,
    accounts: dict[str, Account],
    contributions: pl.DataFrame | pl.LazyFrame,
    as_of: date,
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
    opening_balances: dict[str, OpeningBalance] | None = None,
) -> float:
    """Money that's neither gone toward a real expense nor been earmarked into any goal yet, as of `as_of`.

    `(every real account's opening balance)` plus
    `(real income - real expense, cumulative through as_of)` minus
    `(every goal's contributions, cumulative through as_of)`.

    The first term is why this is not simply the income statement's net
    total. A posting only ever records money moving *through* the ledger
    (see `models.OpeningBalance`), so someone who started tracking
    mid-life, with money already sitting in an account, has all of it
    invisible to the flow terms — the app would offer them nothing to
    allocate until their next payday. Opening balances are not postings and
    never become any: they are their own table, added on top here and in
    `dashboard.net_worth` alone, so counting them cannot double-count
    against the legs summed below.

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
    display
        The currency (and rate) every posting/contribution/opening balance
        is converted into before summing.
    opening_balances
        Manually-entered starting balances, keyed by `account_id`; see
        `_opening_balance_total`. `None` means the caller has none to
        offer, not that they should be ignored — every caller in
        `api.routers.goals` passes them.

    Returns
    -------
    float
    """
    net_income = net_income_expense_total(postings, accounts, as_of, display)
    opening = _opening_balance_total(accounts, opening_balances or {}, as_of, display)
    dated = contributions.lazy().filter(pl.col("date").dt.date() <= as_of)
    contributed = _with_converted_amount(dated, display).select(pl.col("amount").sum().fill_null(0.0))
    total_contributed = contributed.collect().item()
    return opening + net_income - float(total_contributed)
