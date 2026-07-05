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


def account_balances_over_time(postings: pl.DataFrame | pl.LazyFrame, dates: list[date]) -> pl.DataFrame | pl.LazyFrame:
    """Every account's running balance as of each requested date — the per-account net worth history series.

    One vectorized as-of join rather than calling `account_balances` once
    per date (which would be `len(dates)` full-ledger filter+group-bys): a
    per-account daily cumulative sum, cross-joined against every requested
    date, then backward-filled to the latest cumulative value at or before
    that date. An account with no postings yet on a given date gets 0, not
    a missing row.

    Parameters
    ----------
    postings
        The full posting ledger.
    dates
        Every date a balance is needed for.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        Columns `account_id`, `date`, `balance`, `currency`. Same type as `postings`.
    """
    was_eager = isinstance(postings, pl.DataFrame)
    lazy = postings.lazy().with_columns(posted_date=pl.col("posted_at").dt.date())
    per_day = (
        lazy
        .group_by("account_id", "posted_date")
        .agg(day_amount=pl.col("amount").sum(), currency=pl.col("currency").first())
        .sort("account_id", "posted_date")
        .with_columns(balance=pl.col("day_amount").cum_sum().over("account_id"))
    )
    account_currency = per_day.group_by("account_id").agg(currency=pl.col("currency").first())
    grid = per_day.select("account_id").unique().join(pl.LazyFrame({"date": sorted(dates)}), how="cross")
    result = (
        grid
        .sort("account_id", "date")
        .join_asof(
            per_day.select("account_id", "posted_date", "balance").sort("account_id", "posted_date"),
            left_on="date",
            right_on="posted_date",
            by="account_id",
            strategy="backward",
        )
        .drop("posted_date")
        .with_columns(pl.col("balance").fill_null(0.0))
        .join(account_currency, on="account_id", how="left")
    )
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
