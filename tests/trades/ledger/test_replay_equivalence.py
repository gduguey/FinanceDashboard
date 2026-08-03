"""`replay_ledger` against the list-and-frame implementation it replaced, over generated ledgers.

`ledger.lots.LotBook` replaced three free functions that each rebuilt the
symbol's whole lot list through a polars frame — quadratic, and the reason
`GET /lots` cost 8 s at 10k events (item C4b). Swapping the representation
of money-handling code is only defensible if something asserts the
arithmetic did not move, and the twenty unit cases in `test_lots.py` plus
the twenty in `test_replay.py` do not: each pins one rule at a time, and
what a dividend-per-share accumulator can get wrong is the *interaction*
between rules — a dividend that lands between two partial sells of the
same lot, a split between two dividends, a lot opened after a dividend on
a symbol that has since been sold down to nothing.

So this keeps the retired implementation, verbatim apart from its
docstrings and a `total_fees` parameter nothing ever passed, as an oracle:
`_reference_replay` below *is* the code that shipped up to `v1.12.0`. Both
implementations walk the same generated ledgers and every column of both
output frames is compared. If the two ever disagree the test says which
column and by how much, which is the diagnostic a "lots look wrong" bug
report cannot give you.

The generated ledgers are the point. `sell_p` is what decides how many
lots stay open, so it is swept: at 0.20 the book is consumed roughly as
fast as it fills and lots stay few, at 0.03 it accumulates for the whole
history, which is both the regime a real buy-and-hold ledger is in and the
one where the accumulator has the most state to get wrong. `net_dividends`
is swept because it changes the accrued amount but nothing else, so a bug
that only shows up net would otherwise hide behind the default.

Kept in the default suite rather than marked `perf`: the 30 cases cost
about four seconds, nearly all of it the reference implementation being
quadratic, and a gate over a replacement of this kind should run on every
local `pytest` rather than only in its own CI job.

Verified non-vacuous rather than assumed to be, which is the lesson of C9:
four deliberate breaks of `LotBook` — consuming newest-first, splitting a
dividend per lot instead of per share, a split that does not bank what a
lot had accrued, and a partial close that keeps the parent's full dividend
total — fail all 30 cases each.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from functools import cache
from typing import TYPE_CHECKING

import polars as pl
import pytest

from trades.config import AppConfig
from trades.ledger.lots import ClosedLot, Lot, closed_lots_to_frame, lots_to_frame
from trades.ledger.replay import ReplayResult, _withholding_by_symbol_date, replay_ledger
from trades.ledger.signs import cash_effect

if TYPE_CHECKING:
    from collections.abc import Sequence

CONFIG = AppConfig()

_EVENTS = 601
"""Events per generated ledger.

Large enough that the low `sell_p` leaves 255-290 lots open across eight
symbols, having closed 46-85, which is the accumulating regime an
accumulator bug would show up in; small enough that the quadratic
reference implementation stays around a tenth of a second per case. The
figures scale roughly as the square, so doubling this quadruples the
suite's cost for coverage the sweep already provides.
"""

_SELL_PROBABILITIES = (0.20, 0.08, 0.03)
_SEEDS = (1, 2, 3, 4, 5)
_SYMBOLS = ("AAPL", "MSFT", "VTI", "SPY", "NVDA", "GOOG", "AMZN", "TSLA")

_RELATIVE_TOLERANCE = 1e-9
"""Per-column tolerance, relative to the column's own largest magnitude.

