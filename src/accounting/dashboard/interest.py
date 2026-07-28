"""Interest-bearing account view: realized interest income per account, its APY vs. a benchmark, and a projection.

Every fact this aggregates already exists elsewhere in the module —
`apy_pct` on `Account.meta` (set by whichever importer last saw a
statement carrying a rate block), and
interest itself as ordinary `Posting` rows already categorized under the
"Interest Earned" income category. This is purely a read: no new fact is
stored here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import polars as pl

from accounting.ledger.replay import account_balances

if TYPE_CHECKING:
    from datetime import date

    from accounting.models import Account

INTEREST_EARNED_CATEGORY_ID = "income:interest-earned"
_INTEREST_BEARING_KINDS = {"savings", "vault"}


@dataclass(frozen=True)
class InterestAccountRow:
    """One interest-bearing account's realized income, current rate, and a one-year forward projection."""

    account_id: str
    account_name: str
    currency: str
    apy_pct: float
    interest_earned_this_year: float
    current_balance: float
    projected_next_12_months: float
    benchmark_apy_pct: float | None


def interest_summary(
    postings: pl.DataFrame,
    accounts: dict[str, Account],
    as_of: date,
    benchmark_apy_pct: float | None = None,
) -> list[InterestAccountRow]:
    """Every savings/vault account's year-to-date interest, current APY, balance, and a one-year projection.

    The projection is deliberately simple — this year's balance held flat
    at the account's current APY for a year, not a compounding schedule
    (that's what the Phase 7 simulator is for) — since it exists to answer
    "roughly how much will this vault earn me," not to model contributions
    or compounding frequency.

    Parameters
    ----------
    postings
        The full, resolved posting ledger.
    accounts
        Every known account, keyed by `account_id`.
    as_of
        The date to value balances as of, and the end of the "this year" window.
    benchmark_apy_pct
        A reference rate (e.g. the published HYSA rate `trades` tracks) to
        compare each account's own APY against; `None` if unavailable.

    Returns
    -------
    list[InterestAccountRow]
        One row per savings/vault account, sorted by name.
    """
    balances = cast("pl.DataFrame", account_balances(postings, as_of))
    balance_by_account = (
        dict(zip(balances["account_id"].to_list(), balances["balance"].to_list(), strict=True))
        if not balances.is_empty()
        else {}
    )

    year_start = as_of.replace(month=1, day=1)
    interest_rows = postings.filter(
        (pl.col("category_id") == INTEREST_EARNED_CATEGORY_ID)
        & (pl.col("posted_at").dt.date() >= year_start)
        & (pl.col("posted_at").dt.date() <= as_of)
    )
    interest_by_account = {}
    if not interest_rows.is_empty():
        interest_totals = interest_rows.group_by("account_id").agg(interest=pl.col("amount").sum())
        interest_by_account = dict(
            zip(interest_totals["account_id"].to_list(), interest_totals["interest"].to_list(), strict=True)
        )

    rows = []
    for account in accounts.values():
        if account.kind not in _INTEREST_BEARING_KINDS:
            continue
        apy_pct = float(account.meta.get("apy_pct") or 0.0)
        balance = balance_by_account.get(account.account_id, 0.0)
        rows.append(
            InterestAccountRow(
                account_id=account.account_id,
                account_name=account.name,
                currency=account.currency,
                apy_pct=apy_pct,
                interest_earned_this_year=interest_by_account.get(account.account_id, 0.0),
                current_balance=balance,
                projected_next_12_months=balance * (apy_pct / 100),
                benchmark_apy_pct=benchmark_apy_pct,
            )
        )
    return sorted(rows, key=lambda row: row.account_name)
