"""Resolve a posting's placeholder counterparty into a real account, and its category, from rules.

Phase 1's importers deliberately leave every posting's counterparty
pointed at one of the two uncategorized placeholders (see
`accounting.importers.common`). This module is what repoints those
placeholders at the real counterparty — a `TransferRule` match against an
already-known account — and sets a category on the real leg when a rule
says to. A rule never fabricates a counterparty account out of its own
fields; it only ever repoints a posting at an account that already exists
in the store (see `TransferRule`). Nothing here mutates the ledger cache on disk;
it is applied fresh every time postings are read, so a manual correction
(see `ManualOverride`) applied afterward is never at risk of being
clobbered by re-running a rule.

A rule matching a counterparty of an `IMPORTABLE_ACCOUNT_KINDS` kind
(`checking`/`savings`/`credit_card`/`vault`) is the one case `apply_rules`
never repoints directly: that account might already have its own,
independently-imported posting for the same event, so repointing here
could double-count it. Resolving those safely (finding a unique matching
transaction, or leaving it uncategorized) is `ledger.transfers.reconcile_rule_links`'s
job instead, which — unlike everything else in this module — does need to
persist what it finds, since a live candidate search can't safely run
fresh on every date-scoped dashboard read (see its own docstring).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, overload

import polars as pl

from accounting.ledger.frame import LEDGER_FRAME_SCHEMA
from accounting.models import IMPORTABLE_ACCOUNT_KINDS, Account, ManualOverride, PostingMerge, PostingSplit
from accounting.store import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID

if TYPE_CHECKING:
    from accounting.models import TransferRule

_PLACEHOLDER_ACCOUNT_IDS = {UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID}
_TWO_LEG_TRANSACTION = 2  # Phase 1 always produces exactly two postings per transaction


_RULE_MATCH_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "transaction_id": pl.Utf8,
    "rule_id": pl.Utf8,
    "counterparty_account_id": pl.Utf8,
}


def real_legs_of_two_leg_transactions(postings: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    """Return just the real leg of every transaction with exactly one placeholder + one real posting.

    Everything else (an already-resolved transaction, or a future
    3+-posting paycheck split) is excluded — computed as a vectorized
    group-by count instead of a Python per-transaction loop. Shared with
    `ledger.transfers.reconcile_rule_links`, which needs the exact same
    eligibility check before it can even ask whether a rule matches (and
    also needs `amount`/`posted_at` for its own candidate-matching, hence
    both riding along here too even though rule-matching itself only reads
    `account_id`/`description`).

    Returns
    -------
    polars.LazyFrame
        Columns `transaction_id`, `account_id`, `posting_id`, `description`,
        `amount`, `posted_at` — just what rule/candidate-matching needs,
        not the full posting shape.
    """
    lf = postings.lazy() if isinstance(postings, pl.DataFrame) else postings
    counts = lf.group_by("transaction_id").agg(
        pl.len().alias("_n_legs"),
        pl.col("account_id").is_in(_PLACEHOLDER_ACCOUNT_IDS).sum().alias("_n_placeholder"),
    )
    eligible = counts.filter((pl.col("_n_legs") == _TWO_LEG_TRANSACTION) & (pl.col("_n_placeholder") == 1)).select(
        "transaction_id"
    )
    return (
        lf
        .join(eligible, on="transaction_id", how="inner")
        .filter(~pl.col("account_id").is_in(_PLACEHOLDER_ACCOUNT_IDS))
        .select("transaction_id", "account_id", "posting_id", "description", "amount", "posted_at")
    )


def rule_matches_by_transaction(postings: pl.DataFrame | pl.LazyFrame, rules: list[TransferRule]) -> pl.LazyFrame:
    """Return every transaction resolved by its own highest-priority active matching rule, vectorized in one pass.

    Mirrors `ledger.patterns.match_patterns_bulk`'s exact shape (cross-join
    every real leg against every active rule, filter, lowest-priority-wins
    tiebreak) for `TransferRule` instead of `CategoryPattern` — see that
    function's own docstring for why this beats a per-posting Python loop.
    Deliberately unfiltered by counterparty kind: `apply_rules`/
    `resolved_transfer_rule_ids_by_transaction` below only ever act on a
    safe match, `ledger.transfers.reconcile_rule_links` only ever acts on
    the opposite (`IMPORTABLE_ACCOUNT_KINDS`) one — each applies its own
    kind filter on top of this one shared pass, so it's never duplicated.

    Returns
    -------
    polars.LazyFrame
        Columns `transaction_id`, `rule_id`, `counterparty_account_id` —
        one row per transaction a rule matches; unmatched transactions are
        simply absent.
    """
    active_rules = [rule for rule in rules if rule.active]
    if not active_rules:
        return pl.LazyFrame(schema=_RULE_MATCH_SCHEMA)

    real_legs = real_legs_of_two_leg_transactions(postings)
    rules_lf = pl.LazyFrame({
        "rule_id": [rule.rule_id for rule in active_rules],
        "_description_contains": [rule.description_contains.lower() for rule in active_rules],
        "_rule_account_id": [rule.account_id for rule in active_rules],
        "counterparty_account_id": [rule.counterparty_account_id for rule in active_rules],
        "_priority": [rule.priority for rule in active_rules],
        "_excluded_transaction_ids": [rule.excluded_transaction_ids for rule in active_rules],
    })
    return (
        real_legs
        .with_columns(_description_lower=pl.col("description").str.to_lowercase())
        .join(rules_lf, how="cross")
        .filter(
            pl.col("_description_lower").str.contains(pl.col("_description_contains"), literal=True)
            & (pl.col("_rule_account_id").is_null() | (pl.col("_rule_account_id") == pl.col("account_id")))
            & ~pl.col("_excluded_transaction_ids").list.contains(pl.col("transaction_id"))
        )
        # `rule_id` as a secondary key makes ties deterministic — without it,
        # two equal-priority rules resolve by whatever order the non-stable sort
        # happens to yield.
        .sort(["_priority", "rule_id"])
        .group_by("transaction_id", maintain_order=True)
        .first()
        .select("transaction_id", "rule_id", "counterparty_account_id")
    )


def _safe_rule_matches(
    postings: pl.DataFrame | pl.LazyFrame, rules: list[TransferRule], accounts: dict[str, Account]
) -> pl.LazyFrame:
    """Return every transaction a rule resolves, restricted to a safe (non-`IMPORTABLE_ACCOUNT_KINDS`) counterparty.

    The one place `apply_rules`'s and `resolved_transfer_rule_ids_by_transaction`'s
    otherwise-identical matching logic lives, so it's never maintained twice.

    Returns
    -------
    polars.LazyFrame
        Columns `transaction_id`, `rule_id`, `counterparty_account_id`.
    """
    matches = rule_matches_by_transaction(postings, rules)

    # An importable-kind counterparty (checking/savings/credit_card/vault)
    # might already have its own, independently-imported transaction for this
    # same event — repointing here could double-count it, so it's excluded
    # here rather than left for the caller to filter out. A rule naming an
    # account that doesn't exist at all is excluded the same way, since it's
    # simply absent from `safe_account_ids` below.
    safe_account_ids = [
        account_id for account_id, account in accounts.items() if account.kind not in IMPORTABLE_ACCOUNT_KINDS
    ]
    return matches.filter(pl.col("counterparty_account_id").is_in(safe_account_ids))


@overload
def apply_rules(postings: pl.DataFrame, rules: list[TransferRule], accounts: dict[str, Account]) -> pl.DataFrame: ...
@overload
def apply_rules(postings: pl.LazyFrame, rules: list[TransferRule], accounts: dict[str, Account]) -> pl.LazyFrame: ...
def apply_rules(
    postings: pl.DataFrame | pl.LazyFrame, rules: list[TransferRule], accounts: dict[str, Account]
) -> pl.DataFrame | pl.LazyFrame:
    """Repoint every placeholder counterparty a matching rule resolves.

    Only ever touches a transaction with exactly two postings, one of
    which is still on a placeholder account — anything else (an
    already-resolved transaction, or a future 3+-posting paycheck split)
    is left untouched. A rule only ever repoints a posting at an account
    that already exists in `accounts`; it never creates one, so unlike
    `accounts`, this never needs to be persisted back to the store as a
    side effect of resolving a rule.

    Parameters
    ----------
    postings
        The full posting ledger, as `accounting.importers.ingest.load_ledger` returns it.
    rules
        User-maintained trigger/action rules, in the order the store persisted them.
    accounts
        Every known account, keyed by `account_id`.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        The postings with resolved counterparties where a rule matched,
        rebuilt through `LEDGER_FRAME_SCHEMA` exactly like every other
        step in this resolution chain — same type (lazy or eager) as `postings`.
    """
    lf = postings.lazy() if isinstance(postings, pl.DataFrame) else postings
    matches = _safe_rule_matches(lf, rules, accounts).select("transaction_id", "counterparty_account_id")

    result = (
        lf
        .join(matches, on="transaction_id", how="left")
        .with_columns(
            pl
            .when(
                pl.col("account_id").is_in(_PLACEHOLDER_ACCOUNT_IDS) & pl.col("counterparty_account_id").is_not_null()
            )
            .then(pl.col("counterparty_account_id"))
            .otherwise(pl.col("account_id"))
            .alias("account_id")
        )
        .select(*LEDGER_FRAME_SCHEMA)
        .sort("posted_at", "posting_id")
    )
    return result.collect() if isinstance(postings, pl.DataFrame) else result


def resolved_transfer_rule_ids_by_transaction(
    postings: pl.DataFrame | pl.LazyFrame, rules: list[TransferRule], accounts: dict[str, Account]
) -> dict[str, str]:
    """Report which rule (if any) would resolve each transaction's placeholder counterparty — for traceability only.

    Mirrors `apply_rules`'s own matching exactly (see the shared
    `_safe_rule_matches`), without mutating anything, so a posting can show
    *which* rule set its counterparty and category (see `api.get_postings`'s
    `resolved_by_transfer_rule_id` field). A rule is applied fresh from the
    raw ledger every time postings are read (see this module's docstring) —
    so if that rule is later deleted, the transaction reverts to its
    unresolved, placeholder-counterparty state the very next time postings
    are read. There is nothing to undo here; this function only exists to
    make that already-live resolution visible.

    Parameters
    ----------
    postings
        The full posting ledger, before `apply_rules` — a transaction
        that's already resolved (no placeholder leg left) is skipped, the
        same as `apply_rules` itself would skip it.
    rules
        User-maintained trigger/action rules.
    accounts
        Every known account, keyed by `account_id`.

    Returns
    -------
    dict[str, str]
        `transaction_id -> rule_id`, only for transactions a rule actually resolves.
    """
    matches = _safe_rule_matches(postings, rules, accounts).select("transaction_id", "rule_id").collect()
    return dict(zip(matches["transaction_id"].to_list(), matches["rule_id"].to_list(), strict=True))


def apply_category_redirects(postings: pl.DataFrame, redirects: dict[str, str | None]) -> pl.DataFrame:
    """Resolve the category a posting was *imported* under into the one it means today.

    A posting stores the category its statement named and is never
    rewritten afterwards (see `accounting.db.core.Posting`), so renaming a
    category into an existing one, or deleting it outright, changes nothing
    in `postings` at all — it retires the `categories` row instead
    (`accounting.db.core.Category`), and this is where that retirement
    becomes visible. A merged-away category's postings pick up its
    successor; a deleted category's postings go back to uncategorized,
    the same state a posting that was never categorized is already in.

    Runs before the first overlay stage rather than as one of them — see
    `accounting.precedence` for why a dimension lookup isn't an overlay.
    Anything an overlay sets afterwards (a split leg's category, an
    override's) is already a live category, since those are real foreign
    keys the merge/delete endpoints repoint directly.

    Parameters
    ----------
    postings
        The raw posting ledger, as `accounting.importers.ingest.load_ledger` returns it.
    redirects
        Retired category natural key to its successor's, or `None` for one
        deleted outright — `accounting.repositories.taxonomy.load_category_redirects`.
        A no-op when empty, which is the usual case.

    Returns
    -------
    polars.DataFrame
        The same postings, with `category_id`/`subcategory_id` resolved.
    """
    if not redirects or postings.is_empty():
        return postings
    return postings.with_columns(pl.col("category_id").replace(redirects), pl.col("subcategory_id").replace(redirects))


def apply_posting_splits(postings: pl.DataFrame, splits: dict[str, PostingSplit]) -> pl.DataFrame:
    """Replace each split posting with its legs — the one place a `Transaction` grows past two `Posting`s.

    A paycheck-shaped bank deposit categorized as one lump sum can't be
    broken into wage + reimbursement any other way: `category_id` is
    still one-per-posting, so the posting itself has to become several.
    Each leg gets a synthetic id (`f"{posting_id}:split:{n}"`) so
    `ManualOverride`/`apply_manual_overrides` can still target one leg
    independently afterward, same as any other posting.

    Parameters
    ----------
    postings
        The posting ledger, already passed through `apply_rules`.
    splits
        Every persisted split, keyed by the *original* `posting_id`.

    Returns
    -------
    polars.DataFrame
        The same postings, with each split posting's row replaced by its legs.
    """
    if not splits:
        return postings
    rows = []
    for row in postings.to_dicts():
        split = splits.get(row["posting_id"])
        if split is None:
            rows.append(row)
            continue
        for index, leg in enumerate(split.legs):
            rows.append({
                **row,
                "posting_id": f"{row['posting_id']}:split:{index}",
                "amount": leg.amount,
                "category_id": leg.category_id,
                "subcategory_id": leg.subcategory_id,
                "description": leg.description or row["description"],
            })
    return pl.DataFrame(rows, schema=LEDGER_FRAME_SCHEMA).sort("posted_at", "posting_id")


def apply_manual_overrides(postings: pl.DataFrame, overrides: dict[str, ManualOverride]) -> pl.DataFrame:
    """Apply every persisted manual edit on top of whatever `apply_rules` produced.

    Parameters
    ----------
    postings
        The posting ledger, already passed through `apply_rules`.
    overrides
        Every manual override, keyed by `posting_id`.

    Returns
    -------
    polars.DataFrame
        The same postings, with each override's non-`None` fields applied
        to its posting.
    """
    if not overrides:
        return postings
    rows = postings.to_dicts()
    for row in rows:
        override = overrides.get(row["posting_id"])
        if override is None:
            continue
        for field in ("account_id", "category_id", "subcategory_id", "tag_ids"):
            value = getattr(override, field)
            if value is not None:
                row[field] = value
    return pl.DataFrame(rows, schema=LEDGER_FRAME_SCHEMA).sort("posted_at", "posting_id")


def apply_posting_merges(postings: pl.DataFrame, merges: dict[str, PostingMerge]) -> pl.DataFrame:
    """Drop every duplicate transaction a merge decision resolved, keeping only the one the user chose.

    A duplicate transaction's both legs (the real account's own posting and
    its counterparty) are dropped entirely — unlike `apply_posting_splits`,
    which grows one posting into several, this shrinks two or more
    transactions down to the single one that's kept.

    Parameters
    ----------
    postings
        The posting ledger, already passed through `apply_rules`/`apply_posting_splits`.
    merges
        Every persisted merge decision, keyed by `merge_id`.

    Returns
    -------
    polars.DataFrame
        The same postings, minus every dropped duplicate transaction, with
        the kept transaction's description overridden where one was given.
    """
    if not merges:
        return postings
    dropped_transaction_ids: set[str] = set()
    description_by_kept_transaction: dict[str, str] = {}
    for merge in merges.values():
        dropped_transaction_ids.update(merge.duplicate_transaction_ids)
        if merge.description is not None:
            description_by_kept_transaction[merge.kept_transaction_id] = merge.description

    result = postings.filter(~pl.col("transaction_id").is_in(list(dropped_transaction_ids)))
    if not description_by_kept_transaction:
        return result
    return result.with_columns(
        pl
        .when(pl.col("transaction_id").is_in(list(description_by_kept_transaction)))
        .then(pl.col("transaction_id").replace(description_by_kept_transaction))
        .otherwise(pl.col("description"))
        .alias("description")
    ).sort("posted_at", "posting_id")
