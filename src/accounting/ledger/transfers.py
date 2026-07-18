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
*persisted* (see `store.AccountingStore.transfer_links`), not recomputed
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

from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

from accounting.ledger.categorization import matching_rule
from accounting.models import IMPORTABLE_ACCOUNT_KINDS, TransferLink
from accounting.store import load_store, save_store

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from accounting.models import Account, PostingSplit, TransferLinkSource, TransferRule

_PLACEHOLDER_ACCOUNT_IDS = ["uncategorized:expense", "uncategorized:income"]
_AMOUNT_TOLERANCE = 1e-6
_TWO_LEG_TRANSACTION = 2  # Phase 1 always produces exactly two postings per transaction
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
    transaction_id_a: str, transaction_id_b: str, source: TransferLinkSource = "manual"
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

    Returns
    -------
    TransferLink
    """
    first, second = sorted((transaction_id_a, transaction_id_b))
    return TransferLink(
        link_id=f"transfer-link:{first}:{second}", transaction_id_a=first, transaction_id_b=second, source=source
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
    postings: pl.DataFrame,
    rules: list[TransferRule],
    accounts: dict[str, Account],
    posting_splits: dict[str, PostingSplit],
    existing_links: list[TransferLink],
    window_days: int = 3,
) -> list[TransferLink]:
    """Propose a `TransferLink` for every rule match that safely resolves to exactly one candidate transaction.

    Mirrors `apply_rules`'s own matching (`matching_rule`) to find which
    transaction a rule would resolve, then — only for a rule whose
    counterparty is an `IMPORTABLE_ACCOUNT_KINDS` account, the case
    `apply_rules` itself refuses to repoint directly — looks for a unique,
    still-unresolved posting on that counterparty account with an opposite
    amount inside `window_days`. Proposes nothing (leaving the transaction
    on its placeholder) when that search finds zero or more than one
    candidate, or when either side already has a `PostingSplit` (see
    `models.PostingSplit` — a split real leg must never be silently
    excluded from income/expense by a link formed afterward) or is already
    part of a `TransferLink`.

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
    linked_transaction_ids = {
        transaction_id for link in existing_links for transaction_id in (link.transaction_id_a, link.transaction_id_b)
    }
    split_posting_ids = set(posting_splits.keys())

    by_transaction: dict[str, list[dict[str, object]]] = {}
    for row in postings.to_dicts():
        by_transaction.setdefault(row["transaction_id"], []).append(row)

    unresolved_real_legs = {
        transaction_id: real_leg
        for transaction_id, legs in by_transaction.items()
        if (real_leg := _unresolved_real_leg(legs, transaction_id, linked_transaction_ids, split_posting_ids))
        is not None
    }
    unresolved_by_account: dict[str, list[dict[str, object]]] = {}
    for real_leg in unresolved_real_legs.values():
        unresolved_by_account.setdefault(str(real_leg["account_id"]), []).append(real_leg)

    window = timedelta(days=window_days)
    proposed: list[TransferLink] = []
    already_used: set[str] = set()
    for transaction_id in sorted(unresolved_real_legs):
        if transaction_id in already_used:
            continue
        real_leg = unresolved_real_legs[transaction_id]
        counterparty_account_id = _importable_counterparty_account_id(rules, accounts, real_leg, transaction_id)
        if counterparty_account_id is None:
            continue

        other_transaction_id = _unique_candidate_transaction_id(
            real_leg, unresolved_by_account.get(counterparty_account_id, []), already_used, window
        )
        if other_transaction_id is None:
            continue

        proposed.append(make_transfer_link(transaction_id, other_transaction_id, source="rule"))
        already_used.add(transaction_id)
        already_used.add(other_transaction_id)

    return proposed


