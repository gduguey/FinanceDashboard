"""Suggest — and, for a rule with a unique match, safely confirm — internal transfers between two real accounts.

The generalized fallback (`find_unmatched_transfer_candidates`): two
postings, on two different real accounts, still pointing at a placeholder
counterparty, whose amounts are equal and opposite within a short window
of each other, are very likely one transfer between those two accounts. A
heuristic match like this can be wrong (two unrelated $20 charges a day
apart), so it is only ever surfaced as a suggestion for a human to confirm
via a `TransferLink` (see `make_transfer_link`), never auto-applied the way
`ledger.categorization.apply_rules` applies an exact rule match.

`reconcile_rule_links` is the same heuristic put to a narrower, safer use:
a `TransferRule` whose counterparty is an `IMPORTABLE_ACCOUNT_KINDS`
account (one that might already have its own independently-imported
posting for the same event — see `apply_rules`'s own docstring) can't
safely repoint a placeholder directly. Instead, once the rule's own
description/account match narrows things down to one specific transaction,
this looks for a *unique* matching transaction on the counterparty account
and proposes a `TransferLink` for it — the same "refuse to guess when it's
ambiguous" contract `find_unmatched_transfer_candidates` already has,
just scoped by a rule instead of offered to a human to pick from.

Unlike every other function in `ledger`, this one's result needs to be
*persisted* (in `transfer_links`), not recomputed
fresh on every read: a live candidate search run inside a date-scoped
dashboard call would silently disagree with the same transaction's status
on the unscoped Transactions page, and a transaction's transfer status
could retroactively flip after some unrelated future import with no
visible signal. So `reconcile_rule_links` itself stays pure (proposes,
never writes) — it's meant to be called, and its result persisted, at
controlled write points only (import, ledger rebuild, rule save; see
`accounting.importers.ingest` and `accounting.api.routers.store`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from accounting.ledger.categorization import real_legs_of_two_leg_transactions, rule_matches_by_transaction
from accounting.models import IMPORTABLE_ACCOUNT_KINDS, TransferLink
from accounting.repositories.interpretation import (
    insert_transfer_links,
    load_posting_splits,
    load_transfer_links,
    load_transfer_rules,
)
from accounting.taxonomy import seeded_accounts
from db.session import set_rls_user

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from accounting.models import Account, PostingSplit, TransferLinkSource, TransferRule

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


def make_transfer_link(
    transaction_id_a: str, transaction_id_b: str, source: TransferLinkSource = "manual", rule_id: str | None = None
) -> TransferLink:
    """Build a `TransferLink` with a canonical (sorted) id and column order.

    Sorting the pair once, and using that same order both for the derived
    `link_id` and for which transaction lands in `transaction_id_a` vs
    `transaction_id_b`, is what makes confirming (or looking up) the same
    real-world pair idempotent regardless of which side a caller names
    first — `make_transfer_link("x", "y")` and `make_transfer_link("y",
    "x")` are the exact same link.

    Parameters
    ----------
    transaction_id_a
        One side of the transfer.
    transaction_id_b
        The other side.
    source
        `"manual"` for a user's own pick, `"rule"` for one
        `reconcile_rule_links` found — display-only.
    rule_id
        Which `TransferRule` found this link, when `source == "rule"` —
        display-only, see `models.TransferLink`'s own docstring.

    Returns
    -------
    TransferLink
    """
    first, second = sorted((transaction_id_a, transaction_id_b))
    return TransferLink(
        link_id=f"transfer-link:{first}:{second}",
        transaction_id_a=first,
        transaction_id_b=second,
        source=source,
        rule_id=rule_id,
    )


def find_unmatched_transfer_candidates(
    postings: pl.DataFrame, window_days: int = 3, existing_links: list[TransferLink] | None = None
) -> pl.DataFrame:
    """Find pairs of still-unresolved postings that look like one transfer between two real accounts.

    Parameters
    ----------
    postings
        The full posting ledger.
    window_days
        How many days apart the two postings can be and still count as one transfer.
    existing_links
        Every already-confirmed `TransferLink` — a transaction that's
        already part of one is never offered again, since linking a
        transaction never removes its placeholder leg (unlike a
        `TransferRule`'s direct repoint), so without this it would
        otherwise resurface as a candidate forever.

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
    linked_transaction_ids = {
        transaction_id
        for link in (existing_links or [])
        for transaction_id in (link.transaction_id_a, link.transaction_id_b)
    }
    unresolved_transaction_ids = (
        postings.filter(pl.col("account_id").is_in(_PLACEHOLDER_ACCOUNT_IDS))["transaction_id"].unique().to_list()
    )
    candidates = postings.filter(
        pl.col("transaction_id").is_in(unresolved_transaction_ids)
        & ~pl.col("account_id").is_in(_PLACEHOLDER_ACCOUNT_IDS)
        & ~pl.col("transaction_id").is_in(list(linked_transaction_ids))
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


def reconcile_rule_links(
    postings: pl.DataFrame | pl.LazyFrame,
    rules: list[TransferRule],
    accounts: dict[str, Account],
    posting_splits: dict[str, PostingSplit],
    existing_links: list[TransferLink],
    window_days: int = 3,
) -> list[TransferLink]:
    """Propose a `TransferLink` for every rule match that safely resolves to exactly one candidate transaction.

    Mirrors `apply_rules`'s own matching (`ledger.categorization.rule_matches_by_transaction`)
    to find which transaction a rule would resolve, then — only for a rule
    whose counterparty is an `IMPORTABLE_ACCOUNT_KINDS` account, the case
    `apply_rules` itself refuses to repoint directly — looks for a unique,
    still-unresolved posting on that counterparty account with an opposite
    amount inside `window_days`. Proposes nothing (leaving the transaction
    on its placeholder) when that search finds zero or more than one
    candidate, or when either side already has a `PostingSplit` (see
    `models.PostingSplit` — a split real leg must never be silently
    excluded from income/expense by a link formed afterward) or is already
    part of a `TransferLink`.

    Finding candidates is one vectorized pass over the ledger (a cross-join,
    the same shape `find_unmatched_transfer_candidates` already uses); only
    the final tie-break — a transaction can't claim a candidate another,
    earlier-processed transaction already claimed — stays a plain Python
    loop, deliberately: it runs over the already-small set of transactions
    a rule actually matches, never the full ledger, and that exclusivity
    is inherently sequential (which pairing "wins" when two matches are
    both only unique before either has claimed anything).

    Parameters
    ----------
    postings
        The full posting ledger.
    rules
        User-maintained trigger/action rules.
    accounts
        Every known account, keyed by `account_id`.
    posting_splits
        Every persisted split, keyed by the original `posting_id`.
    existing_links
        Every already-confirmed `TransferLink` — neither side of one is
        ever re-proposed.
    window_days
        How many days apart the two transactions can be and still count as one transfer.

    Returns
    -------
    list[TransferLink]
        Newly proposed links only — never re-derives `existing_links`
        themselves. Not persisted here; the caller (see this module's own
        docstring) is responsible for that.
    """
    linked_transaction_ids = [
        transaction_id for link in existing_links for transaction_id in (link.transaction_id_a, link.transaction_id_b)
    ]
    split_posting_ids = list(posting_splits.keys())

    unresolved_real_legs = real_legs_of_two_leg_transactions(postings).filter(
        ~pl.col("transaction_id").is_in(linked_transaction_ids) & ~pl.col("posting_id").is_in(split_posting_ids)
    )

    importable_account_ids = [
        account_id for account_id, account in accounts.items() if account.kind in IMPORTABLE_ACCOUNT_KINDS
    ]
    importable_matches = rule_matches_by_transaction(postings, rules).filter(
        pl.col("counterparty_account_id").is_in(importable_account_ids)
    )
    matched_real_legs = unresolved_real_legs.join(importable_matches, on="transaction_id", how="inner")

    window = pl.duration(days=window_days)
    candidate_lists = (
        matched_real_legs
        .join(unresolved_real_legs, how="cross", suffix="_candidate")
        .filter(
            (pl.col("account_id_candidate") == pl.col("counterparty_account_id"))
            & (pl.col("transaction_id_candidate") != pl.col("transaction_id"))
            & ((pl.col("amount") + pl.col("amount_candidate")).abs() < _AMOUNT_TOLERANCE)
            & ((pl.col("posted_at") - pl.col("posted_at_candidate")).abs() <= window)
        )
        .group_by("transaction_id")
        .agg(pl.col("transaction_id_candidate").unique().alias("_candidates"), pl.col("rule_id").first())
        .collect()
    )
    candidates_by_transaction: dict[str, list[str]] = dict(
        zip(candidate_lists["transaction_id"].to_list(), candidate_lists["_candidates"].to_list(), strict=True)
    )
    rule_id_by_transaction: dict[str, str] = dict(
        zip(candidate_lists["transaction_id"].to_list(), candidate_lists["rule_id"].to_list(), strict=True)
    )

    proposed: list[TransferLink] = []
    already_used: set[str] = set()
    for transaction_id in sorted(candidates_by_transaction):
        if transaction_id in already_used:
            continue
        remaining = [
            candidate_id
            for candidate_id in candidates_by_transaction[transaction_id]
            if candidate_id not in already_used
        ]
        if len(remaining) != 1:
            continue
        other_transaction_id = remaining[0]
        proposed.append(
            make_transfer_link(
                transaction_id, other_transaction_id, source="rule", rule_id=rule_id_by_transaction[transaction_id]
            )
        )
        already_used.add(transaction_id)
        already_used.add(other_transaction_id)

    return proposed


def apply_transfer_links(postings: pl.DataFrame, links: list[TransferLink]) -> pl.DataFrame:
    """Add `is_linked_transfer`/`linked_transaction_id`/`transfer_link_source` for every linked transaction.

    The counterpart to `ledger.categorization.apply_posting_merges` in the
    resolution pipeline, and deliberately the *last* step in it (see
    `api.dependencies._resolved_postings`) — `apply_posting_splits`/
    `apply_manual_overrides` rebuild the frame through `LEDGER_FRAME_SCHEMA`,
    which would silently drop a column added any earlier. Neither
    transaction's own posting is ever touched here — only these three new
    columns are added, so `dashboard.net_worth`/`ledger.replay.account_balances`
    (which sum directly by `account_id`) never need to know about a link at
    all, and `dashboard.income_statement.real_income_expense_legs` (the
    sole income/expense chokepoint) only needs one added filter clause.

    Parameters
    ----------
    postings
        The full, already-resolved posting ledger.
    links
        Every confirmed `TransferLink`.

    Returns
    -------
    polars.DataFrame
        `postings`, with `is_linked_transfer` (bool), `linked_transaction_id`
        (str or None — the other transaction's id), and `transfer_link_source`
        (`"manual"`/`"rule"`/None) added.
    """
    if not links:
        return postings.with_columns(
            is_linked_transfer=pl.lit(value=False),
            linked_transaction_id=pl.lit(None, dtype=pl.Utf8),
            transfer_link_source=pl.lit(None, dtype=pl.Utf8),
        )
    link_lookup = pl.DataFrame(
        [
            {"transaction_id": a, "linked_transaction_id": b, "transfer_link_source": link.source}
            for link in links
            for a, b in ((link.transaction_id_a, link.transaction_id_b), (link.transaction_id_b, link.transaction_id_a))
        ],
        schema={"transaction_id": pl.Utf8, "linked_transaction_id": pl.Utf8, "transfer_link_source": pl.Utf8},
    )
    joined = postings.join(link_lookup, on="transaction_id", how="left")
    return joined.with_columns(is_linked_transfer=pl.col("linked_transaction_id").is_not_null())


def reconcile_and_persist_rule_links(
    postings: pl.DataFrame, session: Session, user_id: uuid.UUID
) -> list[TransferLink]:
    """Find every new rule-safe transfer link and persist it, merging into whatever's already stored.

    The one write-time entry point every caller (see this module's own
    docstring) should use instead of calling `reconcile_rule_links` and
    persisting separately — loads the rules, accounts, splits and links it
    matches against, proposes new links
    against `postings` (the *raw*, pre-`apply_rules` ledger — the same shape
    `reconcile_rule_links` itself expects), and inserts them if any were
    found. The insert is additive and scoped to the new links alone (see
    `repositories.interpretation.insert_transfer_links`), so a link some
    concurrent request confirmed between this function's own read and its
    write is never swept away.

    Parameters
    ----------
    postings
        The raw posting ledger to reconcile against — the freshly merged
        ledger during an import/rebuild, or a fresh, unscoped `load_ledger`
        call when reconciling after a rule change.
    session
        An open database session; `session.commit()` is called only if at
        least one new link was found. Every caller of this function commits
        itself first (via `_write_ledger` or a scoped repository write,
        persisting whatever it just changed), so this
        function's own first read needs Row-Level Security re-scoped — see
        `db.session.set_rls_user`'s own docstring for why that mid-request
        commit alone breaks it.
    user_id
        Whose rules and ledger this is.

    Returns
    -------
    list[TransferLink]
        Newly persisted links, empty if nothing new was found.
    """
    set_rls_user(session, user_id)
    new_links = reconcile_rule_links(
        postings,
        load_transfer_rules(session, user_id),
        seeded_accounts(session, user_id),
        load_posting_splits(session, user_id),
        load_transfer_links(session, user_id),
    )
    if not new_links:
        return []
    insert_transfer_links(session, user_id, new_links)
    session.commit()
    return new_links
