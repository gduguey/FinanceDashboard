"""FIFO tax-lot tracking.

Each `BUY` opens a lot; each `SELL` closes lots oldest-first, tagging
realized gain and holding-period term. Pure logic, no I/O and no
ledger-walking — that is `replay.py`, which drives one `LotBook` per
symbol through the ledger.

`LotBook` is the whole of that logic, and it is a *stateful* object on
purpose. The three operations a replay performs — open, consume
oldest-first, adjust for a split — used to be free functions that each
took a `list[Lot]` and returned a new one, rebuilt through a polars
frame. That reads well and is quadratic: every event copied every lot the
symbol had, so a ledger with N events over one symbol did O(N²) work
(item C4b). A `deque` and a per-share dividend accumulator do the same
arithmetic in O(1) amortized per event, which is what makes a replay
cheap enough to run per request.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal

import polars as pl

if TYPE_CHECKING:
    from datetime import datetime

_SHORTFALL_TOLERANCE = 1e-9
"""How far short of a requested sale the book may be before it is an error rather than float drift.

Share counts are `float64` and reach a lot after any number of splits, so
"exactly enough shares" is not a bit-exact comparison. Carried over
unchanged from the version this replaced.
"""

_RESIDUAL_TOLERANCE = 1e-12
"""When the shares still to consume are close enough to zero to stop.

Tighter than `_SHORTFALL_TOLERANCE` because it guards a loop rather than a
raise: it only has to be below the smallest remainder worth opening a
`ClosedLot` for.
"""

_DUST_SHARES = 1e-9
"""Below this, a book's total share count is float residue rather than a holding.

