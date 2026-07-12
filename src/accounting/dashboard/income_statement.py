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

from dataclasses import dataclass
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


@dataclass(frozen=True)
class Scope:
    """Which postings a breakdown should include — bundled so `category_totals` stays within one param budget.

    `None` on either field means "don't restrict by this" — the same
    all-inclusive default as passing nothing.
    """

    account_ids: list[str] | None = None
    tag_id: str | None = None


def real_income_expense_legs(
    postings: pl.DataFrame | pl.LazyFrame, accounts: dict[str, Account], display: DisplayCurrency
) -> pl.LazyFrame:
    """Filter to postings that are real income/expense, with `amount` converted into `display.code`.

    Every posting keeps its own account's native currency at rest (see
    `dashboard.net_worth`'s docstring) — this only converts the copy used
    for aggregation here, the same way `net_worth_summary` converts
    balances rather than storing pre-converted ones. Always returns a
    `LazyFrame` regardless of `postings`'s own type — every caller is
    itself a public function that continues the lazy chain and only
    collects at its own final boundary (see e.g. `category_totals`).

    Returns
    -------
    polars.LazyFrame
        The subset of `postings` on a real account whose transaction has
        at least one virtual-counterparty leg, with `amount` replaced by
        its `display.code`-converted value.
    """
    lazy = postings.lazy()
    virtual_ids = [account.account_id for account in accounts.values() if account.kind in _VIRTUAL_KINDS]
    sibling_flags = (
        lazy
        .select("transaction_id", "account_id")
        .with_columns(is_virtual=pl.col("account_id").is_in(virtual_ids))
        .group_by("transaction_id")
        .agg(any_virtual_sibling=pl.col("is_virtual").any())
    )
    real_currencies = pl.LazyFrame(
        {
            "account_id": list(accounts.keys()),
            "account_currency": [account.currency for account in accounts.values()],
        },
        schema={"account_id": pl.Utf8, "account_currency": pl.Utf8},
    )
    rate_table = pl.LazyFrame(
        {"account_currency": list(display.rates_to_base.keys()), "rate_to_base": list(display.rates_to_base.values())},
        schema={"account_currency": pl.Utf8, "rate_to_base": pl.Float64},
    )
    legs = (
        lazy
        .join(sibling_flags, on="transaction_id", how="left")
        .filter(pl.col("any_virtual_sibling") & ~pl.col("account_id").is_in(virtual_ids))
        .drop("any_virtual_sibling")
        .join(real_currencies, on="account_id", how="left")
        .join(rate_table, on="account_currency", how="left")
    )
    converted = pl.col("amount") * pl.col("rate_to_base") / display.rates_to_base[display.code]
    return legs.with_columns(amount=converted).drop("account_currency", "rate_to_base")


def category_totals(
    postings: pl.DataFrame | pl.LazyFrame,
    accounts: dict[str, Account],
    categories: dict[str, Category],
    start: date,
    end: date,
    scope: Scope = Scope(),  # noqa: B008
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
) -> pl.DataFrame | pl.LazyFrame:
    """Sum real income/expense postings by classification, category, and subcategory.

    An uncategorized posting (no rule or manual edit has classified it yet)
    still needs somewhere to go in a chart — it's bucketed by its own sign
    into a synthetic `"Uncategorized"` category per classification, with
    ids `UNCATEGORIZED_INCOME_ID`/`UNCATEGORIZED_EXPENSE_ID` rather than a
    real `Category` row, since it isn't one the user chose. `categories` is
    turned into two small lookup frames (by top-level id, by subcategory
    id) and joined rather than walked row-by-row in Python — the join's
    left side stays lazy end to end, and naturally yields zero rows with
    the right dtypes when there's nothing to total, so no separate
    empty-input branch is needed either.

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
    scope
        Which accounts/tag to restrict to; see `Scope`.
    display
        The currency (and rate) every posting's amount is converted into before summing.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        Same type as `postings`. Columns `classification`, `category_id`,
        `category_name`, `subcategory_id`, `subcategory_name`, `color` (the
        subcategory's own color if there is one, else the top-level
        category's), `category_color` (always the top-level category's
        own), `amount` (always non-negative — `classification` says which
        side it's on).
    """
    was_eager = isinstance(postings, pl.DataFrame)
    legs = real_income_expense_legs(postings, accounts, display).filter(
        (pl.col("posted_at").dt.date() >= start) & (pl.col("posted_at").dt.date() <= end)
    )
    if scope.account_ids is not None:
        legs = legs.filter(pl.col("account_id").is_in(scope.account_ids))
    if scope.tag_id is not None:
        legs = legs.filter(pl.col("tag_ids").list.contains(scope.tag_id))

    tagged = legs.with_columns(
        top_category_id=pl
        .when(pl.col("category_id").is_not_null())
        .then(pl.col("category_id"))
        .when(pl.col("amount") >= 0)
        .then(pl.lit(UNCATEGORIZED_INCOME_ID))
        .otherwise(pl.lit(UNCATEGORIZED_EXPENSE_ID)),
    )
    totals = tagged.group_by("top_category_id", "subcategory_id").agg(amount=pl.col("amount").abs().sum())

    category_lookup = pl.LazyFrame(
        {
            "category_id": [category.category_id for category in categories.values()],
            "name": [category.name for category in categories.values()],
            "classification": [category.classification for category in categories.values()],
            "color": [category.color for category in categories.values()],
        },
        schema={"category_id": pl.Utf8, "name": pl.Utf8, "classification": pl.Utf8, "color": pl.Utf8},
    )
    top_lookup = category_lookup.rename({
        "category_id": "top_category_id",
        "name": "category_name",
        "color": "category_color",
    })
    subcategory_lookup = category_lookup.select(
        pl.col("category_id").alias("subcategory_id"), pl.col("name").alias("subcategory_name"), pl.col("color")
    )

    result = (
        totals
        .join(top_lookup, on="top_category_id", how="left")
        .join(subcategory_lookup, on="subcategory_id", how="left")
        .with_columns(
            # The subcategory's own color when there is one (each
            # subcategory gets a color distinct from its parent and
            # siblings — see `store.next_available_color`) — falls back to
            # the top-level category's color for a row with no
            # subcategory. `category_color` is always the top-level
            # category's own, regardless, for a drilldown that aggregates
            # several subcategories back into one top-level slice and
            # needs one stable color for it.
            classification=pl.col("classification").fill_null(
                pl
                .when(pl.col("top_category_id") == UNCATEGORIZED_INCOME_ID)
                .then(pl.lit("income"))
                .otherwise(pl.lit("expense"))
            ),
            category_name=pl.col("category_name").fill_null("Uncategorized"),
            category_color=pl.col("category_color").fill_null("#9ca3af"),
        )
        .with_columns(color=pl.col("color").fill_null(pl.col("category_color")))
        .rename({"top_category_id": "category_id"})
        .select(
            "classification",
            "category_id",
            "category_name",
            "subcategory_id",
            "subcategory_name",
            "color",
            "category_color",
            "amount",
        )
    )
    return result.collect() if was_eager else result


