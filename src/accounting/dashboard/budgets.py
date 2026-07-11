"""Monthly budgets: one spending target per expense category, compared against actual spend.

A `Budget` carries no bookkeeping on `Posting` itself — a posting's own
`category_id` already determines which budget it counts against for
whichever month it landed in, so "actual" is always `category_totals`
filtered to one month, never a separately-maintained running total that
could drift out of sync with the ledger.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

import polars as pl

from accounting.dashboard.income_statement import category_totals, real_income_expense_legs
from accounting.ledger.currency import DisplayCurrency

if TYPE_CHECKING:
    from accounting.models import Account, Budget, Category


@dataclass(frozen=True)
class BudgetComparisonRow:
    """One budgeted category's (or subcategory's) target next to what was actually spent that month."""

    category_id: str
    category_name: str
    subcategory_id: str | None
    subcategory_name: str | None
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
    postings: pl.DataFrame | pl.LazyFrame,
    accounts: dict[str, Account],
    category_id: str,
    month: str,
    lookback_months: int = 3,
    subcategory_id: str | None = None,
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
) -> float:
    """Median actual spend in a category (or one subcategory of it) over the months just before `month`.

    Median rather than mean (porting Maybe's `median_monthly_expense`) — a
    single unusually large month (a one-off purchase) shouldn't drag the
    suggestion up the way an average would. Filters `postings` directly to
    this one category (rather than calling `category_totals`, which builds
    a full classification/color breakdown across every category) and
    groups by month in a single pass over the whole lookback span, instead
    of one full re-scan of `postings` per lookback month.

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
    subcategory_id
        When set, scopes the suggestion to this one subcategory's actual
        spend rather than the whole category's.
    display
        The currency (and rate) every posting's amount is converted into before summing.

    Returns
    -------
    float
        The median of the trailing months' actual spend, or 0.0 if there's no history.
    """
    cursor = month
    months: list[str] = []
    for _ in range(lookback_months):
        cursor = _previous_month(cursor)
        months.append(cursor)
    if not months:
        return 0.0

    bounds = [month_bounds(one_month) for one_month in months]
    earliest_start = min(start for start, _ in bounds)
    latest_end = max(end for _, end in bounds)

    legs = real_income_expense_legs(postings, accounts, display).filter(
        (pl.col("posted_at").dt.date() >= earliest_start)
        & (pl.col("posted_at").dt.date() <= latest_end)
        & (pl.col("category_id") == category_id)
    )
    if subcategory_id is not None:
        legs = legs.filter(pl.col("subcategory_id") == subcategory_id)

    monthly = (
        legs
        .with_columns(month=pl.col("posted_at").dt.strftime("%Y-%m"))
        .group_by("month")
        .agg(amount=pl.col("amount").abs().sum())
        .collect()
    )
    total_by_month = dict(zip(monthly["month"].to_list(), monthly["amount"].to_list(), strict=True))
    totals = [total_by_month.get(one_month, 0.0) for one_month in months]

    return statistics.median(totals)


def budget_comparison(
    postings: pl.DataFrame | pl.LazyFrame,
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
    # `actual` matches `postings`'s own type (see `category_totals`) — row
    # iteration below is a hard boundary that needs it materialized regardless.
    actual = actual.collect() if isinstance(actual, pl.LazyFrame) else actual
    actual_by_subcategory: dict[tuple[str, str | None], float] = {}
    actual_by_category: dict[str, float] = {}
    for row in actual.iter_rows(named=True):
        key = (row["category_id"], row["subcategory_id"])
        actual_by_subcategory[key] = actual_by_subcategory.get(key, 0.0) + row["amount"]
        actual_by_category[row["category_id"]] = actual_by_category.get(row["category_id"], 0.0) + row["amount"]

    rows = []
    for budget in budgets:
        if budget.month != month:
            continue
        category = categories.get(budget.category_id)
        subcategory = categories.get(budget.subcategory_id) if budget.subcategory_id else None
        actual_amount = (
            actual_by_subcategory.get((budget.category_id, budget.subcategory_id), 0.0)
            if budget.subcategory_id
            else actual_by_category.get(budget.category_id, 0.0)
        )
        rows.append(
            BudgetComparisonRow(
                category_id=budget.category_id,
                category_name=category.name if category else budget.category_id,
                subcategory_id=budget.subcategory_id,
                subcategory_name=subcategory.name if subcategory else None,
                color=category.color if category else "#9ca3af",
                budgeted=budget.amount,
                actual=actual_amount,
                currency=display.code,
            )
        )
    return sorted(rows, key=lambda row: (row.category_name, row.subcategory_name or ""))
