"""Portfolio and per-lot performance metrics, built on `replay.replay_ledger`'s output.

Portfolio XIRR, per-symbol XIRR/return, realized+unrealized gain split,
and max drawdown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

import numpy as np
import polars as pl

from trades.utils.frames import collect_if_lazy

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date

    from trades.config import AppConfig
    from trades.ledger.replay import ReplayResult

_XIRR_MIN_CASHFLOWS = 2  # a rate needs at least an outflow and a terminal value — not a tunable, a mathematical floor
_XIRR_BISECTION_BRACKET = (-0.9999, 10.0)  # the feasible domain of an annual return: over -100%, under 1000%


def lot_returns(
    open_lots: pl.DataFrame | pl.LazyFrame,
    price_lookup: Callable[[str, date], float | None],
    as_of: date,
    config: AppConfig,
) -> pl.DataFrame | pl.LazyFrame:
    """Compute each open lot's total return, including dividends, as of a date.

    `price_lookup` is an arbitrary Python callback, not a polars
    expression, so each symbol is priced by a plain `for` loop rather than
    a vectorized computation (same justification as
    `returns.build_returns_table`); the return arithmetic itself is
    vectorized. Per GIPS convention, a lot held under
    `config.returns.annualization_days` gets no annualized figure at all —
    short holds otherwise produce large-looking numbers that are mostly noise.

    Parameters
    ----------
    open_lots
        Open lots (see `replay.ReplayResult.open_lots`), with `symbol`,
        `shares`, `cost_per_share`, `opened_at`, `dividends_received` columns.
    price_lookup
        Looks up a symbol's price as of a given date; returns None if unavailable.
    as_of
        The date to value every lot as of.
    config
        Application configuration; `config.returns.annualization_days` is read.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        `open_lots` plus `current_price`, `days_held`, `raw_return_pct`,
        `annualized_return_pct` (null under `config.returns.annualization_days`
        days held). Same type as `open_lots`.

    Raises
    ------
    ValueError
        If `price_lookup` returns None for any lot's symbol, or if a lot's
        `opened_at` is after `as_of`.
    """
    was_eager = isinstance(open_lots, pl.DataFrame)
    lots = collect_if_lazy(open_lots)

    current_prices: list[float] = []
    for row in lots.iter_rows(named=True):
        price = price_lookup(row["symbol"], as_of)
        if price is None:
            message = f"No price available for {row['symbol']} on or before {as_of}."
            raise ValueError(message)
        current_prices.append(price)

    with_days_held = lots.with_columns(
        current_price=pl.Series(current_prices, dtype=pl.Float64),
        days_held=(pl.lit(as_of) - pl.col("opened_at").dt.date()).dt.total_days(),
    )
    if not with_days_held.is_empty() and cast("int", with_days_held["days_held"].min()) < 0:
        message = f"A lot's days_held is negative — as_of ({as_of}) is before that lot's opened_at."
        raise ValueError(message)

    annualization_days = config.returns.annualization_days
    result = with_days_held.with_columns(
        raw_return_pct=(
            (pl.col("shares") * pl.col("current_price") + pl.col("dividends_received"))
            / (pl.col("shares") * pl.col("cost_per_share"))
            - 1
        )
        * 100
    ).with_columns(
        annualized_return_pct=pl
        .when(pl.col("days_held") >= annualization_days)
        .then(((1 + pl.col("raw_return_pct") / 100) ** (annualization_days / pl.col("days_held")) - 1) * 100)
        .otherwise(None)
    )
    return result if was_eager else result.lazy()


def xirr(dates: Sequence[date], amounts: Sequence[float], config: AppConfig) -> float:
    """Solve for the money-weighted rate of return over a set of dated cashflows.

    XIRR (the "extended internal rate of return") is the single constant
    annual interest rate that, if every cashflow had earned it from the
    day it happened, would leave a running balance of exactly zero. It
    generalizes the familiar "internal rate of return" to cashflows on
    arbitrary dates rather than evenly spaced periods. Solved by
    Newton-Raphson with a bisection fallback. The iteration itself is a
    genuinely sequential numerical search — each guess depends on the
    previous one — so it's a bounded loop, not a vectorized expression;
    the NPV and its derivative at each guess ARE vectorized, over every
    cashflow at once via numpy.

    Parameters
    ----------
    dates
        Each cashflow's date.
    amounts
        Each cashflow's signed amount (money out negative, money in positive).
    config
        Application configuration; `config.returns.days_per_year`,
        `xirr_tolerance`, and `xirr_max_newton_iterations` are read.

    Returns
    -------
    float
        The annualized rate `r` solving `sum(CF_i / (1 + r)^((d_i - d_0)/days_per_year)) = 0`.

    Raises
    ------
    ValueError
        If fewer than 2 cashflows are given, if every amount shares a sign
        (no rate can reconcile an all-outflow or all-inflow set), or if
        neither Newton-Raphson nor bisection converges.
    """
    if len(dates) < _XIRR_MIN_CASHFLOWS:
        message = "xirr needs at least 2 cashflows (an outflow and a terminal value)."
        raise ValueError(message)
    amounts_arr = np.asarray(amounts, dtype=float)
    if not (np.any(amounts_arr > 0) and np.any(amounts_arr < 0)):
        message = "xirr needs both a negative and a positive cashflow — check the sign convention."
        raise ValueError(message)

    first_date = min(dates)
    years = np.array([(d - first_date).days / config.returns.days_per_year for d in dates], dtype=float)

    def npv(rate: float) -> float:
        return float(np.sum(amounts_arr / (1 + rate) ** years))

    def npv_derivative(rate: float) -> float:
        return float(np.sum(-years * amounts_arr / (1 + rate) ** (years + 1)))

    tolerance = config.returns.xirr_tolerance
    rate = 0.1
    for _ in range(config.returns.xirr_max_newton_iterations):
        value = npv(rate)
        if abs(value) < tolerance:
            return rate
        derivative = npv_derivative(rate)
        if derivative == 0 or rate <= -1:
            break
        rate -= value / derivative

    return _bisect_xirr(npv, tolerance, config.returns.xirr_max_bisection_iterations)


def _bisect_xirr(npv: Callable[[float], float], tolerance: float, max_iterations: int) -> float:
    lo, hi = _XIRR_BISECTION_BRACKET
    npv_lo, npv_hi = npv(lo), npv(hi)
    if npv_lo * npv_hi > 0:
        message = (
            f"xirr found no sign change in NPV across the search bracket {_XIRR_BISECTION_BRACKET} "
            "— the cashflow set may have multiple roots or none in this range."
        )
        raise ValueError(message)

    for _ in range(max_iterations):
        mid = (lo + hi) / 2
        npv_mid = npv(mid)
        if abs(npv_mid) < tolerance:
            return mid
        if (npv_mid > 0) == (npv_lo > 0):
            lo, npv_lo = mid, npv_mid
        else:
            hi = mid

    message = f"xirr did not converge after {max_iterations} bisection iterations."
    raise ValueError(message)


def realized_gain_total(closed_lots: pl.DataFrame | pl.LazyFrame) -> float:
    """Sum realized gain across every closed lot.

    Parameters
    ----------
    closed_lots
        Closed lots (see `replay.ReplayResult.closed_lots`), with a `realized_gain` column.

    Returns
    -------
    float
        `Σ closed lots: realized_gain`, or 0.0 if there are none.
    """
    lots = collect_if_lazy(closed_lots)
    if lots.is_empty():
        return 0.0
    return float(lots["realized_gain"].sum())


def unrealized_gain(
    open_lots: pl.DataFrame | pl.LazyFrame,
    price_lookup: Callable[[str, date], float | None],
    as_of: date,
    *,
    include_dividends: bool = False,
) -> float:
    """Sum price-appreciation gain across every open lot, priced as of a date.

    `price_lookup` is an arbitrary Python callback, not a polars
    expression, so each symbol still held is priced by a plain `for` loop
    rather than a vectorized computation (same justification as
    `replay.portfolio_value`).

    Parameters
    ----------
    open_lots
        Open lots (see `replay.ReplayResult.open_lots`).
    price_lookup
        Looks up a symbol's price as of a given date; returns None if unavailable.
    as_of
        The date to value every lot as of.
    include_dividends
        Also add each lot's `dividends_received`, for a total-return figure
        rather than price appreciation alone.

    Returns
    -------
    float
        `Σ open lots: shares x (current_price - cost_per_share)`, plus
        `dividends_received` if `include_dividends`. 0.0 if there are no open lots.

    Raises
    ------
    ValueError
        If `price_lookup` returns None for any symbol still held.
    """
    lots = collect_if_lazy(open_lots)
    if lots.is_empty():
        return 0.0

    prices: dict[str, float] = {}
    for row in lots.select("symbol").unique().iter_rows(named=True):
        price = price_lookup(row["symbol"], as_of)
        if price is None:
            message = f"No price available for {row['symbol']} on or before {as_of}."
            raise ValueError(message)
        prices[row["symbol"]] = price

    with_price = lots.with_columns(current_price=pl.col("symbol").replace_strict(prices, return_dtype=pl.Float64))
    total = float((with_price["shares"] * (with_price["current_price"] - with_price["cost_per_share"])).sum())
    if include_dividends:
        total += float(lots["dividends_received"].sum())
    return total


@dataclass(frozen=True)
class SymbolMetrics:
    """Lifecycle stats and money-weighted return for one symbol."""

    symbol: str
    invested: float
    proceeds_received: float
    dividends_received: float
    current_value: float
    realized_gain: float
    unrealized_gain: float
    status: Literal["open", "closed"]
    xirr: float


def symbol_metrics(
    ledger: pl.DataFrame | pl.LazyFrame,
    replay_result: ReplayResult,
    symbol: str,
    price_lookup: Callable[[str, date], float | None],
    as_of: date,
    config: AppConfig,
    *,
    net_dividends: bool = False,
) -> SymbolMetrics:
    """Compute one symbol's lifecycle stats and money-weighted return.

    `invested` excludes dividend-reinvestment buys: a `BUY` funded by that
    symbol's own dividend isn't new capital, it's income the symbol
    already earned being recycled — counting it would inflate `invested`
    and understate the symbol's true return. The XIRR cashflow set, in
    contrast, keeps every `BUY` including reinvestment ones: a same-date
    `DIVIDEND`(+)/`BUY`(-) pair nets to zero, which is the correct
    treatment — the reinvested amount's growth shows up through the
    terminal value, exactly like a total-return index.

    A fully closed symbol (no open lots) gets no terminal-value flow — its
    XIRR is a final, frozen figure that can't change anymore.

    Parameters
    ----------
    ledger
        The event ledger.
    replay_result
        The same ledger's replayed state (see `replay.replay_ledger`).
    symbol
        The symbol to compute stats for.
    price_lookup
        Looks up a symbol's price as of a given date; returns None if unavailable.
    as_of
        The date to value any remaining open position as of.
    config
        Application configuration; `config.returns.days_per_year` is read.
    net_dividends
        When ``True``, ``dividends_received`` is gross dividends minus
        withholding instead of the gross amount. Does not affect ``xirr``:
        its cashflow set always includes ``WITHHOLDING`` (see
        `_symbol_cashflows`), independent of this flag.

    Returns
    -------
    SymbolMetrics
        The symbol's lifecycle stats and XIRR.

    Raises
    ------
    ValueError
        If the symbol has an open position and `price_lookup` returns None for it.
    """
    rows = collect_if_lazy(ledger).filter(pl.col("symbol") == symbol)
    open_lots = replay_result.open_lots.filter(pl.col("symbol") == symbol)
    is_open = not open_lots.is_empty()

    current_value, unrealized = 0.0, 0.0
    if is_open:
        price = price_lookup(symbol, as_of)
        if price is None:
            message = f"No price available for {symbol} on or before {as_of}."
            raise ValueError(message)
        current_value = float(open_lots["shares"].sum()) * price
        unrealized = float((open_lots["shares"] * (price - open_lots["cost_per_share"])).sum())

    cashflow_dates, cashflow_amounts = _symbol_cashflows(rows)
    if is_open:
        cashflow_dates.append(as_of)
        cashflow_amounts.append(current_value)

    gross_dividends = float(rows.filter(pl.col("event_type") == "DIVIDEND")["amount"].sum())
    withholding = float(rows.filter(pl.col("event_type") == "WITHHOLDING")["amount"].sum()) if net_dividends else 0.0

    return SymbolMetrics(
        symbol=symbol,
        invested=_symbol_invested(rows.filter(pl.col("event_type") == "BUY")),
        proceeds_received=float(rows.filter(pl.col("event_type") == "SELL")["amount"].sum()),
        dividends_received=gross_dividends - withholding,
        current_value=current_value,
        realized_gain=float(replay_result.closed_lots.filter(pl.col("symbol") == symbol)["realized_gain"].sum()),
        unrealized_gain=unrealized,
        status="open" if is_open else "closed",
        xirr=xirr(cashflow_dates, cashflow_amounts, config),
    )


def _symbol_invested(buys: pl.DataFrame) -> float:
    """Sum a symbol's `BUY` amounts, excluding dividend-reinvestment buys.

    A `BUY` funded by that symbol's own dividend isn't new capital — see
    `symbol_metrics`'s docstring for the full reasoning.

    Parameters
    ----------
    buys
        The symbol's `BUY` ledger rows.

    Returns
    -------
    float
        Total `amount` across non-DRIP buys, or 0.0 if there are none.
    """
    if buys.is_empty():
        return 0.0
    is_drip = buys["meta"].map_elements(lambda meta: meta.get("drip_reinvestment") == "true", return_dtype=pl.Boolean)
    return float(buys.filter(~is_drip)["amount"].sum())


def _symbol_cashflows(rows: pl.DataFrame) -> tuple[list[date], list[float]]:
    """Build a symbol's `BUY`/`SELL`/`DIVIDEND`/`WITHHOLDING` cashflows for `xirr`.

    `WITHHOLDING` is always included as a negative flow, the same way
    `replay.replay_ledger` always subtracts it from `cash_balance` — so the
    cash XIRR is computed from reflects what actually moved, independent of
    `symbol_metrics`'s `net_dividends` toggle (which only changes how
    `dividends_received` is reported, not the underlying cashflows).

    Parameters
    ----------
    rows
        The symbol's ledger rows.

    Returns
    -------
    tuple[list[datetime.date], list[float]]
        Dates and signed amounts (`BUY`/`WITHHOLDING` negative,
        `SELL`/`DIVIDEND` positive).
    """
    types = ["BUY", "SELL", "DIVIDEND", "WITHHOLDING"]
    flows = rows.filter(pl.col("event_type").is_in(types)).select(
        "event_datetime",
        amount=pl
        .when(pl.col("event_type").is_in(["BUY", "WITHHOLDING"]))
        .then(-pl.col("amount"))
        .otherwise(pl.col("amount")),
    )
    return flows["event_datetime"].dt.date().to_list(), flows["amount"].to_list()


def max_drawdown(series: pl.DataFrame | pl.LazyFrame, value_column: str) -> float:
    """Find the largest peak-to-trough decline in a value series.

    Parameters
    ----------
    series
        A value series in chronological order (e.g. a NAV series, see `nav.nav_series`).
    value_column
        Name of the column holding the value at each point.

    Returns
    -------
    float
        `min over t of (value(t) / max(value(0..t)) - 1)`, as a fraction
        (e.g. -0.25 for a 25% decline); 0.0 for an empty or
        never-declining series.
    """
    values = collect_if_lazy(series)
    if values.is_empty():
        return 0.0
    running_max = values[value_column].cum_max()
    drawdown = values[value_column] / running_max - 1
    return cast("float", drawdown.min())
