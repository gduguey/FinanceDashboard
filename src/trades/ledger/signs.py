"""The one place a `trades` ledger event's *direction* is decided.

The two ledgers in this codebase disagree about where direction lives.
`accounting` signs the money: a posting's `amount` is negative when money
leaves and positive when it arrives, and every reader just sums the column.
`trades` stores an unsigned magnitude and puts direction in `event_type`, so
every reader that needs a signed number has to reconstruct one — and four
different places did, each with its own `pl.when(pl.col("event_type") == ...)`
expression, each independently able to drift (DB-audit move #1, "unify the
sign convention").

This module is the single translation point that removes that. It does not
change what is stored — the storage convention is fine, and rewriting eight
event types' worth of history to carry a sign would be a far larger change
than the problem warrants. It changes *where the sign is decided*: exactly
once, here, from `CASH_EFFECT_SIGN`. Downstream code asks for a signed
column and never mentions an event type to get one.

## The convention

`signed_cash_effect` is the **effect on the portfolio's cash balance**, which
is the only convention that makes the two ledgers speak the same language: a
signed number you can sum, exactly like `postings.amount`. Money arriving is
positive (`DEPOSIT`, `SELL`, `DIVIDEND`), money leaving is negative
(`WITHDRAWAL`, `BUY`, `WITHHOLDING`, `FEE`), and `SPLIT` moves no cash at
all, so it is zero rather than a special case every caller has to remember to
filter out.

Two callers want the *opposite* of that, and say so by negating it rather
than by re-deriving it:

- money-weighted return treats a deposit as a negative flow, because from the
  investor's point of view it is money paid in (`replay.external_cashflows`);
- a symbol's "money in" for the month is its net `BUY` minus `SELL`
  (`dashboard.charts`), which is cash out.

`signed_share_effect` is the same idea on the other axis — the effect on
*shares held*, which is the exact mirror of the cash effect for the only two
event types that move shares.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Mapping

    from trades.config import LedgerEventType

CASH_EFFECT_SIGN: Mapping[LedgerEventType, int] = {
    "DEPOSIT": 1,
    "WITHDRAWAL": -1,
    "BUY": -1,
    "SELL": 1,
    "DIVIDEND": 1,
    "WITHHOLDING": -1,
    "FEE": -1,
    "SPLIT": 0,
}
"""Which way each event type moves cash. The whole of this codebase's sign knowledge, in one table."""

SHARE_EFFECT_SIGN: Mapping[LedgerEventType, int] = {"BUY": 1, "SELL": -1}
"""Which way each event type moves the share count. Only `BUY`/`SELL` do; everything else is absent, not zero."""


def cash_effect(event_type: str, amount: float) -> float:
    """Signed effect of one event on the cash balance.

    The scalar counterpart to `signed_cash_effect`, for the one caller that
    walks the ledger row by row (`replay.replay_ledger`, a genuinely
    sequential fold).

    Parameters
    ----------
    event_type
        The event's `event_type`.
    amount
        The event's stored, unsigned magnitude.

    Returns
    -------
    float
        `amount` with its direction applied; `0.0` for an event that moves
        no cash.

    Raises
    ------
    ValueError
        If `event_type` is not one this module knows a direction for —
        which is the same thing as the ledger containing an event type
        nothing downstream could interpret.
    """
    # Keyed by `str`, not `LedgerEventType`: the caller has a column value
    # read back out of Postgres, and the whole point of this branch is to
    # reject one that turns out not to be a known event type.
    sign = dict(CASH_EFFECT_SIGN).get(event_type)  # type: ignore[call-overload]
    if sign is None:
        message = f"Unhandled ledger event_type: {event_type!r}"
        raise ValueError(message)
    return sign * amount


def _signed(signs: Mapping[LedgerEventType, int], magnitude: str) -> pl.Expr:
    """Build the polars expression applying `signs` to the `magnitude` column.

    Parameters
    ----------
    signs
        The direction table to apply.
    magnitude
        Name of the unsigned column to sign.

    Returns
    -------
    polars.Expr
    """
    return pl.col("event_type").replace_strict(dict(signs), default=0, return_dtype=pl.Int8) * pl.col(magnitude)


def signed_cash_effect(magnitude: str = "amount") -> pl.Expr:
    """Build the polars expression giving each row its signed cash effect.

    Parameters
    ----------
    magnitude
        Name of the unsigned amount column, `"amount"` unless a caller has
        renamed it.

    Returns
    -------
    polars.Expr
        Positive where money arrives, negative where it leaves, zero for
        an event that moves none.
    """
    return _signed(CASH_EFFECT_SIGN, magnitude)


def signed_share_effect(magnitude: str = "shares") -> pl.Expr:
    """Build the polars expression giving each row its signed change in shares held.

    Parameters
    ----------
    magnitude
        Name of the unsigned share-count column.

    Returns
    -------
    polars.Expr
        Positive for a `BUY`, negative for a `SELL`, zero for anything
        that moves no shares.
    """
    return _signed(SHARE_EFFECT_SIGN, magnitude)
