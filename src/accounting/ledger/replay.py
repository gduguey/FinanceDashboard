"""Replay postings into account balances, and check the zero-sum invariant every transaction must satisfy.

Unlike `trades.ledger.replay`, this isn't a sequential fold — a posting's
effect on its account's balance doesn't depend on any other posting, so the
whole thing is one `group_by`/`sum`, not a `for` loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from datetime import date

_ZERO_SUM_TOLERANCE = 1e-6  # floating-point slack for a transaction's postings summing to zero


def account_balances(postings: pl.DataFrame | pl.LazyFrame, as_of: date | None = None) -> pl.DataFrame | pl.LazyFrame:
    """Sum posting amounts per account, optionally truncated to a date.

    Parameters
    ----------
    postings
        The full posting ledger.
    as_of
        Only count postings on or before this date; `None` means every posting.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        Columns `account_id`, `balance`, `currency`. Same type as `postings`.
    """
    was_eager = isinstance(postings, pl.DataFrame)
    lazy = postings.lazy()
    if as_of is not None:
        lazy = lazy.filter(pl.col("posted_at").dt.date() <= as_of)
    result = lazy.group_by("account_id").agg(balance=pl.col("amount").sum(), currency=pl.col("currency").first())
    return result.collect() if was_eager else result


def unbalanced_transactions(postings: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
    """Find every transaction whose postings don't sum to zero within one currency.

    A transaction spanning more than one currency isn't checked here — that
    needs the stored exchange rate from `meta`, not a plain sum — so this
    only ever flags a same-currency imbalance, which is always a bug (a
    missed counterparty leg, a bad import) rather than a legitimate
    cross-currency transfer.

    Parameters
    ----------
    postings
        The full posting ledger.

    Returns
    -------
    polars.DataFrame
        Columns `transaction_id`, `currency`, `total` — one row per
        (transaction, currency) combination whose postings don't sum to
        zero. Empty if every transaction balances.
    """
    rows = postings.lazy() if isinstance(postings, pl.DataFrame) else postings
    return (
        rows
        .group_by("transaction_id", "currency")
        .agg(total=pl.col("amount").sum())
        .filter(pl.col("total").abs() > _ZERO_SUM_TOLERANCE)
        .collect()
    )


def validate_balanced(postings: pl.DataFrame | pl.LazyFrame) -> None:
    """Raise if any transaction's same-currency postings don't sum to zero.

    Parameters
    ----------
    postings
        The full posting ledger.

    Raises
    ------
    ValueError
        If one or more transactions are unbalanced, naming the first offender.
    """
    offenders = unbalanced_transactions(postings)
    if offenders.is_empty():
        return
    row = offenders.row(0, named=True)
    message = (
        f"Transaction {row['transaction_id']!r} postings sum to {row['total']} {row['currency']}, not zero "
        f"({len(offenders)} unbalanced transaction(s) total)."
    )
    raise ValueError(message)
