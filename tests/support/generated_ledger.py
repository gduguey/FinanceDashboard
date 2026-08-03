"""A synthetic `trades` event ledger, for tests that need one bigger than a literal.

Shared by two gates that have to measure and verify the same shape of
input: `tests/trades/ledger/test_replay_equivalence.py`, which replays
these against the implementation `LotBook` replaced, and
`tests/performance/test_replay_scaling.py`, which asserts the cost of
replaying them stays linear in their length. A generator per gate would let
the verified shape and the measured shape drift apart, which is the whole
value of having both.

The event mix is buy-heavy the way a real brokerage ledger is, and
`sell_p` is the knob that matters: it decides how fast the open-lot book is
consumed relative to how fast it fills, and therefore how much state a
replay is holding. At 0.20 lots stay few; at 0.03 they accumulate over the
whole history, which is the regime a buy-and-hold ledger is actually in.

Every event type `ledger.signs` knows a direction for is produced, checked
by a test rather than assumed — see
`test_the_generated_ledgers_actually_exercise_every_event_type`.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from functools import cache

import polars as pl

SYMBOLS = ("AAPL", "MSFT", "VTI", "SPY", "NVDA", "GOOG", "AMZN", "TSLA")
"""Eight symbols, so a replay holds eight books rather than one long one."""

_START = datetime(2015, 1, 2, 10, 0)  # noqa: DTZ001 — a ledger event_datetime is naive, and the replay compares them
_OPENING_DEPOSIT = 5_000_000.0
"""Large enough that no generated `BUY` can be refused for want of cash."""

_BUY_THRESHOLD = 0.55
"""Below this a roll is a `BUY`, which is what makes the mix buy-heavy."""

_DIVIDEND_THRESHOLD = 0.93
"""Between the sell band and this, a roll is a `DIVIDEND` — the event the accumulator exists for."""

_WITHHOLDING_THRESHOLD = 0.97
"""Above `_DIVIDEND_THRESHOLD` and below this, a `WITHHOLDING`, so `net_dividends=True` has something to net."""


def _row(event_id: str, when: datetime, symbol: str, event_type: str, **fields: object) -> dict:
    """One ledger row, with every column present so `pl.DataFrame` infers one schema for the lot.

    Returns
    -------
    dict
    """
    return {
        "event_id": event_id,
        "event_datetime": when,
        "symbol": symbol,
        "event_type": event_type,
        "shares": fields.get("shares"),
        "price": fields.get("price"),
        "amount": fields.get("amount", 0.0),
        "currency": "USD",
        "meta": fields.get("meta") or {},
    }


@cache
def generated_ledger(
    n_events: int, seed: int, sell_p: float, *, sell_shares: float | None = None, split_p: float = 0.005
) -> pl.DataFrame:
    """Build a chronological ledger of `n_events` events.

    Cached on its arguments: callers replay the same ledger more than once
    — twice per `net_dividends` value in the equivalence battery, several
    times per measurement in the scaling gate — and generating one costs
    more than replaying it now does.

    Parameters
    ----------
    n_events
        How many events to produce, including the opening deposit.
    seed
        Seeds the generator, so a given call always returns the same ledger.
    sell_p
        Probability that an event which could be a `SELL` is one.
    sell_shares
        Shares per `SELL`. `None`, the default, sells a random slice of up
        to a third of the position, which makes the open-lot book
        **self-limiting**: sells scale with holdings, so the book stops
        growing and stays roughly the same size however long the ledger is.
        A fixed number instead lets the book grow in proportion to the
        ledger, which is the shape a real buy-and-hold ledger has and the
        only shape in which a per-event cost proportional to the book is
        visible at all (see `tests/performance/test_replay_scaling.py`).
    split_p
        Probability of a `SPLIT`. Settable to zero because a `SPLIT` is
        inherently O(open lots) — every lot's share count and cost changes
        — so a ledger whose book grows *and* splits at a fixed rate per
        event cannot be replayed in linear time by any implementation.

    Returns
    -------
    polars.DataFrame
        `brokers.ibkr.main.load_ledger`'s columns, oldest event first.
    """
    rng = random.Random(seed)  # noqa: S311 — a reproducible ledger, not a secret
    held = dict.fromkeys(SYMBOLS, 0.0)
    rows: list[dict] = [_row("e0", _START, "CASH", "DEPOSIT", amount=_OPENING_DEPOSIT)]
    day = 0
    i = 0
    split_threshold = 1.0 - split_p
    while len(rows) < n_events:
        day += rng.choice([0, 0, 1, 1, 2, 3])
        when = _START + timedelta(days=day, minutes=i % 300)
        symbol = rng.choice(SYMBOLS)
        roll = rng.random()
        i += 1
        event_id = f"e{i}"
        if roll < _BUY_THRESHOLD:
            shares = float(rng.randint(1, 40))
            held[symbol] += shares
            rows.append(_row(event_id, when, symbol, "BUY", shares=shares, price=50.0 + rng.random() * 300))
        elif roll < _BUY_THRESHOLD + sell_p and held[symbol] > max(1.0, sell_shares or 0.0):
            shares = sell_shares if sell_shares is not None else float(rng.randint(1, max(1, int(held[symbol] // 3))))
            held[symbol] -= shares
            rows.append(_row(event_id, when, symbol, "SELL", shares=shares, price=50.0 + rng.random() * 300))
        elif roll < _DIVIDEND_THRESHOLD:
            rows.append(_row(event_id, when, symbol, "DIVIDEND", amount=rng.random() * 200))
        elif roll < _WITHHOLDING_THRESHOLD:
            rows.append(_row(event_id, when, symbol, "WITHHOLDING", amount=rng.random() * 30))
        elif roll < split_threshold:
            rows.append(_row(event_id, when, "CASH", "FEE", amount=rng.random() * 5))
        else:
            held[symbol] *= 2
            rows.append(_row(event_id, when, symbol, "SPLIT", meta={"ratio": 2.0}))
    metas = [row.pop("meta") for row in rows]
    return pl.DataFrame(rows).with_columns(meta=pl.Series("meta", metas, dtype=pl.Object))