def monthly_income_expense(
    postings: pl.DataFrame | pl.LazyFrame,
    accounts: dict[str, Account],
    start: date,
    end: date,
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
) -> pl.DataFrame | pl.LazyFrame:
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
    polars.DataFrame or polars.LazyFrame
        Same type as `postings`. Columns `month` (`"YYYY-MM"`), `income`, `expense` (both non-negative).
    """
    was_eager = isinstance(postings, pl.DataFrame)
    legs = real_income_expense_legs(postings, accounts, display).filter(
        (pl.col("posted_at").dt.date() >= start) & (pl.col("posted_at").dt.date() <= end)
    )
    result = (
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
    return result.collect() if was_eager else result


def net_income_expense_total(
    postings: pl.DataFrame | pl.LazyFrame,
    accounts: dict[str, Account],
    as_of: date,
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
) -> float:
    """Net real income minus real expense, cumulative through `as_of` — never bucketed by month.

    The building block `dashboard.goals.unallocated_balance` uses: money
    that's neither gone toward a real expense nor been earmarked into a
    goal yet. Excludes transfers between two real accounts the same way
    every other function here does (see module docstring).

    Parameters
    ----------
    postings
        The full, resolved posting ledger.
    accounts
        Every known account, keyed by `account_id`.
    as_of
        Last day to include, inclusive.
    display
        The currency (and rate) every posting's amount is converted into before summing.

    Returns
    -------
    float
        Positive if cumulative income exceeds cumulative expense.
    """
    legs = real_income_expense_legs(postings, accounts, display).filter(pl.col("posted_at").dt.date() <= as_of)
    total = legs.select(pl.col("amount").sum().fill_null(0.0)).collect().item()
    return float(total)


def spend_curve_vs_average(
    postings: pl.DataFrame | pl.LazyFrame,
    accounts: dict[str, Account],
    month: date,
    lookback_months: int = 3,
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
) -> pl.DataFrame | pl.LazyFrame:
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
    polars.DataFrame or polars.LazyFrame
        Same type as `postings`. Columns `day`, `current_month_cumulative`,
        `average_previous_months_cumulative`. The running day-of-month
        average is a genuine sequential carry-forward (like
        `trades.dashboard.cash_sitting.daily_cash_balances`'s own
        event-replay walk) and collects internally regardless of
        `postings`'s type — only the final result's type is chosen to match it.
    """
    was_eager = isinstance(postings, pl.DataFrame)
    legs = real_income_expense_legs(postings, accounts, display).filter(pl.col("amount") < 0)
    month_start = month.replace(day=1)

    def _cumulative_by_day(period_start: date, period_end: date) -> dict[int, float]:
        """Sum this period's spend day by day, keyed by day-of-month, running-total-so-far.

        Returns
        -------
        dict[int, float]
        """
        daily = (
            legs
            .filter((pl.col("posted_at").dt.date() >= period_start) & (pl.col("posted_at").dt.date() <= period_end))
            .with_columns(day=pl.col("posted_at").dt.day())
            .group_by("day")
            .agg(spent=pl.col("amount").abs().sum())
            .sort("day")
            .with_columns(cumulative=pl.col("spent").cum_sum())
            .collect()
        )
        if daily.is_empty():
            return {}
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
    result = pl.DataFrame(
        rows,
        schema={
            "day": pl.Int64,
            "current_month_cumulative": pl.Float64,
            "average_previous_months_cumulative": pl.Float64,
        },
    )
    return result if was_eager else result.lazy()
