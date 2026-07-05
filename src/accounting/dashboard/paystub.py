"""Match a parsed paystub's deposits against the real bank postings that arrived on/near pay day.

Never forces a split that doesn't add up — an unmatched deposit is
reported, not silently guessed at, per `ACCOUNTING_PLAN.md` Phase 9: the
whole point of reconciling is to catch a paystub whose numbers don't
actually correspond to what landed in the bank, not to paper over that
with an assumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from accounting.models import Account, EarningsDeposit, EarningsStatement

_AMOUNT_TOLERANCE = 0.01


@dataclass(frozen=True)
class DepositMatch:
    """One statement deposit, and the real posting it was matched to (if any)."""

    deposit: EarningsDeposit
    posting_id: str | None
    account_id: str | None


@dataclass(frozen=True)
class ReconciliationResult:
    """Every statement deposit's match against a real posting, and whether all of them matched."""

    statement: EarningsStatement
    matches: list[DepositMatch]
    is_fully_matched: bool


def reconcile_earnings_statement(
    statement: EarningsStatement,
    postings: pl.DataFrame,
    accounts: dict[str, Account],
    tolerance_days: int = 3,
) -> ReconciliationResult:
    """Match each of a paystub's deposits against a real bank posting near pay day, by amount and account.

    Each candidate posting is matched to at most one deposit — greedily,
    in the statement's own deposit order — so two deposits that happen to
    share an amount don't both silently claim the same bank row.

    Parameters
    ----------
    statement
        The parsed paystub.
    postings
        The full, resolved posting ledger.
    accounts
        Every known account, keyed by `account_id` — used to match a
        deposit's `account_last4` against an account's own trailing digits.
    tolerance_days
        How many days before/after `statement.pay_date` to look for a matching deposit.

    Returns
    -------
    ReconciliationResult
    """
    window_start = statement.pay_date.date() - timedelta(days=tolerance_days)
    window_end = statement.pay_date.date() + timedelta(days=tolerance_days)
    candidates = postings.filter(
        (pl.col("posted_at").dt.date() >= window_start)
        & (pl.col("posted_at").dt.date() <= window_end)
        & (pl.col("amount") > 0)
    )
    candidate_rows = candidates.to_dicts()

    used_posting_ids: set[str] = set()
    matches: list[DepositMatch] = []
    for deposit in statement.deposits:
        matched_row = None
        for row in candidate_rows:
            if row["posting_id"] in used_posting_ids:
                continue
            if abs(row["amount"] - deposit.amount) > _AMOUNT_TOLERANCE:
                continue
            account = accounts.get(row["account_id"])
            if deposit.account_last4 and account and not account.account_id.endswith(f":{deposit.account_last4}"):
                continue
            matched_row = row
            break
        if matched_row is None:
            matches.append(DepositMatch(deposit=deposit, posting_id=None, account_id=None))
            continue
        used_posting_ids.add(matched_row["posting_id"])
        matches.append(
            DepositMatch(deposit=deposit, posting_id=matched_row["posting_id"], account_id=matched_row["account_id"])
        )

    return ReconciliationResult(
        statement=statement,
        matches=matches,
        is_fully_matched=all(match.posting_id is not None for match in matches),
    )
