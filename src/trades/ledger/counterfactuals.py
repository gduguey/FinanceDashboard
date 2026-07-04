"""The counterfactual engine.

Every counterfactual replays a ledger through `replay.replay_ledger` and
values the result through `replay.portfolio_value`.
`hysa_counterfactual_value` and `benchmark_counterfactual_value`
value a synthetic position bought with the real ledger's external flows;
`decision_counterfactual_value` instead replays the real ledger itself
with one reallocation's sell/buy removed.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

from trades.ledger.replay import portfolio_value, replay_ledger
from trades.utils.frames import collect_if_lazy

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date

    from trades.config import AppConfig


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
    series = hysa_counterfactual_series(cashflows, as_of, rate_lookup, days_per_year)
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
    `benchmark_counterfactual_value` is a thin wrapper around this that
    keeps only the last day.

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


def benchmark_counterfactual_value(
    cashflows: pl.DataFrame | pl.LazyFrame,
    as_of: date,
    price_lookup: Callable[[date], float | None],
) -> float:
    """Buy a benchmark with a set of external cashflows and value the resulting position.

    Parameters
    ----------
    cashflows
        External cashflows, as returned by `replay.external_cashflows`:
        columns `event_datetime`, `amount` (`DEPOSIT` negative,
        `WITHDRAWAL` positive).
    as_of
        The date to value the resulting position as of.
    price_lookup
        Looks up the benchmark's adjusted price as of a given date;
        returns None if unavailable.

    Returns
    -------
    float
        `shares_held x price(as_of)`.
    """
    series = benchmark_counterfactual_series(cashflows, as_of, price_lookup)
    if series.is_empty():
        return 0.0
    return float(series["value"][-1])


def decision_counterfactual_value(
    ledger: pl.DataFrame | pl.LazyFrame,
    skip_event_ids: Sequence[str],
    price_lookup: Callable[[str, date], float | None],
    as_of: date,
    config: AppConfig,
) -> float:
    """Value the ledger as if one reallocation's sell and buy had never happened.

    Answers "what if I hadn't sold": drops the given event IDs (a
    reallocation's `SELL` and the `BUY` it funded) from the ledger,
    replays what remains through `replay.replay_ledger`, and values the
    result through `replay.portfolio_value` — the shadow position would
    still hold whatever was sold instead of what it was reallocated into.
    Comparing this against the real portfolio's value at the same date
    gives `decision_alpha = actual_portfolio_value - decision_counterfactual_value`.

    `ledger` is truncated to events on or before `as_of` before replaying:
    `replay_ledger` has no date awareness of its own (see its docstring),
    so if this didn't truncate, any event after `as_of` would still be
    replayed and `portfolio_value` would then price a holding that, as of
    `as_of`, was never actually bought.

    Parameters
    ----------
    ledger
        The real ledger, in chronological order.
    skip_event_ids
        Event IDs to drop before replaying — a reallocation's `SELL` and
        `BUY` rows. An empty sequence reproduces the real ledger's replay.
    price_lookup
        Looks up a symbol's price as of a given date; returns None if unavailable.
    as_of
        The date to value the shadow portfolio as of.
    config
        Application configuration, passed through to `replay_ledger`.

    Returns
    -------
    float
        The shadow portfolio's value as of `as_of`.
    """
    rows = collect_if_lazy(ledger)
    shadow_ledger = rows.filter(
        (pl.col("event_datetime").dt.date() <= as_of) & ~pl.col("event_id").is_in(skip_event_ids)
    )
    result = replay_ledger(shadow_ledger, config)
    return portfolio_value(result, price_lookup, as_of)
