"""FIFO tax-lot tracking.

Each `BUY` opens a lot; each `SELL` closes lots oldest-first, tagging
realized gain and holding-period term. Pure logic, no I/O and no
ledger-walking — that is `replay.py`, which calls into this module once
per `BUY`/`SELL`/`SPLIT` event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

import polars as pl

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class Lot:
    """One open (or partially closed) tax lot."""

    lot_id: str
    symbol: str
    opened_at: datetime
    shares: float
    cost_per_share: float

    polars_schema: ClassVar[dict[str, type[pl.DataType] | pl.DataType]] = {
        "lot_id": pl.Utf8,
        "symbol": pl.Utf8,
        "opened_at": pl.Datetime("us"),
        "shares": pl.Float64,
        "cost_per_share": pl.Float64,
    }


@dataclass(frozen=True)
class ClosedLot:
    """The portion of a lot consumed by one `SELL` (or other closing event)."""

    lot_id: str
    symbol: str
    opened_at: datetime
    closed_at: datetime
    shares: float
    cost_per_share: float
    exit_price: float
    realized_gain: float
    term: Literal["LONG", "SHORT"]
    closed_by_event_id: str

    polars_schema: ClassVar[dict[str, type[pl.DataType] | pl.DataType]] = {
        "lot_id": pl.Utf8,
        "symbol": pl.Utf8,
        "opened_at": pl.Datetime("us"),
        "closed_at": pl.Datetime("us"),
        "shares": pl.Float64,
        "cost_per_share": pl.Float64,
        "exit_price": pl.Float64,
        "realized_gain": pl.Float64,
        "term": pl.Utf8,
        "closed_by_event_id": pl.Utf8,
    }


def lots_to_frame(open_lots: list[Lot]) -> pl.DataFrame:
    """Convert a list of open lots to a DataFrame.

    Parameters
    ----------
    open_lots
        The lots to convert.

    Returns
    -------
    polars.DataFrame
        Columns `lot_id`, `symbol`, `opened_at`, `shares`, `cost_per_share`.
    """
    if not open_lots:
        return pl.DataFrame(schema=Lot.polars_schema)
    return pl.DataFrame([vars(lot) for lot in open_lots], schema=Lot.polars_schema)


def closed_lots_to_frame(closed_lots: list[ClosedLot]) -> pl.DataFrame:
    """Convert a list of closed lots to a DataFrame.

    Parameters
    ----------
    closed_lots
        The closed lots to convert.

    Returns
    -------
    polars.DataFrame
        Columns `lot_id`, `symbol`, `opened_at`, `closed_at`, `shares`,
        `cost_per_share`, `exit_price`, `realized_gain`, `term`, `closed_by_event_id`.
    """
    if not closed_lots:
        return pl.DataFrame(schema=ClosedLot.polars_schema)
    return pl.DataFrame([vars(lot) for lot in closed_lots], schema=ClosedLot.polars_schema)


def consume_fifo(
    open_lots: list[Lot],
    shares_to_consume: float,
    exit_price: float,
    closed_at: datetime,
    closed_by_event_id: str,
    long_term_holding_days: int,
    total_fees: float = 0.0,
) -> tuple[list[Lot], list[ClosedLot]]:
    """Consume shares oldest-`opened_at`-first from a set of open lots.

    `total_fees` is allocated pro rata by shares consumed:
    `realized_gain = shares_consumed x (exit_price - cost_per_share) - allocated_fees`.

    Parameters
    ----------
    open_lots
        The lots to consume from.
    shares_to_consume
        The number of shares being sold (or otherwise closed out).
    exit_price
        The price per share the shares are closed at.
    closed_at
        The timestamp of the closing event.
    closed_by_event_id
        The ledger event id that triggered this consumption.
    long_term_holding_days
        Holding period, in days, at or above which a closed lot is tagged `LONG` rather than `SHORT`.
    total_fees
        Total fees charged on the closing event, allocated pro rata across the lots consumed.

    Returns
    -------
    tuple[list[Lot], list[ClosedLot]]
        The lots still open afterward (a partially consumed lot keeps its
        remainder) and one `ClosedLot` per lot touched.

    Raises
    ------
    ValueError
        If `open_lots` doesn't hold enough shares to satisfy `shares_to_consume`.
    """
    fee_per_share = total_fees / shares_to_consume if shares_to_consume else 0.0
    lots = (
        lots_to_frame(open_lots)
        .sort("opened_at")
        .with_columns(prior_cum_shares=pl.col("shares").cum_sum() - pl.col("shares"))
        .with_columns(
            consumed_shares=pl.min_horizontal(
                pl.col("shares"),
                (pl.lit(shares_to_consume) - pl.col("prior_cum_shares")).clip(lower_bound=0.0),
            )
        )
        .with_columns(
            remaining_shares=pl.col("shares") - pl.col("consumed_shares"),
            held_days=(pl.lit(closed_at) - pl.col("opened_at")).dt.total_days(),
            realized_gain=pl.col("consumed_shares") * (exit_price - pl.col("cost_per_share"))
            - pl.col("consumed_shares") * fee_per_share,
        )
        .with_columns(
            term=pl.when(pl.col("held_days") >= long_term_holding_days).then(pl.lit("LONG")).otherwise(pl.lit("SHORT"))
        )
    )

    total_consumed = lots["consumed_shares"].sum()
    if total_consumed < shares_to_consume - 1e-9:
        message = f"Cannot consume {shares_to_consume} shares — only {total_consumed} available across open lots."
        raise ValueError(message)

    still_open = [
        Lot(
            lot_id=row["lot_id"],
            symbol=row["symbol"],
            opened_at=row["opened_at"],
            shares=row["remaining_shares"],
            cost_per_share=row["cost_per_share"],
        )
        for row in lots.filter(pl.col("remaining_shares") > 0).iter_rows(named=True)
    ]
    closed = [
        ClosedLot(
            lot_id=row["lot_id"],
            symbol=row["symbol"],
            opened_at=row["opened_at"],
            closed_at=closed_at,
            shares=row["consumed_shares"],
            cost_per_share=row["cost_per_share"],
            exit_price=exit_price,
            realized_gain=row["realized_gain"],
            term=row["term"],
            closed_by_event_id=closed_by_event_id,
        )
        for row in lots.filter(pl.col("consumed_shares") > 0).iter_rows(named=True)
    ]
    return still_open, closed


def apply_split(open_lots: list[Lot], symbol: str, ratio: float) -> list[Lot]:
    """Apply a `SPLIT` event to a set of open lots.

    Multiplies share counts and divides cost/share for every open lot of
    `symbol`; lots of other symbols are untouched.

    Parameters
    ----------
    open_lots
        The lots to apply the split to.
    symbol
        The symbol that split.
    ratio
        The split ratio (e.g. 2.0 for a 2-for-1 split).

    Returns
    -------
    list[Lot]
        The lots with `symbol`'s shares/cost-per-share adjusted.
    """
    if not open_lots:
        return []
    is_split_symbol = pl.col("symbol") == symbol
    lots = lots_to_frame(open_lots).with_columns(
        shares=pl.when(is_split_symbol).then(pl.col("shares") * ratio).otherwise(pl.col("shares")),
        cost_per_share=pl
        .when(is_split_symbol)
        .then(pl.col("cost_per_share") / ratio)
        .otherwise(pl.col("cost_per_share")),
    )
    return [Lot(**row) for row in lots.iter_rows(named=True)]
