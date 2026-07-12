"""Resolve a posting's placeholder counterparty into a real account, and its category, from rules and vault detection.

Phase 1's importers deliberately leave every posting's counterparty
pointed at one of the two uncategorized placeholders (see
`accounting.importers.common`). This module is what repoints those
placeholders at the real counterparty — a `Rule` match, or, for SoFi
savings specifically, a "To/From <Name> Vault" pattern that names a
sub-account to auto-create — and sets a category on the real leg when a
rule says to. Nothing here mutates the ledger cache on disk; it is applied
fresh every time postings are read, so a manual correction (see
`ManualOverride`) applied afterward is never at risk of being clobbered by
re-running a rule.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import polars as pl

from accounting.models import Account, ManualOverride, Posting, PostingSplit
from accounting.store import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID, slugify

if TYPE_CHECKING:
    from accounting.models import Rule

_PLACEHOLDER_ACCOUNT_IDS = {UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID}
_VAULT_TRANSFER = re.compile(r"^(?:To|From)\s+(.+?)\s+Vault$", re.IGNORECASE)
_SOFI_INTERNAL_TRANSFER = re.compile(r"^To (Checking|Savings) - (\d+)$", re.IGNORECASE)
_TWO_LEG_TRANSACTION = 2  # Phase 1 always produces exactly two postings per transaction


def detect_vault_transfer(description: str) -> str | None:
    """Recognize a SoFi savings "To/From <Name> Vault" transfer and name the vault involved.

    A categorization-layer concern, not an importer one — the SoFi savings
    file format itself gives no structural hint that a row is a vault
    transfer versus an ordinary withdrawal; only the description text does.

    Parameters
    ----------
    description
        A posting's description text.

    Returns
    -------
    str or None
        The vault's name (e.g. `"Travel"`), or `None` if the description doesn't match.
    """
    match = _VAULT_TRANSFER.match(description.strip())
    return match.group(1) if match else None


def detect_sofi_internal_account_transfer(description: str) -> tuple[str, str] | None:
    """Recognize a SoFi statement PDF's "To Checking - 1234"/"To Savings - 5678" transfer.

    Only the "To ..." side ever reaches this point — `importers.sofi.statement_pdf`
    already drops the mirrored "From ..." row on the other account as a
    duplicate of the same real-world transfer, the same double-booking fix
    already applied to Chase's "Payment Thank You" rows.

    Parameters
    ----------
    description
        A posting's description text.

    Returns
    -------
    tuple[str, str] or None
        `(account_kind, last_four_digits)`, e.g. `("Savings", "3680")`, or `None` if it doesn't match.
    """
    match = _SOFI_INTERNAL_TRANSFER.match(description.strip())
    return (match.group(1), match.group(2)) if match else None


def _vault_account(vault_name: str, parent: Account) -> Account:
    vault_id = f"{parent.account_id}:vault:{slugify(vault_name)}"
    return Account(
        account_id=vault_id,
        name=f"{vault_name} Vault",
        kind="vault",
        institution=parent.institution,
        currency=parent.currency,
        parent_account_id=parent.account_id,
    )


def _rule_account(rule: Rule) -> Account | None:
    if rule.counterparty_account_id is None or rule.counterparty_account_kind is None:
        return None
    return Account(
        account_id=rule.counterparty_account_id,
        name=rule.counterparty_account_name or rule.counterparty_account_id,
        kind=rule.counterparty_account_kind,
        institution="external",
        currency="USD",
        parent_account_id=rule.counterparty_parent_account_id,
    )


def _matching_rule(rules: list[Rule], description: str, account_id: str) -> Rule | None:
    lowered = description.lower()
    for rule in sorted(rules, key=lambda r: r.priority):
        if rule.description_contains.lower() not in lowered:
            continue
        if rule.account_id is not None and rule.account_id != account_id:
            continue
        return rule
    return None


def _resolve_counterparty(
    real_leg: dict[str, object], accounts: dict[str, Account]
) -> tuple[Account, str | None, str | None] | None:
    real_account = accounts.get(str(real_leg["account_id"]))
    if real_account is None:
        return None
    description = str(real_leg["description"])
    if real_account.kind == "savings":
        vault_name = detect_vault_transfer(description)
        if vault_name is not None:
            return _vault_account(vault_name, real_account), None, None
    if real_account.kind in {"checking", "savings"}:
        internal_transfer = detect_sofi_internal_account_transfer(description)
        if internal_transfer is not None:
            kind, last4 = internal_transfer
            target_account_id = f"sofi:{kind.lower()}:{last4}"
            target_account = accounts.get(target_account_id)
            if target_account is not None:
                return target_account, None, None
    return None


def apply_rules(
    postings: pl.DataFrame, rules: list[Rule], accounts: dict[str, Account]
) -> tuple[pl.DataFrame, dict[str, Account]]:
    """Repoint every placeholder counterparty a vault-name match or a rule can resolve, and set categories.

    Only ever touches a transaction with exactly two postings, one of
    which is still on a placeholder account — anything else (an
    already-resolved transaction, or a future 3+-posting paycheck split)
    is left untouched.

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
    tuple[polars.DataFrame, dict[str, Account]]
        The postings with resolved counterparties/categories where a match
        was found, and the account registry with any newly-created
        counterparty accounts (vaults, rule-defined accounts) added.
    """
    accounts = dict(accounts)
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

        resolved = _resolve_counterparty(real_leg, accounts)
        if resolved is None:
            rule = _matching_rule(rules, str(real_leg["description"]), str(real_leg["account_id"]))
            if rule is None:
                continue
            counterparty = _rule_account(rule)
            if counterparty is None:
                continue
            resolved = (counterparty, rule.category_id, rule.subcategory_id)

        counterparty_account, category_id, subcategory_id = resolved
        accounts.setdefault(counterparty_account.account_id, counterparty_account)
        placeholder_leg["account_id"] = counterparty_account.account_id
        if category_id is not None:
            real_leg["category_id"] = category_id
        if subcategory_id is not None:
            real_leg["subcategory_id"] = subcategory_id

    resolved_frame = (
        pl.DataFrame(rows, schema=Posting.polars_schema).sort("posted_at", "posting_id")
        if rows
        else pl.DataFrame(schema=Posting.polars_schema)
    )
    return resolved_frame, accounts


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
