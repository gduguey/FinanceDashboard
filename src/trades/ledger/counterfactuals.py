"""The counterfactual engine.

`hysa_counterfactual_value` and `benchmark_counterfactual_series` each
value a synthetic position bought with the real ledger's external flows —
"what if every deposit/withdrawal had instead gone into a HYSA/benchmark
instead."
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

from trades.utils.frames import collect_if_lazy

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date


_EMPTY_SERIES_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {"date": pl.Date, "value": pl.Float64}


def hysa_counterfactual_series(
    cashflows: pl.DataFrame | pl.LazyFrame,
    end: date,
    rate_lookup: Callable[[date], float],
    days_per_year: int,
) -> pl.DataFrame | pl.LazyFrame:
    """Compound a virtual high-yield-savings balance daily, returning the balance for every day along the way.

    Simulates "what if every deposit/withdrawal had instead gone into a
    high-yield savings account (HYSA)": each `DEPOSIT` grows the virtual
    balance, each `WITHDRAWAL` shrinks it, and the balance compounds daily
    in between at `rate_lookup`'s annual rate, converted to a daily rate
    by dividing by `days_per_year`. This is a genuinely sequential
    day-by-day walk (like `replay.replay_ledger`'s loop) since each day's
    balance depends on the previous day's, not a vectorized expression.
    `hysa_counterfactual_value` is a thin wrapper around this that keeps
    only the last day — computing a whole chart series by calling that
    once per day would redo this walk from scratch every time.

    Parameters
    ----------
    cashflows
        External cashflows, as returned by `replay.external_cashflows`:
        columns `event_datetime`, `amount` (`DEPOSIT` negative,
        `WITHDRAWAL` positive).
    end
        The last date to compute the balance for. The walk starts at the
        earliest cashflow date; no compounding is applied on `end` itself,
        only on days strictly before it.
    rate_lookup
        Looks up the annual HYSA rate as of a given date; a real rate
        series can be substituted for a constant one without changing
        this function's contract (see `market_data.hysa_rates`).
    days_per_year
        Day-count basis for converting the annual rate to a daily one
        (see `config.ReturnsConfig.days_per_year`).

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        Columns `date`, `value` — the virtual HYSA balance for every day
        from the earliest cashflow through `end`. Empty if `cashflows` is empty.
        Same type as input.
    """
    was_eager = isinstance(cashflows, pl.DataFrame)
    flows = collect_if_lazy(cashflows)
    if flows.is_empty():
        empty = pl.DataFrame(schema=_EMPTY_SERIES_SCHEMA)
        return empty if was_eager else empty.lazy()

    daily_flows = (
        flows
        .with_columns(flow_date=pl.col("event_datetime").dt.date())
        .group_by("flow_date")
        .agg(amount=pl.col("amount").sum())
    )
    flows_by_date: dict[date, float] = dict(
        zip(daily_flows["flow_date"].to_list(), daily_flows["amount"].to_list(), strict=True)
    )

    balance = 0.0
    dates: list[date] = []
    balances: list[float] = []
    current_date = min(flows_by_date)
    while current_date <= end:
        balance -= flows_by_date.get(current_date, 0.0)
        dates.append(current_date)
        balances.append(balance)
        if current_date < end:
            balance *= 1 + rate_lookup(current_date) / days_per_year
        current_date += timedelta(days=1)
    if not dates:
        empty = pl.DataFrame(schema=_EMPTY_SERIES_SCHEMA)
        return empty if was_eager else empty.lazy()
    result = pl.DataFrame({"date": dates, "value": balances})
    return result if was_eager else result.lazy()


def hysa_counterfactual_value(
    cashflows: pl.DataFrame | pl.LazyFrame,
    as_of: date,
    rate_lookup: Callable[[date], float],
    days_per_year: int,
) -> float:
    """Compound a virtual high-yield-savings balance daily against a set of external cashflows.

    Parameters
    ----------
    cashflows
        External cashflows, as returned by `replay.external_cashflows`:
        columns `event_datetime`, `amount` (`DEPOSIT` negative,
        `WITHDRAWAL` positive).
    as_of
        The date to value the virtual balance as of.
    rate_lookup
        Looks up the annual HYSA rate as of a given date; a real rate
        series can be substituted for a constant one without changing
        this function's contract (see `market_data.hysa_rates`).
    days_per_year
        Day-count basis for converting the annual rate to a daily one
        (see `config.ReturnsConfig.days_per_year`).

    Returns
    -------
    float
        The virtual HYSA balance as of `as_of`.
    """
    series = collect_if_lazy(hysa_counterfactual_series(cashflows, as_of, rate_lookup, days_per_year))
    if series.is_empty():
        return 0.0
    return float(series["value"][-1])


def benchmark_counterfactual_series(
    cashflows: pl.DataFrame | pl.LazyFrame,
    end: date,
    price_lookup: Callable[[date], float | None],
) -> pl.DataFrame | pl.LazyFrame:
    """Buy a benchmark with a set of external cashflows, valuing the position for every day along the way.

    Simulates "what if every deposit/withdrawal had instead bought this
    benchmark on that date": each `DEPOSIT` buys shares at that day's
    price, each `WITHDRAWAL` sells shares at that day's price. `price_lookup`
    must be the dividend-reinvested (adjusted, total-return) series (see
    `market_data`'s price rules) — this one function is reused for every
    price-series benchmark (VOO, VT, ...), only `price_lookup` differs.

    Parameters
    ----------
    cashflows
        External cashflows, as returned by `replay.external_cashflows`:
        columns `event_datetime`, `amount` (`DEPOSIT` negative,
        `WITHDRAWAL` positive).
    end
        The last date to value the position for. The walk starts at the earliest cashflow date.
    price_lookup
        Looks up the benchmark's adjusted price as of a given date;
        returns None if unavailable.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        Columns `date`, `value` (`shares_held x price(date)`) for every
        day from the earliest cashflow through `end`. Empty if `cashflows` is empty.
        Same type as input.

    Raises
    ------
    ValueError
        If `price_lookup` returns None for any day from the earliest cashflow through `end`.
    """
    was_eager = isinstance(cashflows, pl.DataFrame)
    flows = collect_if_lazy(cashflows)
    if flows.is_empty():
        empty = pl.DataFrame(schema=_EMPTY_SERIES_SCHEMA)
        return empty if was_eager else empty.lazy()

    daily_flows = (
        flows
        .with_columns(flow_date=pl.col("event_datetime").dt.date())
        .group_by("flow_date")
        .agg(amount=pl.col("amount").sum())
    )
    flows_by_date: dict[date, float] = dict(
        zip(daily_flows["flow_date"].to_list(), daily_flows["amount"].to_list(), strict=True)
    )

    shares = 0.0
    dates: list[date] = []
    values: list[float] = []
    current_date = min(flows_by_date)
    while current_date <= end:
        price = price_lookup(current_date)
        if price is None:
            message = f"No price available for {current_date}."
            raise ValueError(message)
        shares -= flows_by_date.get(current_date, 0.0) / price
        dates.append(current_date)
        values.append(shares * price)
        current_date += timedelta(days=1)
    if not dates:
        empty = pl.DataFrame(schema=_EMPTY_SERIES_SCHEMA)
        return empty if was_eager else empty.lazy()
    result = pl.DataFrame({"date": dates, "value": values})
    return result if was_eager else result.lazy()
