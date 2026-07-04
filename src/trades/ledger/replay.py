"""Replay the ledger.

Walk every event in chronological order and produce the state everything
else — cost basis, gains, XIRR, NAV — gets computed from. Nothing here is
stored; call this fresh whenever current (or `as_of`, by truncating the
ledger before calling) state is needed.

The walk is a genuinely sequential fold: each event's effect depends on
open lots left by every prior event, so it is a plain `for` loop rather
than a vectorized expression. The per-event arithmetic itself
(`lots.consume_fifo`, `lots.apply_split`) is vectorized.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from trades.ledger.lots import (
    ClosedLot,
    Lot,
    accrue_dividend,
    apply_split,
    closed_lots_to_frame,
    consume_fifo,
    lots_to_frame,
)
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


def replay_ledger(ledger: pl.DataFrame | pl.LazyFrame, config: AppConfig) -> ReplayResult:
    """Replay a ledger into open/closed lots and a cash balance.

    `config.ledger.cash_symbol` is a plain running total, not a lot: every
    dollar is identical, so there is no cost-basis heterogeneity for FIFO
    to track, unlike a real symbol where different buys have different
    prices. Each `DIVIDEND` also accrues into the open lots of its symbol
    (see `lots.accrue_dividend`), pro rata by shares held at that moment —
    a lot opened after the dividend was paid gets none of it, including a
    lot the dividend itself created via reinvestment.

    Parameters
    ----------
    ledger
        The ledger to replay, in chronological order (see
        `brokers/ibkr/main.py:load_ledger`). Row iteration requires the
        data to be materialized, so a `LazyFrame` is collected immediately.
    config
        Application configuration; `config.ledger` is read.

    Returns
    -------
    ReplayResult
        The open lots, closed lots, and cash balance after every event.

    Raises
    ------
    ValueError
        If the ledger contains an event type this replay doesn't handle.
    """
    rows = collect_if_lazy(ledger)
    open_lots_by_symbol: dict[str, list[Lot]] = {}
    closed_lots: list[ClosedLot] = []
    cash_balance = 0.0

    for row in rows.iter_rows(named=True):
        event_type: str = row["event_type"]
        symbol: str = row["symbol"]
        amount: float = row["amount"]

        if event_type == "DEPOSIT":
            cash_balance += amount
        elif event_type == "DIVIDEND":
            cash_balance += amount
            open_lots_by_symbol[symbol] = accrue_dividend(open_lots_by_symbol.get(symbol, []), amount)
        elif event_type in {"WITHDRAWAL", "WITHHOLDING", "FEE"}:
            cash_balance -= amount
        elif event_type == "BUY":
            cash_balance -= amount
            open_lots_by_symbol.setdefault(symbol, []).append(
                Lot(
                    lot_id=row["event_id"],
                    symbol=symbol,
                    opened_at=row["event_datetime"],
                    shares=row["shares"],
                    cost_per_share=row["price"],
                )
            )
        elif event_type == "SELL":
            cash_balance += amount
            remaining, newly_closed = consume_fifo(
                open_lots_by_symbol.get(symbol, []),
                shares_to_consume=row["shares"],
                exit_price=row["price"],
                closed_at=row["event_datetime"],
                closed_by_event_id=row["event_id"],
                long_term_holding_days=config.ledger.long_term_holding_days,
            )
            open_lots_by_symbol[symbol] = remaining
            closed_lots.extend(newly_closed)
        elif event_type == "SPLIT":
            ratio = float(row["meta"]["ratio"])
            open_lots_by_symbol[symbol] = apply_split(open_lots_by_symbol.get(symbol, []), symbol, ratio)
        else:
            message = f"Unhandled ledger event_type: {event_type!r}"
            raise ValueError(message)

    all_open_lots = [lot for lots in open_lots_by_symbol.values() for lot in lots]
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
        "event_datetime",
        amount=pl.when(pl.col("event_type") == "DEPOSIT").then(-pl.col("amount")).otherwise(pl.col("amount")),
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
