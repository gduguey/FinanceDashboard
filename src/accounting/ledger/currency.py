"""Convert an amount between any two supported currencies, via one shared base-currency rate table.

Adding a new `CurrencyCode` (see `accounting.models`) never touches this
file — `convert` only ever needs a `rates_to_base` entry for whichever
codes it's asked to convert between, so nothing here hardcodes which
currencies exist.

Two shapes of conversion live here, and which one a caller wants follows
from what it is converting. A *stock* — a balance, an asset, a net worth —
is held on one date and converts at that date's rate: `convert`, against
`DisplayCurrency.rates_to_base`. A *flow* — a posting, a goal
contribution — happened on its own date and converts at that date's rate,
however long ago that was: `with_converted_amount`, against
`DisplayCurrency.rates_by_date`. Converting a flow at the report date's
rate makes last March's spending move every time the currency market does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import polars as pl

from accounting.models import BASE_CURRENCY

if TYPE_CHECKING:
    from datetime import date

    from accounting.models import CurrencyCode


@dataclass(frozen=True)
class DisplayCurrency:
    """A currency to aggregate into, plus the rate tables needed to get there.

    Bundles the values every aggregation function
    (`dashboard.net_worth`, `dashboard.income_statement`) always needs
    together — a display currency is meaningless to convert into without
    rates for the currencies actually involved, so callers never pass one
    without the other. `rates_to_base` maps every relevant `CurrencyCode`
    to how many `BASE_CURRENCY` units one unit of it is worth
    (`BASE_CURRENCY` itself always maps to `1.0`) — see
    `market_data.exchange_rates.current_rates_to_base`.

    `rates_by_date` is the same thing per day, already divided into `code`:
    columns `currency`, `rate_date`, `rate_into_display`, one row per
    (currency, calendar day), from
    `market_data.exchange_rates.smoothed_rate_series`. It is what
    `with_converted_amount` needs and what only the flow aggregations
    build; `None` means every conversion falls back to `rates_to_base`,
    which is exactly right when the base currency is the only one in play
    and every rate is `1.0`. Excluded from equality because a `DataFrame`
    has no scalar `==`, and two display currencies are the same one when
    their code and rates are.
    """

    code: CurrencyCode = BASE_CURRENCY
    rates_to_base: dict[CurrencyCode, float] = field(default_factory=lambda: {BASE_CURRENCY: 1.0})
    rates_by_date: pl.DataFrame | None = field(default=None, compare=False)


def with_converted_amount(
    frame: pl.LazyFrame, display: DisplayCurrency, currency_column: str, date_column: str
) -> pl.LazyFrame:
    """Replace `amount` with its `display.code` value, converting each row at its own date's rate.

    The one conversion every flow aggregation shares —
    `dashboard.income_statement.real_income_expense_legs` over postings
    (`account_currency`, `posted_at`) and `dashboard.goals` over
    contributions (`currency`, `date`) — so that "which rate applies to a
    row" is answered in one place rather than once per rate-table join.

    A row dated outside the cached history is converted at the nearest end
    of it: the far side is clamped here, the near side by
    `smoothed_rate_series`' own backward fill. The alternative is a null
    rate, which a left join turns into a null amount and every `sum`
    then silently skips — a wrong total that looks like a right one.

    That reasoning covers the *date* axis only. The currency axis carries
    the same exposure and is a caller obligation rather than something
    enforced here: a row whose currency is in neither rate table joins to
    nothing and lands in exactly that null-amount state. Every API caller
    builds its table from `api.dependencies._currencies_in_use`, which is
    derived from the same accounts, assets and contributions being
    converted, so the set is complete by construction. Making it loud
    instead of implicit needs a `collect()` and would cost the laziness
    every caller composes on — tracked as A7 in `docs/remaining-work.md`.

    With no `rates_by_date` the whole frame converts at `rates_to_base`,
    the pre-per-date behaviour, which is what a direct caller
    constructing a bare `DisplayCurrency` gets.

    Parameters
    ----------
    frame
        Any lazy frame with an `amount` column, a currency column, and a date column.
    display
        The currency (and rates) every row's amount is converted into.
    currency_column
        Which column names each row's own currency.
    date_column
        Which column dates each row. A `Datetime` or a `Date` both work.

    Returns
    -------
    polars.LazyFrame
        `frame` with `amount` converted, and no extra columns.
    """
    if display.rates_by_date is None:
        rate_table = pl.LazyFrame(
            {currency_column: list(display.rates_to_base.keys()), "rate": list(display.rates_to_base.values())},
            schema={currency_column: pl.Utf8, "rate": pl.Float64},
        ).with_columns(rate_into_display=pl.col("rate") / display.rates_to_base[display.code])
        return (
            frame
            .join(rate_table.drop("rate"), on=currency_column, how="left")
            .with_columns(amount=pl.col("amount") * pl.col("rate_into_display"))
            .drop("rate_into_display")
        )

    first = cast("date", display.rates_by_date["rate_date"].min())
    last = cast("date", display.rates_by_date["rate_date"].max())
    dated_rates = display.rates_by_date.lazy().rename({"currency": currency_column})
    return (
        frame
        .with_columns(rate_date=pl.col(date_column).dt.date().clip(first, last))
        .join(dated_rates, on=[currency_column, "rate_date"], how="left")
        .with_columns(amount=pl.col("amount") * pl.col("rate_into_display"))
        .drop("rate_date", "rate_into_display")
    )


def rates_into_display(series: pl.DataFrame, code: CurrencyCode) -> pl.DataFrame:
    """Turn a per-date `rate_to_base` series into the per-date `rate_into_display` table `DisplayCurrency` holds.

    The divisor is per-date too, and that is the point: dividing by
    *today's* rate for the display currency would leave a EUR amount shown
    in EUR drifting with the market instead of being itself.

    Parameters
    ----------
    series
        As `market_data.exchange_rates.smoothed_rate_series` returns:
        columns `date`, `currency`, `rate_to_base`, including `code` itself.
    code
        The display currency every rate is divided into.

    Returns
    -------
    polars.DataFrame
        Columns `currency`, `rate_date`, `rate_into_display`. Empty if `series` is.
    """
    if series.is_empty():
        return pl.DataFrame(schema={"currency": pl.Utf8, "rate_date": pl.Date, "rate_into_display": pl.Float64})
    display_rates = series.filter(pl.col("currency") == code).select("date", display_rate=pl.col("rate_to_base"))
    return series.join(display_rates, on="date", how="inner").select(
        "currency",
        rate_date=pl.col("date"),
        rate_into_display=pl.col("rate_to_base") / pl.col("display_rate"),
    )


def convert(
    amount: float, from_currency: CurrencyCode, to_currency: CurrencyCode, rates_to_base: dict[CurrencyCode, float]
) -> float:
    """Convert a signed amount from one supported currency to another, through the shared base currency.

    Parameters
    ----------
    amount
        The amount, in `from_currency`.
    from_currency
        The currency `amount` is denominated in.
    to_currency
        The currency to convert into.
    rates_to_base
        Every relevant currency's rate into `accounting.models.BASE_CURRENCY`
        (which itself must map to `1.0`) — see `DisplayCurrency`. A missing
        entry for `from_currency`/`to_currency` raises `KeyError`.

    Returns
    -------
    float
        `amount`, converted into `to_currency`.
    """
    if from_currency == to_currency:
        return amount
    amount_in_base = amount * rates_to_base[from_currency]
    return amount_in_base / rates_to_base[to_currency]
