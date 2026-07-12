"""Match a parsed paystub's deposits against the real bank postings that arrived on/near pay day.

Never forces a split that doesn't add up — an unmatched deposit is
reported, not silently guessed at: the whole point of reconciling is to
catch a paystub whose numbers don't actually correspond to what landed in
the bank, not to paper over that with an assumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from accounting.models import Account, EarningsDeposit, EarningsStatement

_AMOUNT_TOLERANCE = 0.01
_SALARY_CATEGORY_ID = "income:salary"
_REIMBURSEMENT_CATEGORY_ID = "income:reimbursement"
_REIMBURSEMENT_SUBCATEGORY_ID = "income:reimbursement:employer"


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


@dataclass(frozen=True)
class ProposedSplitLeg:
    """One leg of a proposed split — the same shape `PostingSplitLeg` needs, before a user has confirmed it."""

    amount: float
    category_id: str | None
    subcategory_id: str | None
    description: str


@dataclass(frozen=True)
class ProposedSplit:
    """A proposed way to categorize one matched deposit — one or more legs, always summing to the deposit's amount.

    A single-leg proposal (no reimbursement landed in this particular
    deposit) isn't really a "split" — the caller applies it as a plain
    category override instead of `PostingSplit`, which requires at least two legs.
    """

    posting_id: str
    account_id: str
    legs: list[ProposedSplitLeg]


def propose_posting_splits(statement: EarningsStatement, matches: list[DepositMatch]) -> list[ProposedSplit]:
    """Propose splitting each matched deposit into salary vs. reimbursement legs.

    A paystub names its reimbursement line items and its net pay, but
    never *which* deposit each reimbursement rode along with when pay is
    split across several accounts — so this assigns reimbursement lines
    to the largest matched deposit(s) first (a specific reimbursement
    typically lands whole in one account, not spread across several,
    the same way this employee's own paystub shows $1,188.76 of
    reimbursements riding entirely on the larger of two deposits) and
    treats whatever's left of each deposit as salary. Always just a
    starting point — the user reviews and can re-edit every leg before
    applying anything.

    Parameters
    ----------
    statement
        The parsed paystub.
    matches
        `reconcile_earnings_statement`'s per-deposit matches; unmatched deposits are skipped.

    Returns
    -------
    list[ProposedSplit]
        One per matched deposit, largest first.
    """
    remaining_lines = sorted(statement.reimbursement_lines, key=lambda line: line.amount, reverse=True)
    matched = sorted(
        (match for match in matches if match.posting_id is not None and match.account_id is not None),
        key=lambda match: match.deposit.amount,
        reverse=True,
    )

    proposals = []
    for match in matched:
        posting_id = match.posting_id
        account_id = match.account_id
        if posting_id is None or account_id is None:  # narrows for mypy; filtered above
            continue
        deposit_remaining = match.deposit.amount
        # Checked one at a time against the *shrinking* remainder — a
        # plain "does this line individually fit" filter would double-book
        # the deposit if two lines each fit alone but not together.
        assigned_lines = []
        for line in list(remaining_lines):
            if line.amount <= deposit_remaining + _AMOUNT_TOLERANCE:
                assigned_lines.append(line)
                remaining_lines.remove(line)
                deposit_remaining -= line.amount

        legs = [
            ProposedSplitLeg(
                amount=line.amount,
                category_id=_REIMBURSEMENT_CATEGORY_ID,
                subcategory_id=_REIMBURSEMENT_SUBCATEGORY_ID,
                description=line.label,
            )
            for line in assigned_lines
        ]
        if deposit_remaining > _AMOUNT_TOLERANCE or not legs:
            legs.append(
                ProposedSplitLeg(
                    amount=max(deposit_remaining, 0.0),
                    category_id=_SALARY_CATEGORY_ID,
                    subcategory_id=None,
                    description="Salary",
                )
            )
        proposals.append(ProposedSplit(posting_id=posting_id, account_id=account_id, legs=legs))
    return proposals
