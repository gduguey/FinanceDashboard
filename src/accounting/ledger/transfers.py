"""Suggest — never apply — internal transfers a rule doesn't already catch.

The generalized fallback `ACCOUNTING_PLAN.md` describes: two postings, on
two different real accounts, still pointing at a placeholder counterparty,
whose amounts are equal and opposite within a short window of each other,
are very likely one transfer between those two accounts. A heuristic match
like this can be wrong (two unrelated $20 charges a day apart), so it is
only ever surfaced as a suggestion for a human — or a new rule — to
confirm, never auto-applied the way `ledger.categorization.apply_rules`
applies an exact rule match.
"""

from __future__ import annotations

import polars as pl

_PLACEHOLDER_ACCOUNT_IDS = ["uncategorized:expense", "uncategorized:income"]
_AMOUNT_TOLERANCE = 1e-6
_CANDIDATE_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "account_id": pl.Utf8,
    "posting_id": pl.Utf8,
    "posted_at": pl.Datetime("us"),
    "description": pl.Utf8,
    "other_account_id": pl.Utf8,
    "other_posting_id": pl.Utf8,
    "other_posted_at": pl.Datetime("us"),
    "other_description": pl.Utf8,
    "amount": pl.Float64,
}


def find_unmatched_transfer_candidates(postings: pl.DataFrame, window_days: int = 3) -> pl.DataFrame:
    """Find pairs of still-unresolved postings that look like one transfer between two real accounts.

    Parameters
    ----------
    postings
        The full posting ledger.
    window_days
        How many days apart the two postings can be and still count as one transfer.

    Returns
    -------
    polars.DataFrame
        Columns `account_id`, `posting_id`, `posted_at`, `description`,
        `other_account_id`, `other_posting_id`, `other_posted_at`,
        `other_description`, `amount` — one row per candidate pair,
        `amount` signed from `account_id`'s side. The two description
        columns are for the caller to propose a `TransferRule` from (see
        `api.get_transfer_suggestions`), never read by the matching itself.
    """
    unresolved_transaction_ids = (
        postings.filter(pl.col("account_id").is_in(_PLACEHOLDER_ACCOUNT_IDS))["transaction_id"].unique().to_list()
    )
    candidates = postings.filter(
        pl.col("transaction_id").is_in(unresolved_transaction_ids)
        & ~pl.col("account_id").is_in(_PLACEHOLDER_ACCOUNT_IDS)
    ).select("posting_id", "transaction_id", "account_id", "posted_at", "description", "amount")
    if candidates.is_empty():
        return pl.DataFrame(schema=_CANDIDATE_SCHEMA)

    joined = candidates.join(candidates, how="cross", suffix="_other")
    matches = joined.filter(
        (pl.col("account_id") != pl.col("account_id_other"))
        & (pl.col("posting_id") < pl.col("posting_id_other"))
        & ((pl.col("amount") + pl.col("amount_other")).abs() < _AMOUNT_TOLERANCE)
        & ((pl.col("posted_at") - pl.col("posted_at_other")).abs() <= pl.duration(days=window_days))
    )
    return matches.select(
        "account_id",
        "posting_id",
        "posted_at",
        "description",
        other_account_id="account_id_other",
        other_posting_id="posting_id_other",
        other_posted_at="posted_at_other",
        other_description="description_other",
        amount="amount",
    ).sort("posted_at")