def _unresolved_real_leg(
    legs: list[dict[str, object]],
    transaction_id: str,
    linked_transaction_ids: set[str],
    split_posting_ids: set[str],
) -> dict[str, object] | None:
    """Return the transaction's real leg, if it's still a plain unresolved two-leg transaction, else `None`.

    Returns
    -------
    dict or None
    """
    if transaction_id in linked_transaction_ids or len(legs) != _TWO_LEG_TRANSACTION:
        return None
    placeholder_legs = [leg for leg in legs if leg["account_id"] in _PLACEHOLDER_ACCOUNT_IDS]
    real_legs = [leg for leg in legs if leg["account_id"] not in _PLACEHOLDER_ACCOUNT_IDS]
    if len(placeholder_legs) != 1 or len(real_legs) != 1:
        return None
    real_leg = real_legs[0]
    return None if real_leg["posting_id"] in split_posting_ids else real_leg


def _importable_counterparty_account_id(
    rules: list[TransferRule], accounts: dict[str, Account], real_leg: dict[str, object], transaction_id: str
) -> str | None:
    """Return the matching rule's counterparty account id, if it's an `IMPORTABLE_ACCOUNT_KINDS` kind, else `None`.

    Returns
    -------
    str or None
    """
    rule = matching_rule(rules, str(real_leg["description"]), str(real_leg["account_id"]), transaction_id)
    if rule is None or rule.counterparty_account_id is None:
        return None
    counterparty_account = accounts.get(rule.counterparty_account_id)
    if counterparty_account is None or counterparty_account.kind not in IMPORTABLE_ACCOUNT_KINDS:
        return None
    return rule.counterparty_account_id


def _unique_candidate_transaction_id(
    real_leg: dict[str, object], candidates: list[dict[str, object]], already_used: set[str], window: timedelta
) -> str | None:
    """Return the one candidate matching `real_leg` (opposite amount, within `window`), or `None` if not unique.

    Returns
    -------
    str or None
    """
    matches = [
        candidate
        for candidate in candidates
        if candidate["transaction_id"] != real_leg["transaction_id"]
        and candidate["transaction_id"] not in already_used
        and abs(float(candidate["amount"]) + float(real_leg["amount"])) < _AMOUNT_TOLERANCE  # type: ignore[arg-type]
        and abs(candidate["posted_at"] - real_leg["posted_at"]) <= window  # type: ignore[operator]
    ]
    return str(matches[0]["transaction_id"]) if len(matches) == 1 else None


def apply_transfer_links(postings: pl.DataFrame, links: list[TransferLink]) -> pl.DataFrame:
    """Add `is_linked_transfer`/`linked_transaction_id`/`transfer_link_source` for every linked transaction.

    The counterpart to `ledger.categorization.apply_posting_merges` in the
    resolution pipeline, and deliberately the *last* step in it (see
    `api.dependencies._resolved_postings_and_store`) — `apply_posting_splits`/
    `apply_manual_overrides` rebuild the frame through `Posting.polars_schema`,
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
    docstring) should use instead of calling `reconcile_rule_links`/`save_store`
    separately — loads the current store, proposes new links against
    `postings` (the *raw*, pre-`apply_rules` ledger — the same shape
    `reconcile_rule_links` itself expects), and persists them if any were
    found.

    Parameters
    ----------
    postings
        The raw posting ledger to reconcile against — the freshly merged
        ledger during an import/rebuild, or a fresh, unscoped `load_ledger`
        call when reconciling after a rule change.
    session
        An open database session; `session.commit()` is called (via
        `save_store`) only if at least one new link was found.
    user_id
        Whose store/ledger this is.

    Returns
    -------
    list[TransferLink]
        Newly persisted links, empty if nothing new was found.
    """
    store = load_store(session, user_id)
    new_links = reconcile_rule_links(postings, store.rules, store.accounts, store.posting_splits, store.transfer_links)
    if not new_links:
        return []
    store = store.model_copy(update={"transfer_links": [*store.transfer_links, *new_links]})
    save_store(store, session, user_id)
    return new_links