Not zero, and deliberately: the accumulator adds `amount / total_shares`
once and multiplies by a lot's shares on the way out, where the retired
version multiplied `amount * shares / total` per lot, so the two differ in
the last bits of a `float64`. Anything above rounding is a real
disagreement — a mis-split dividend or a lot consumed out of order moves a
figure by percent, not by `1e-16`.
"""


# --------------------------------------------------------------------------
# The oracle: `ledger.lots` and `ledger.replay` as they shipped up to v1.12.0.
# --------------------------------------------------------------------------


def _reference_consume_fifo(
    open_lots: list[Lot],
    shares_to_consume: float,
    exit_price: float,
    closed_at: datetime,
    closed_by_event_id: str,
    long_term_holding_days: int,
) -> tuple[list[Lot], list[ClosedLot]]:
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
            realized_gain=pl.col("consumed_shares") * (exit_price - pl.col("cost_per_share")),
            consumed_dividends=pl.col("dividends_received") * pl.col("consumed_shares") / pl.col("shares"),
        )
        .with_columns(
            remaining_dividends=pl.col("dividends_received") - pl.col("consumed_dividends"),
            term=pl.when(pl.col("held_days") >= long_term_holding_days).then(pl.lit("LONG")).otherwise(pl.lit("SHORT")),
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
            dividends_received=row["remaining_dividends"],
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
            dividends_received=row["consumed_dividends"],
        )
        for row in lots.filter(pl.col("consumed_shares") > 0).iter_rows(named=True)
    ]
    return still_open, closed


def _reference_accrue_dividend(open_lots: list[Lot], amount: float) -> list[Lot]:
    if not open_lots:
        return []
    lots = lots_to_frame(open_lots).with_columns(
        dividends_received=pl.col("dividends_received") + amount * pl.col("shares") / pl.col("shares").sum()
    )
    return [Lot(**row) for row in lots.iter_rows(named=True)]


def _reference_apply_split(open_lots: list[Lot], symbol: str, ratio: float) -> list[Lot]:
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


def _reference_replay(rows: pl.DataFrame, *, net_dividends: bool) -> ReplayResult:
    open_lots_by_symbol: dict[str, list[Lot]] = {}
    closed_lots: list[ClosedLot] = []
    cash_balance = 0.0
    withholding_by_symbol_date = _withholding_by_symbol_date(rows) if net_dividends else {}

    for row in rows.iter_rows(named=True):
        event_type: str = row["event_type"]
        symbol: str = row["symbol"]
        amount: float = row["amount"]
        cash_balance += cash_effect(event_type, amount)

        if event_type == "DIVIDEND":
            accrual_amount = amount
            if net_dividends:
                wh = withholding_by_symbol_date.get((symbol, row["event_datetime"].date()), 0.0)
                accrual_amount = max(0.0, amount - wh)
            open_lots_by_symbol[symbol] = _reference_accrue_dividend(
                open_lots_by_symbol.get(symbol, []), accrual_amount
            )
        elif event_type == "BUY":
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
            remaining, newly_closed = _reference_consume_fifo(
                open_lots_by_symbol.get(symbol, []),
                shares_to_consume=row["shares"],
                exit_price=row["price"],
                closed_at=row["event_datetime"],
                closed_by_event_id=row["event_id"],
                long_term_holding_days=CONFIG.ledger.long_term_holding_days,
            )
            open_lots_by_symbol[symbol] = remaining
            closed_lots.extend(newly_closed)
        elif event_type == "SPLIT":
            open_lots_by_symbol[symbol] = _reference_apply_split(
                open_lots_by_symbol.get(symbol, []), symbol, float(row["meta"]["ratio"])
            )

    return ReplayResult(
        open_lots=lots_to_frame([lot for lots in open_lots_by_symbol.values() for lot in lots]),
        closed_lots=closed_lots_to_frame(closed_lots),
        cash_balance=cash_balance,
    )


# --------------------------------------------------------------------------
# The generated ledgers.
# --------------------------------------------------------------------------


@cache
def _generated_ledger(n_events: int, seed: int, sell_p: float) -> pl.DataFrame:
    """A chronological ledger of `n_events` events, buy-heavy the way a real one is.

    Cached because each `(seed, sell_p)` pair is replayed twice — once per
    `net_dividends` value — and generating it costs more than a replay now
    does.
    """
    rng = random.Random(seed)
    start = datetime(2015, 1, 2, 10, 0)  # naive, like every ledger timestamp
    held = dict.fromkeys(_SYMBOLS, 0.0)
    rows: list[dict] = [_row("e0", start, "CASH", "DEPOSIT", amount=5_000_000.0)]
    day = 0
    i = 0
    while len(rows) < n_events:
        day += rng.choice([0, 0, 1, 1, 2, 3])
        when = start + timedelta(days=day, minutes=i % 300)
        symbol = rng.choice(_SYMBOLS)
        roll = rng.random()
        i += 1
        event_id = f"e{i}"
        if roll < 0.55:
            shares = float(rng.randint(1, 40))
            held[symbol] += shares
            rows.append(_row(event_id, when, symbol, "BUY", shares=shares, price=50.0 + rng.random() * 300))
        elif roll < 0.55 + sell_p and held[symbol] > 1:
            shares = float(rng.randint(1, max(1, int(held[symbol] // 3))))
            held[symbol] -= shares
            rows.append(_row(event_id, when, symbol, "SELL", shares=shares, price=50.0 + rng.random() * 300))
        elif roll < 0.93:
            rows.append(_row(event_id, when, symbol, "DIVIDEND", amount=rng.random() * 200))
        elif roll < 0.97:
            rows.append(_row(event_id, when, symbol, "WITHHOLDING", amount=rng.random() * 30))
        elif roll < 0.995:
            rows.append(_row(event_id, when, "CASH", "FEE", amount=rng.random() * 5))
        else:
            held[symbol] *= 2
            rows.append(_row(event_id, when, symbol, "SPLIT", meta={"ratio": 2.0}))
    metas = [row.pop("meta") for row in rows]
    return pl.DataFrame(rows).with_columns(meta=pl.Series("meta", metas, dtype=pl.Object))


def _row(
    event_id: str,
    when: datetime,
    symbol: str,
    event_type: str,
    shares: float | None = None,
    price: float | None = None,
    amount: float = 0.0,
    meta: dict | None = None,
) -> dict:
    return {
        "event_id": event_id,
        "event_datetime": when,
        "symbol": symbol,
        "event_type": event_type,
        "shares": shares,
        "price": price,
        "amount": amount,
        "currency": "USD",
        "meta": meta or {},
    }


# --------------------------------------------------------------------------
# The comparison.
# --------------------------------------------------------------------------


def _assert_frames_match(label: str, actual: pl.DataFrame, expected: pl.DataFrame, key: Sequence[str]) -> None:
    assert actual.shape == expected.shape, f"{label}: {actual.shape} rows/columns against {expected.shape}"
    if expected.is_empty():
        return
    assert set(actual.columns) == set(expected.columns), f"{label}: column names differ"
    got = actual.sort(key)
    want = expected.sort(key)
    for column in expected.columns:
        if want[column].dtype in {pl.Float32, pl.Float64}:
            delta = (got[column] - want[column]).abs().max()
            scale = max(1.0, float(want[column].abs().max() or 1.0))
            assert delta is not None
            assert delta <= _RELATIVE_TOLERANCE * scale, (
                f"{label}.{column}: largest absolute difference {delta:.3e} over a scale of {scale:.3e}"
            )
        else:
            assert got[column].equals(want[column]), f"{label}.{column}: differs"


@pytest.mark.parametrize("net_dividends", [False, True])
@pytest.mark.parametrize("seed", _SEEDS)
@pytest.mark.parametrize("sell_p", _SELL_PROBABILITIES)
def test_the_replay_matches_the_implementation_it_replaced(sell_p: float, seed: int, net_dividends: bool) -> None:
    ledger = _generated_ledger(_EVENTS, seed, sell_p)
    actual = replay_ledger(ledger, CONFIG, net_dividends=net_dividends)
    expected = _reference_replay(ledger, net_dividends=net_dividends)

    assert actual.cash_balance == pytest.approx(expected.cash_balance)
    _assert_frames_match("open_lots", actual.open_lots, expected.open_lots, ["symbol", "lot_id"])
    _assert_frames_match(
        "closed_lots", actual.closed_lots, expected.closed_lots, ["symbol", "lot_id", "closed_by_event_id"]
    )


def test_the_generated_ledgers_actually_exercise_every_event_type() -> None:
    # Without this the battery above could pass by never reaching a SPLIT or a
    # WITHHOLDING, which is the shape of hole PR H found in C9's own
    # equivalence battery: an input the fixture never produced.
    for sell_p in _SELL_PROBABILITIES:
        for seed in _SEEDS:
            ledger = _generated_ledger(_EVENTS, seed, sell_p)
            present = set(ledger["event_type"].unique().to_list())
            assert present == {"DEPOSIT", "BUY", "SELL", "DIVIDEND", "WITHHOLDING", "FEE", "SPLIT"}, (
                f"sell_p={sell_p} seed={seed} generated only {sorted(present)}"
            )


def test_the_low_sell_probability_really_does_accumulate_open_lots() -> None:
    # The regime the accumulator has the most state to get wrong in. If the
    # generator ever stops producing it, the battery above still passes and
    # stops testing what it is for.
    few = replay_ledger(_generated_ledger(_EVENTS, 1, 0.20), CONFIG)
    many = replay_ledger(_generated_ledger(_EVENTS, 1, 0.03), CONFIG)
    assert len(many.open_lots) > 2 * len(few.open_lots)
    assert not few.closed_lots.is_empty()