Not a share count anyone can own — a fractional-share broker quotes six
decimals — so the only way under it is a partial close whose requested
shares missed the lot's own by rounding, which leaves a remainder of order
`1e-13` open. `accrue_dividend` refuses to divide by such a total, because
doing so would put a permanently enormous number into `divps`.
"""


@dataclass(frozen=True)
class Lot:
    """One open (or partially closed) tax lot.

    `dividends_received` accrues while the lot is open (see
    `replay.replay_ledger`): each `DIVIDEND` on the lot's symbol is split
    across every lot open at that moment, in proportion to shares held.
    """

    lot_id: str
    symbol: str
    opened_at: datetime
    shares: float
    cost_per_share: float
    dividends_received: float = 0.0

    polars_schema: ClassVar[dict[str, type[pl.DataType] | pl.DataType]] = {
        "lot_id": pl.Utf8,
        "symbol": pl.Utf8,
        "opened_at": pl.Datetime("us"),
        "shares": pl.Float64,
        "cost_per_share": pl.Float64,
        "dividends_received": pl.Float64,
    }


@dataclass(frozen=True)
class ClosedLot:
    """The portion of a lot consumed by one `SELL` (or other closing event).

    `dividends_received` is the closed portion's share of whatever the
    parent lot had accrued so far, split by `shares / (shares + the
    portion that stayed open)` — see `LotBook.consume_fifo`.
    """

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
    dividends_received: float = 0.0

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
        "dividends_received": pl.Float64,
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


@dataclass(slots=True)
class _OpenLot:
    """One open lot while a `LotBook` owns it: mutable, and its dividend total derived rather than stored.

    `banked_dividends` plus `divps_at_open` reconstruct
    `Lot.dividends_received` at any moment (see `LotBook.accrued`). Storing
    the running total instead would mean touching every lot on every
    `DIVIDEND`, which is one of the two things that made the old
    list-rebuilding version quadratic.
    """

    lot_id: str
    opened_at: datetime
    shares: float
    cost_per_share: float
    banked_dividends: float = 0.0
    divps_at_open: float = 0.0


@dataclass(slots=True)
class LotBook:
    """One symbol's open lots, oldest first, walked in O(1) amortized work per event.

    Two representation choices carry the whole speed difference against the
    list-rebuilding version this replaced.

    **A `deque`, kept sorted by `opened_at`.** `consume_fifo` takes from the
    left and never sorts, because the book is already in FIFO order: events
    arrive chronologically, so an append is in order by construction. The
    old version sorted the symbol's whole lot list on every single `SELL`.

    **A dividend-per-share accumulator.** A `DIVIDEND` adds
    `amount / total_shares` to `divps` and touches no lot at all; a lot's
    accrued total is `banked_dividends + shares x (divps - divps_at_open)`.
    Whenever a lot's `shares` changes — a partial close, a split — its
    accrual to that point is banked and its baseline re-pinned, so the
    identity holds across every share-count change. The old version
    rewrote every open lot of the symbol on every `DIVIDEND`.

    Two operations are still O(open lots), and both are meant to be:
    `apply_split`, because a split genuinely changes every lot; and a
    dividend paid to a book holding only float dust, where dividing by the
    total would poison the accumulator for good (see `accrue_dividend`).
    Neither is reachable often enough to matter — a split is rare and dust
    needs a sale that missed a lot's own share count by rounding.

    The arithmetic is unchanged and asserted to be: see
    `tests/trades/ledger/test_replay_equivalence.py`, which replays
    generated ledgers against an independent list-and-frame implementation
    of the same rules.
    """

    symbol: str
    lots: deque[_OpenLot] = field(default_factory=deque)
    total_shares: float = 0.0
    divps: float = 0.0
    """Cumulative dividend paid per share held, over the whole life of the book."""

    def accrued(self, lot: _OpenLot) -> float:
        """Return what one of this book's lots has accrued in dividends so far.

        Parameters
        ----------
        lot
            A lot currently held by this book.

        Returns
        -------
        float
            The lot's `dividends_received` as of the last event applied.
        """
        return lot.banked_dividends + lot.shares * (self.divps - lot.divps_at_open)

    def open(self, lot_id: str, opened_at: datetime, shares: float, cost_per_share: float) -> None:
        """Open a lot, keeping the book in oldest-first order.

        Parameters
        ----------
        lot_id
            The ledger event id of the `BUY` that opened it.
        opened_at
            When it was opened.
        shares
            Shares bought.
        cost_per_share
            Price paid per share.
        """
        lot = _OpenLot(
            lot_id=lot_id,
            opened_at=opened_at,
            shares=shares,
            cost_per_share=cost_per_share,
            divps_at_open=self.divps,
        )
        if self.lots and opened_at < self.lots[-1].opened_at:
            # Not reached on a production read: `brokers.ibkr.main._ledger_query`
            # orders by `event_datetime` in SQL, so an append is already in
            # order. Kept because the version this replaced sorted on every
            # `SELL` and therefore matched oldest-first even against an
            # out-of-order ledger, and losing that quietly would be a
            # behaviour change rather than a speedup.
            at = len(self.lots)
            while at > 0 and self.lots[at - 1].opened_at > opened_at:
                at -= 1
            self.lots.insert(at, lot)
        else:
            self.lots.append(lot)
        self.total_shares += shares

    def _bank_and_rebase(self) -> None:
        """Freeze every lot's accrual so far and re-pin its baseline, leaving `divps` free to reset.

        O(open lots), so it is only called where the accumulator cannot be
        used: on a book whose `total_shares` is dust (see
        `accrue_dividend`).
        """
        for lot in self.lots:
            lot.banked_dividends = self.accrued(lot)
            lot.divps_at_open = self.divps

    def accrue_dividend(self, amount: float) -> None:
        """Split a dividend across every lot currently open, in proportion to shares held.

        Recorded as one addition to `divps` rather than a write per lot; a
        book holding no shares accrues nothing, since there is nobody to
        split it across.

        **The one case the accumulator cannot express** is a book holding
        dust: a partial close whose `shares_to_consume` misses the lot's own
        `shares` by float noise leaves a remainder of order `1e-13`, and
        `amount / 1e-13` would put a number 13 orders of magnitude too large
        into `divps` — permanently, since it only ever grows. Every lot
        opened afterwards would then derive its total from the difference of
        two enormous floats, and a later ordinary dividend would be lost to
        cancellation. So that case splits the dividend per lot and rebases,
        which is what the implementation this replaced did on every
        dividend: the same arithmetic, at O(open lots), on a book that holds
        almost nothing.

        Parameters
        ----------
        amount
            The dividend amount to split.
        """
        if self.total_shares <= 0.0:
            return
        if self.total_shares < _DUST_SHARES:
            held = self.total_shares
            self._bank_and_rebase()
            for lot in self.lots:
                lot.banked_dividends += amount * lot.shares / held
            return
        self.divps += amount / self.total_shares

    def consume_fifo(
        self,
        shares_to_consume: float,
        exit_price: float,
        closed_at: datetime,
        closed_by_event_id: str,
        long_term_holding_days: int,
    ) -> list[ClosedLot]:
        """Consume shares oldest-`opened_at`-first, closing lots as it goes.

        `realized_gain = shares_consumed x (exit_price - cost_per_share)`. A
        commission is never part of it: it arrives as its own `FEE` event
        (`ibkr:{transactionID}:fee`), precisely so it neither inflates a
        lot's cost basis nor gets buried inside a realized gain.

        Each consumed lot's accrued `dividends_received` splits by shares:
        the closed portion keeps `accrued x consumed / shares` and a
        partially closed lot banks the rest, so a lot that shrinks keeps a
        proportionally shrunk dividend total rather than its full pre-sale
        amount.

        Parameters
        ----------
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

        Returns
        -------
        list[ClosedLot]
            One entry per lot touched, in the order they were consumed.

        Raises
        ------
        ValueError
            If the book doesn't hold enough shares to satisfy `shares_to_consume`.
        """
        if self.total_shares < shares_to_consume - _SHORTFALL_TOLERANCE:
            message = (
                f"Cannot consume {shares_to_consume} shares — only {self.total_shares} available across open lots."
            )
            raise ValueError(message)

        closed: list[ClosedLot] = []
        remaining = shares_to_consume
        while remaining > _RESIDUAL_TOLERANCE and self.lots:
            lot = self.lots[0]
            take = min(lot.shares, remaining)
            accrued = self.accrued(lot)
            consumed_dividends = accrued * take / lot.shares if lot.shares else 0.0
            held_days = (closed_at - lot.opened_at).days
            closed.append(
                ClosedLot(
                    lot_id=lot.lot_id,
                    symbol=self.symbol,
                    opened_at=lot.opened_at,
                    closed_at=closed_at,
                    shares=take,
                    cost_per_share=lot.cost_per_share,
                    exit_price=exit_price,
                    realized_gain=take * (exit_price - lot.cost_per_share),
                    term="LONG" if held_days >= long_term_holding_days else "SHORT",
                    closed_by_event_id=closed_by_event_id,
                    dividends_received=consumed_dividends,
                )
            )
            if take >= lot.shares:
                self.lots.popleft()
            else:
                lot.banked_dividends = accrued - consumed_dividends
                lot.divps_at_open = self.divps
                lot.shares -= take
            self.total_shares -= take
            remaining -= take
        if not self.lots:
            # An emptied book holds no baseline, so both running figures go
            # with the last lot. `divps` is the one that matters: it only ever
            # grows, so a book that fills again would otherwise hand every new
            # lot a baseline inherited from a position nobody holds any more,
            # and the difference of two large floats is where a small later
            # dividend gets lost. `total_shares` is defensive — `take` is
            # clamped to the lot, so the loop can never subtract more than was
            # added, but the two run in different orders and float addition
            # does not re-associate exactly.
            self.total_shares = 0.0
            self.divps = 0.0
        return closed

    def apply_split(self, ratio: float) -> None:
        """Multiply every open lot's shares by `ratio` and divide its cost per share by the same.

        The one operation that unavoidably touches every open lot, which is
        fine: a split is rare, where a `DIVIDEND` is not.

        Parameters
        ----------
        ratio
            The split ratio (e.g. 2.0 for a 2-for-1 split).
        """
        for lot in self.lots:
            lot.banked_dividends = self.accrued(lot)
            lot.divps_at_open = self.divps
            lot.shares *= ratio
            lot.cost_per_share /= ratio
        self.total_shares *= ratio

    def open_lots(self) -> list[Lot]:
        """Materialize the book's open lots, oldest first, with their dividend totals resolved.

        Returns
        -------
        list[Lot]
            One frozen `Lot` per open lot.
        """
        return [
            Lot(
                lot_id=lot.lot_id,
                symbol=self.symbol,
                opened_at=lot.opened_at,
                shares=lot.shares,
                cost_per_share=lot.cost_per_share,
                dividends_received=self.accrued(lot),
            )
            for lot in self.lots
        ]
