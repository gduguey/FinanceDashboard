"""Monthly budgets: one spending target per expense category, compared against actual spend.

A `Budget` carries no bookkeeping on `Posting` itself — a posting's own
`category_id` already determines which budget it counts against for
whichever month it landed in, so "actual" is always `category_totals`
filtered to one month, never a separately-maintained running total that
could drift out of sync with the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

import polars as pl

from accounting.dashboard.income_statement import category_totals
from accounting.ledger.currency import DisplayCurrency

if TYPE_CHECKING:
    from accounting.models import Account, Budget, Category


@dataclass(frozen=True)
class BudgetComparisonRow:
    """One budgeted category's target next to what was actually spent that month."""

    category_id: str
    category_name: str
    color: str
    budgeted: float
    actual: float
    currency: str


def month_bounds(month: str) -> tuple[date, date]:
    """First and last calendar day of a `"YYYY-MM"` month, inclusive.

    Parameters
    ----------
    month
        A month in `"YYYY-MM"` form.

    Returns
    -------
    tuple[datetime.date, datetime.date]
        `(start, end)`, both inclusive.
    """
    year, mon = (int(part) for part in month.split("-"))
    start = date(year, mon, 1)
    next_month_start = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, next_month_start - timedelta(days=1)


def _previous_month(month: str) -> str:
    start, _ = month_bounds(month)
    previous = start - timedelta(days=1)
    return previous.strftime("%Y-%m")


def suggested_budget_amount(
    postings: pl.DataFrame,
    accounts: dict[str, Account],
    category_id: str,
    month: str,
    lookback_months: int = 3,
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
) -> float:
    """Median actual spend in a category over the months just before `month`, as a starting suggestion.

    Median rather than mean (porting Maybe's `median_monthly_expense`) — a
    single unusually large month (a one-off purchase) shouldn't drag the
    suggestion up the way an average would.

    Parameters
    ----------
    postings
        The full, resolved posting ledger.
    accounts
        Every known account, keyed by `account_id`.
    category_id
        The category to suggest a budget for.
    month
        The month (`"YYYY-MM"`) being budgeted; only months before it are averaged.
    lookback_months
        How many preceding calendar months to look at.
    display
        The currency (and rate) every posting's amount is converted into before summing.

    Returns
    -------
    float
        The median of the trailing months' actual spend, or 0.0 if there's no history.
    """
    cursor = month
    totals: list[float] = []
    for _ in range(lookback_months):
        cursor = _previous_month(cursor)
        start, end = month_bounds(cursor)
        rows = category_totals(postings, accounts, {}, start, end, display=display)
        matching = rows.filter(pl.col("category_id") == category_id)
        totals.append(float(matching["amount"].sum()) if not matching.is_empty() else 0.0)
    if not totals:
        return 0.0
    ordered = sorted(totals)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 0:
        return (ordered[mid - 1] + ordered[mid]) / 2
    return ordered[mid]


def budget_comparison(
    postings: pl.DataFrame,
    accounts: dict[str, Account],
    categories: dict[str, Category],
    budgets: list[Budget],
    month: str,
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
) -> list[BudgetComparisonRow]:
    """Every category budgeted for `month`, actual spend next to the target.

    Only categories with an actual `Budget` row for this month are
    included — an unbudgeted category has nothing to compare against, and
    showing it at a $0 target would read as "budgeted to zero" rather than
    "not tracked here".

    Parameters
    ----------
    postings
        The full, resolved posting ledger.
    accounts
        Every known account, keyed by `account_id`.
    categories
        Every known category, keyed by `category_id`.
    budgets
        Every persisted budget row, across all months.
    month
        The month (`"YYYY-MM"`) to compare.
    display
        The currency (and rate) every amount is converted into.

    Returns
    -------
    list[BudgetComparisonRow]
        One row per category budgeted for `month`, sorted by name.
    """
    start, end = month_bounds(month)
    actual = category_totals(postings, accounts, categories, start, end, display=display)
    actual_by_category = (
        dict(zip(actual["category_id"].to_list(), actual["amount"].to_list(), strict=True))
        if not actual.is_empty()
        else {}
    )
    rows = []
    for budget in budgets:
        if budget.month != month:
            continue
        category = categories.get(budget.category_id)
        rows.append(
            BudgetComparisonRow(
                category_id=budget.category_id,
                category_name=category.name if category else budget.category_id,
                color=category.color if category else "#9ca3af",
                budgeted=budget.amount,
                actual=actual_by_category.get(budget.category_id, 0.0),
                currency=display.code,
            )
        )
    return sorted(rows, key=lambda row: row.category_name)
