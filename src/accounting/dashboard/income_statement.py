"""Income and expense aggregation: category/subcategory totals, monthly income vs. expense, and a spend curve.

A posting only ever represents real income or a real expense — as opposed
to an internal transfer between two accounts you hold — when its sibling
leg (same `transaction_id`) is against a virtual `income_source`/
`expense_payee` counterparty (see `accounting.models.AccountKind`); every
function here filters to that set first, the same test
`dashboard.net_worth` uses to exclude those virtual accounts from balances,
applied here to exclude transfers from income/expense instead.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

from accounting.ledger.currency import DisplayCurrency

if TYPE_CHECKING:
    from datetime import date

    from accounting.models import Account, Category

_VIRTUAL_KINDS = ["income_source", "expense_payee"]
UNCATEGORIZED_INCOME_ID = "uncategorized:income-category"
UNCATEGORIZED_EXPENSE_ID = "uncategorized:expense-category"


def _real_income_expense_legs(
    postings: pl.DataFrame, accounts: dict[str, Account], display: DisplayCurrency
) -> pl.DataFrame:
    """Filter to postings that are real income/expense, with `amount` converted into `display.code`.

    Every posting keeps its own account's native currency at rest (see
    `dashboard.net_worth`'s docstring) — this only converts the copy used
    for aggregation here, the same way `net_worth_summary` converts
    balances rather than storing pre-converted ones.

    Returns
    -------
    polars.DataFrame
        The subset of `postings` on a real account whose transaction has
        at least one virtual-counterparty leg, with `amount` replaced by
        its `display.code`-converted value.
    """
    virtual_ids = [account.account_id for account in accounts.values() if account.kind in _VIRTUAL_KINDS]
    sibling_flags = (
        postings
        .select("transaction_id", "account_id")
        .with_columns(is_virtual=pl.col("account_id").is_in(virtual_ids))
        .group_by("transaction_id")
        .agg(any_virtual_sibling=pl.col("is_virtual").any())
    )
    real_currencies = pl.DataFrame(
        {
            "account_id": list(accounts.keys()),
            "account_currency": [account.currency for account in accounts.values()],
        },
        schema={"account_id": pl.Utf8, "account_currency": pl.Utf8},
    )
    legs = (
        postings
        .join(sibling_flags, on="transaction_id", how="left")
        .filter(pl.col("any_virtual_sibling") & ~pl.col("account_id").is_in(virtual_ids))
        .drop("any_virtual_sibling")
        .join(real_currencies, on="account_id", how="left")
    )
    if display.code == "USD":
        converted = (
            pl
            .when(pl.col("account_currency") == "EUR")
            .then(pl.col("amount") * display.eur_usd_rate)
            .otherwise(pl.col("amount"))
        )
    else:
        converted = (
            pl
            .when(pl.col("account_currency") == "USD")
            .then(pl.col("amount") / display.eur_usd_rate)
            .otherwise(pl.col("amount"))
        )
    return legs.with_columns(amount=converted).drop("account_currency")


def category_totals(
    postings: pl.DataFrame,
    accounts: dict[str, Account],
    categories: dict[str, Category],
    start: date,
    end: date,
    account_ids: list[str] | None = None,
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
) -> pl.DataFrame:
    """Sum real income/expense postings by classification, category, and subcategory.

    An uncategorized posting (no rule or manual edit has classified it yet)
    still needs somewhere to go in a chart — it's bucketed by its own sign
    into a synthetic `"Uncategorized"` category per classification, with
    ids `UNCATEGORIZED_INCOME_ID`/`UNCATEGORIZED_EXPENSE_ID` rather than a
    real `Category` row, since it isn't one the user chose.

    Parameters
    ----------
    postings
        The full, resolved posting ledger.
    accounts
        Every known account, keyed by `account_id`.
    categories
        Every known category, keyed by `category_id`.
    start
        First day to include, inclusive.
    end
        Last day to include, inclusive.
    account_ids
        Only include postings on one of these accounts; `None` means every account.
    display
        The currency (and rate) every posting's amount is converted into before summing.

    Returns
    -------
    polars.DataFrame
        Columns `classification`, `category_id`, `category_name`,
        `subcategory_id`, `subcategory_name`, `color`, `amount` (always
        non-negative — `classification` says which side it's on).
    """
    legs = _real_income_expense_legs(postings, accounts, display).filter(
        (pl.col("posted_at").dt.date() >= start) & (pl.col("posted_at").dt.date() <= end)
    )
    if account_ids is not None:
        legs = legs.filter(pl.col("account_id").is_in(account_ids))
    if legs.is_empty():
        return pl.DataFrame(
            schema={
                "classification": pl.Utf8,
                "category_id": pl.Utf8,
                "category_name": pl.Utf8,
                "subcategory_id": pl.Utf8,
                "subcategory_name": pl.Utf8,
                "color": pl.Utf8,
                "amount": pl.Float64,
            }
        )

    tagged = legs.with_columns(
        top_category_id=pl
        .when(pl.col("category_id").is_not_null())
        .then(pl.col("category_id"))
        .when(pl.col("amount") >= 0)
        .then(pl.lit(UNCATEGORIZED_INCOME_ID))
        .otherwise(pl.lit(UNCATEGORIZED_EXPENSE_ID)),
    )
    rows = []
    for row in (
        tagged
        .group_by("top_category_id", "subcategory_id")
        .agg(amount=pl.col("amount").abs().sum())
        .iter_rows(named=True)
    ):
        top_category_id = row["top_category_id"]
        category = categories.get(top_category_id)
        subcategory = categories.get(row["subcategory_id"]) if row["subcategory_id"] else None
        is_income_sentinel = top_category_id == UNCATEGORIZED_INCOME_ID
        classification = category.classification if category else ("income" if is_income_sentinel else "expense")
        rows.append({
            "classification": classification,
            "category_id": top_category_id,
            "category_name": category.name if category else "Uncategorized",
            "subcategory_id": row["subcategory_id"],
            "subcategory_name": subcategory.name if subcategory else None,
            "color": category.color if category else "#9ca3af",
            "amount": row["amount"],
        })
    return pl.DataFrame(
        rows,
        schema={
            "classification": pl.Utf8,
            "category_id": pl.Utf8,
            "category_name": pl.Utf8,
            "subcategory_id": pl.Utf8,
            "subcategory_name": pl.Utf8,
            "color": pl.Utf8,
            "amount": pl.Float64,
        },
    )


def monthly_income_expense(
    postings: pl.DataFrame,
    accounts: dict[str, Account],
    start: date,
    end: date,
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
) -> pl.DataFrame:
    """Sum real income and real expense per calendar month.

    Parameters
    ----------
    postings
        The full, resolved posting ledger.
    accounts
        Every known account, keyed by `account_id`.
    start
        First day to include, inclusive.
    end
        Last day to include, inclusive.
    display
        The currency (and rate) every posting's amount is converted into before summing.

    Returns
    -------
    polars.DataFrame
        Columns `month` (`"YYYY-MM"`), `income`, `expense` (both non-negative).
    """
    legs = _real_income_expense_legs(postings, accounts, display).filter(
        (pl.col("posted_at").dt.date() >= start) & (pl.col("posted_at").dt.date() <= end)
    )
    schema = {"month": pl.Utf8, "income": pl.Float64, "expense": pl.Float64}
    if legs.is_empty():
        return pl.DataFrame(schema=schema)
    return (
        legs
        .with_columns(month=pl.col("posted_at").dt.strftime("%Y-%m"))
        .group_by("month")
        .agg(
            income=pl.col("amount").filter(pl.col("amount") >= 0).sum(),
            expense=pl.col("amount").filter(pl.col("amount") < 0).abs().sum(),
        )
        .with_columns(pl.col("income", "expense").fill_null(0.0))
        .sort("month")
    )


def spend_curve_vs_average(
    postings: pl.DataFrame,
    accounts: dict[str, Account],
    month: date,
    lookback_months: int = 3,
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
) -> pl.DataFrame:
    """Cumulative daily spend through one month, next to the same day-of-month average over the prior months.

    Parameters
    ----------
    postings
        The full, resolved posting ledger.
    accounts
        Every known account, keyed by `account_id`.
    month
        Any date within the month to chart; only its year/month are used.
    lookback_months
        How many preceding calendar months to average.
    display
        The currency (and rate) every posting's amount is converted into before summing.

    Returns
    -------
    polars.DataFrame
        Columns `day`, `current_month_cumulative`, `average_previous_months_cumulative`.
    """
    legs = _real_income_expense_legs(postings, accounts, display).filter(pl.col("amount") < 0)
    month_start = month.replace(day=1)

    def _cumulative_by_day(period_start: date, period_end: date) -> dict[int, float]:
        period = legs.filter(
            (pl.col("posted_at").dt.date() >= period_start) & (pl.col("posted_at").dt.date() <= period_end)
        )
        if period.is_empty():
            return {}
        daily = (
            period
            .with_columns(day=pl.col("posted_at").dt.day())
            .group_by("day")
            .agg(spent=pl.col("amount").abs().sum())
            .sort("day")
            .with_columns(cumulative=pl.col("spent").cum_sum())
        )
        return dict(zip(daily["day"].to_list(), daily["cumulative"].to_list(), strict=True))

    next_month_start = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    days_in_month = (next_month_start - month_start).days
    current_by_day = _cumulative_by_day(month_start, next_month_start - timedelta(days=1))

    previous_daily: list[dict[int, float]] = []
    cursor_end = month_start - timedelta(days=1)
    for _ in range(lookback_months):
        cursor_start = cursor_end.replace(day=1)
        previous_daily.append(_cumulative_by_day(cursor_start, cursor_end))
        cursor_end = cursor_start - timedelta(days=1)

    rows = []
    running_current = 0.0
    running_previous_last = [0.0] * len(previous_daily)
    for day in range(1, days_in_month + 1):
        if day in current_by_day:
            running_current = current_by_day[day]
        averages = []
        for index, month_data in enumerate(previous_daily):
            if day in month_data:
                running_previous_last[index] = month_data[day]
            averages.append(running_previous_last[index])
        rows.append({
            "day": day,
            "current_month_cumulative": running_current,
            "average_previous_months_cumulative": (sum(averages) / len(averages)) if averages else 0.0,
        })
    return pl.DataFrame(
        rows,
        schema={
            "day": pl.Int64,
            "current_month_cumulative": pl.Float64,
            "average_previous_months_cumulative": pl.Float64,
        },
    )
