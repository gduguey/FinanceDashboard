"""Replay the ledger.

Walk every event in chronological order and produce the state everything
else — cost basis, gains, XIRR, NAV — gets computed from. Nothing here is
stored; call this fresh whenever current (or `as_of`, by truncating the
ledger before calling) state is needed.

The walk is a genuinely sequential fold: each event's effect depends on
open lots left by every prior event, so it is a plain `for` loop rather
than a vectorized expression. Each event costs O(1) amortized work in
`lots.LotBook`, so one replay is linear in the ledger — see that class
for the two representation choices that make it so, and item C4b for the
quadratic it replaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from trades.ledger.lots import ClosedLot, LotBook, closed_lots_to_frame, lots_to_frame
from trades.ledger.signs import cash_effect, signed_cash_effect
from trades.utils.frames import collect_if_lazy

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from trades.config import AppConfig


@dataclass(frozen=True)
class ReplayResult:
    """Ledger state as of the last event replayed."""

    open_lots: pl.DataFrame
    closed_lots: pl.DataFrame
    cash_balance: float


def _withholding_by_symbol_date(rows: pl.DataFrame) -> dict[tuple[str, date], float]:
    """Sum `WITHHOLDING` amounts per `(symbol, date)`, for netting against same-day dividends.

    Parameters
    ----------
    rows
        Ledger rows, already materialized.

    Returns
    -------
    dict[tuple[str, datetime.date], float]
        Total withheld amount per symbol per day.
    """
    wh_rows = rows.filter(pl.col("event_type") == "WITHHOLDING")
    if wh_rows.is_empty():
        return {}
    grouped = (
        wh_rows
        .with_columns(dt=pl.col("event_datetime").dt.date())
        .group_by(["symbol", "dt"])
        .agg(pl.col("amount").sum())
    )
    return {(row["symbol"], row["dt"]): row["amount"] for row in grouped.iter_rows(named=True)}


def replay_ledger(
    ledger: pl.DataFrame | pl.LazyFrame, config: AppConfig, *, net_dividends: bool = False
) -> ReplayResult:
    """Replay a ledger into open/closed lots and a cash balance.

    `config.ledger.cash_symbol` is a plain running total, not a lot: every
    dollar is identical, so there is no cost-basis heterogeneity for FIFO
    to track, unlike a real symbol where different buys have different
    prices. Each `DIVIDEND` also accrues into the open lots of its symbol
    (see `lots.LotBook.accrue_dividend`), pro rata by shares held at that
    moment — a lot opened after the dividend was paid gets none of it,
    including a lot the dividend itself created via reinvestment.

    A `DIVIDEND` or `SPLIT` on a symbol no `BUY` has ever opened a lot for
    still moves cash and is otherwise skipped: there is no book to credit
    or adjust. That is the same outcome the version this replaced reached
    by handing an empty lot list to a function that returned it unchanged.

    Parameters
    ----------
    ledger
        The ledger to replay, in chronological order (see
        `brokers/ibkr/main.py:load_ledger`). Row iteration requires the
        data to be materialized, so a `LazyFrame` is collected immediately.
    config
        Application configuration; `config.ledger` is read.
    net_dividends
        When ``True``, the amount accrued into each lot is the gross
        ``DIVIDEND`` minus any ``WITHHOLDING`` for the same symbol on the
        same date, so ``lot.dividends_received`` reflects after-withholding
        income. Cash is always correct regardless of this flag (DIVIDEND
        adds gross, WITHHOLDING subtracts separately).

    Returns
    -------
    ReplayResult
        The open lots, closed lots, and cash balance after every event.

    An event type `ledger.signs` knows no direction for raises
    `ValueError` out of `cash_effect`, which is now the single place an
    unhandled type is caught rather than a fall-through branch here.
    """
    rows = collect_if_lazy(ledger)
    books: dict[str, LotBook] = {}
    closed_lots: list[ClosedLot] = []
    cash_balance = 0.0
    withholding_by_symbol_date = _withholding_by_symbol_date(rows) if net_dividends else {}

    for row in rows.iter_rows(named=True):
        event_type: str = row["event_type"]
        symbol: str = row["symbol"]
        amount: float = row["amount"]

        # Direction is decided once, in `ledger.signs`, for every event type
        # at once — including the ones whose only effect is on cash. What is
        # left below is per-type *structure* (which events open a lot, close
        # one, or accrue a dividend), never a sign.
        cash_balance += cash_effect(event_type, amount)

        if event_type == "DIVIDEND":
            book = books.get(symbol)
            if book is None:
                continue
            accrual_amount = amount
            if net_dividends:
                wh = withholding_by_symbol_date.get((symbol, row["event_datetime"].date()), 0.0)
                accrual_amount = max(0.0, amount - wh)
            book.accrue_dividend(accrual_amount)
        elif event_type == "BUY":
            books.setdefault(symbol, LotBook(symbol)).open(
                lot_id=row["event_id"],
                opened_at=row["event_datetime"],
                shares=row["shares"],
                cost_per_share=row["price"],
            )
        elif event_type == "SELL":
            closed_lots.extend(
                books.setdefault(symbol, LotBook(symbol)).consume_fifo(
                    shares_to_consume=row["shares"],
                    exit_price=row["price"],
                    closed_at=row["event_datetime"],
                    closed_by_event_id=row["event_id"],
                    long_term_holding_days=config.ledger.long_term_holding_days,
                )
            )
        elif event_type == "SPLIT":
            book = books.get(symbol)
            if book is None:
                continue
            book.apply_split(float(row["meta"]["ratio"]))

    all_open_lots = [lot for book in books.values() for lot in book.open_lots()]
    return ReplayResult(
        open_lots=lots_to_frame(all_open_lots),
        closed_lots=closed_lots_to_frame(closed_lots),
        cash_balance=cash_balance,
    )


def external_cashflows(ledger: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame | pl.LazyFrame:
    """Select the ledger's external cashflows, signed for money-weighted-return math.

    `DEPOSIT`/`WITHDRAWAL` are the only two event types that cross the
    portfolio boundary — every other type (`BUY`, `SELL`, `DIVIDEND`,
    `FEE`, `SPLIT`, `WITHHOLDING`) moves money or shares internally and is
    excluded. This is the cashflow set XIRR, the counterfactual engines,
    and NAV unit minting/burning all read from.

    The sign is the *negation* of the cash effect (`ledger.signs`), stated
    as a negation rather than re-derived from `event_type`: money-weighted
    return is computed from the investor's side of the boundary, where
    paying money in is an outflow.

    Parameters
    ----------
    ledger
        The event ledger.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        Columns `event_datetime`, `amount` — `DEPOSIT` negative,
        `WITHDRAWAL` positive. Same type as `ledger`.
    """
    return ledger.filter(pl.col("event_type").is_in(["DEPOSIT", "WITHDRAWAL"])).select(
        "event_datetime", amount=-signed_cash_effect()
    )


def portfolio_value(result: ReplayResult, price_lookup: Callable[[str, date], float | None], as_of: date) -> float:
    """Compute total portfolio value: open-lot holdings priced as of a date, plus cash.

    `price_lookup` is an arbitrary Python callback (a cache lookup), not a
    polars expression, so each symbol still held is priced by a plain
    `for` loop rather than a vectorized computation (same justification as
    `returns.build_returns_table`).

    Parameters
    ----------
    result
        A replayed ledger's open lots and cash balance.
    price_lookup
        Looks up a symbol's price as of a given date; returns None if unavailable.
    as_of
        The date to price every holding as of.

    Returns
    -------
    float
        `Σ shares x price + cash`.

    Raises
    ------
    ValueError
        If `price_lookup` returns None for any symbol still held.
    """
    if result.open_lots.is_empty():
        return result.cash_balance

    holdings_value = 0.0
    for row in result.open_lots.group_by("symbol").agg(shares=pl.col("shares").sum()).iter_rows(named=True):
        price = price_lookup(row["symbol"], as_of)
        if price is None:
            message = f"No price available for {row['symbol']} on or before {as_of}."
            raise ValueError(message)
        holdings_value += row["shares"] * price
    return holdings_value + result.cash_balance
