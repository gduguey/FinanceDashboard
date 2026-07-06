"""Resolve a posting's placeholder counterparty into a real account, and its category, from rules.

Phase 1's importers deliberately leave every posting's counterparty
pointed at one of the two uncategorized placeholders (see
`accounting.importers.common`). This module is what repoints those
placeholders at the real counterparty — a `Rule` match against an
already-known account — and sets a category on the real leg when a rule
says to. A rule never fabricates a counterparty account out of its own
fields; it only ever repoints a posting at an account that already exists
in the store (see `Rule`). Nothing here mutates the ledger cache on disk;
it is applied fresh every time postings are read, so a manual correction
(see `ManualOverride`) applied afterward is never at risk of being
clobbered by re-running a rule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from accounting.models import Account, ManualOverride, Posting, PostingSplit
from accounting.store import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID

if TYPE_CHECKING:
    from accounting.models import Rule

_PLACEHOLDER_ACCOUNT_IDS = {UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID}
_TWO_LEG_TRANSACTION = 2  # Phase 1 always produces exactly two postings per transaction


def _matching_rule(rules: list[Rule], description: str, account_id: str) -> Rule | None:
    lowered = description.lower()
    for rule in sorted(rules, key=lambda r: r.priority):
        if rule.description_contains.lower() not in lowered:
            continue
        if rule.account_id is not None and rule.account_id != account_id:
            continue
        return rule
    return None


def apply_rules(postings: pl.DataFrame, rules: list[Rule], accounts: dict[str, Account]) -> pl.DataFrame:
    """Repoint every placeholder counterparty a matching rule resolves, and set categories.

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
    polars.DataFrame
        The postings with resolved counterparties/categories where a rule matched.
    """
    rows = postings.to_dicts()
    by_transaction: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_transaction.setdefault(row["transaction_id"], []).append(row)

    for legs in by_transaction.values():
        if len(legs) != _TWO_LEG_TRANSACTION:
            continue
        placeholder_legs = [leg for leg in legs if leg["account_id"] in _PLACEHOLDER_ACCOUNT_IDS]
        real_legs = [leg for leg in legs if leg["account_id"] not in _PLACEHOLDER_ACCOUNT_IDS]
        if len(placeholder_legs) != 1 or len(real_legs) != 1:
            continue
        placeholder_leg, real_leg = placeholder_legs[0], real_legs[0]

        rule = _matching_rule(rules, str(real_leg["description"]), str(real_leg["account_id"]))
        if rule is None or rule.counterparty_account_id is None:
            continue
        counterparty_account = accounts.get(rule.counterparty_account_id)
        if counterparty_account is None:
            continue

        placeholder_leg["account_id"] = counterparty_account.account_id
        if rule.category_id is not None:
            real_leg["category_id"] = rule.category_id
        if rule.subcategory_id is not None:
            real_leg["subcategory_id"] = rule.subcategory_id

    return (
        pl.DataFrame(rows, schema=Posting.polars_schema).sort("posted_at", "posting_id")
        if rows
        else pl.DataFrame(schema=Posting.polars_schema)
    )


def resolved_rule_ids_by_transaction(
    postings: pl.DataFrame, rules: list[Rule], accounts: dict[str, Account]
) -> dict[str, str]:
    """Report which rule (if any) would resolve each transaction's placeholder counterparty — for traceability only.

    Mirrors `apply_rules`'s own matching exactly, without mutating
    anything, so a posting can show *which* rule set its counterparty and
    category (see `api.get_postings`'s `resolved_by_rule_id` field). A
    rule is applied fresh from the raw ledger every time postings are
    read (see this module's docstring) — so if that rule is later
    deleted, the transaction reverts to its unresolved, placeholder-counterparty
    state the very next time postings are read. There is nothing to undo
    here; this function only exists to make that already-live resolution visible.

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
    rows = postings.to_dicts()
    by_transaction: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_transaction.setdefault(row["transaction_id"], []).append(row)

    resolved_by: dict[str, str] = {}
    for transaction_id, legs in by_transaction.items():
        if len(legs) != _TWO_LEG_TRANSACTION:
            continue
        placeholder_legs = [leg for leg in legs if leg["account_id"] in _PLACEHOLDER_ACCOUNT_IDS]
        real_legs = [leg for leg in legs if leg["account_id"] not in _PLACEHOLDER_ACCOUNT_IDS]
        if len(placeholder_legs) != 1 or len(real_legs) != 1:
            continue
        real_leg = real_legs[0]

        rule = _matching_rule(rules, str(real_leg["description"]), str(real_leg["account_id"]))
        if rule is None or rule.counterparty_account_id is None:
            continue
        if accounts.get(rule.counterparty_account_id) is None:
            continue
        resolved_by[transaction_id] = rule.rule_id
    return resolved_by


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
    return pl.DataFrame(rows, schema=Posting.polars_schema).sort("posted_at", "posting_id")


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
    return pl.DataFrame(rows, schema=Posting.polars_schema).sort("posted_at", "posting_id")
