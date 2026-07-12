"""`state` and private helpers shared by 2+ of `accounting.api`'s routers.

A helper only ever called from within a single router's own file stays
defined there instead — see that router module for those. `state` lives
here (rather than in `accounting.api.api`) so every router can import it
without a circular import back through the module that imports them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import polars as pl
from fastapi import HTTPException
from sqlalchemy.orm import Session

from accounting.config import AccountingConfig
from accounting.importers.ingest import load_ledger
from accounting.ledger.categorization import (
    apply_manual_overrides,
    apply_posting_merges,
    apply_posting_splits,
    apply_rules,
)
from accounting.ledger.currency import DisplayCurrency
from accounting.ledger.manual_transfers import postings_for_manual_transfers
from accounting.market_data import exchange_rates
from accounting.models import CurrencyCode
from accounting.store import AccountingStore, load_overrides, load_store

if TYPE_CHECKING:
    import uuid


class _State:
    """Everything a running server needs, held off the shared `app` object so tests can swap it per-test."""

    def __init__(self) -> None:
        self.config = AccountingConfig()


state = _State()


def _resolved_postings_and_store(
    config: AccountingConfig,  # noqa: ARG001
    session: Session,
    user_id: uuid.UUID,
) -> tuple[Any, Any]:
    """Load the raw ledger and resolve it against the current rules and manual overrides.

    A rule only ever repoints a posting at an account that already exists
    in the store, never creates one — so unlike importing a statement
    (which does register a new account), this is a pure read with no side
    effect to persist. Manual overrides are never written back here
    either; they already live in their own file and are only ever applied
    on top.

    Returns
    -------
    tuple[polars.DataFrame, accounting.store.AccountingStore]
        The fully resolved postings, and the current store.
    """
    raw = load_ledger(session, user_id)
    store = load_store(session, user_id)
    resolved = apply_rules(raw, store.rules, store.accounts)
    resolved = apply_posting_splits(resolved, store.posting_splits)
    overrides = load_overrides(session, user_id)
    resolved = apply_manual_overrides(resolved, overrides)
    resolved = apply_posting_merges(resolved, store.posting_merges)
    if store.manual_transfers:
        manual = postings_for_manual_transfers(store.manual_transfers, store.accounts)
        resolved = pl.concat([resolved, manual], how="vertical")
    return resolved, store


def _resolved_postings_for_aggregation(
    config: AccountingConfig, session: Session, user_id: uuid.UUID
) -> tuple[Any, Any]:
    """Like `_resolved_postings_and_store`, but clears category/subcategory for unconfirmed suggestions.

    A pending AI/pattern suggestion is applied optimistically everywhere
    else (see `ledger.pending`) so its category shows up immediately in the
    transaction table for review — but a chart, income statement, or
    budget shouldn't count it until it's actually been validated. Without
    this, `category_totals`/`monthly_income_expense`/`spend_curve_vs_average`/
    the budget endpoints would bucket a still-pending posting under its
    suggested category rather than leaving it in "Uncategorized".

    Returns
    -------
    tuple[polars.DataFrame, accounting.store.AccountingStore]
        The resolved postings (with any pending posting's category/subcategory
        nulled out), and the current store.
    """
    postings, store = _resolved_postings_and_store(config, session, user_id)
    overrides = load_overrides(session, user_id)
    pending_ids = [posting_id for posting_id, override in overrides.items() if override.pending_source is not None]
    if not pending_ids:
        return postings, store
    cleared = pl.when(pl.col("posting_id").is_in(pending_ids)).then(None).otherwise(pl.col("category_id"))
    cleared_sub = pl.when(pl.col("posting_id").is_in(pending_ids)).then(None).otherwise(pl.col("subcategory_id"))
    return postings.with_columns(category_id=cleared, subcategory_id=cleared_sub), store


def _account_has_postings(
    account_id: str,
    config: AccountingConfig,  # noqa: ARG001
    session: Session,
    user_id: uuid.UUID,
) -> bool:
    """Check whether any imported posting has ever been assigned to this account.

    Used to enforce the accounts-CRUD rule: an account's institution,
    kind, and currency (and the account itself) may only be edited or
    deleted before any real transaction has landed on it — afterward,
    only its display name may change.

    Returns
    -------
    bool
        `True` if at least one posting in the raw ledger references this account.
    """
    ledger = load_ledger(session, user_id)
    if ledger.is_empty():
        return False
    return bool(ledger.filter(pl.col("account_id") == account_id).height > 0)


def _display_currency(
    code: CurrencyCode, store: AccountingStore | None = None, as_of: date | None = None
) -> DisplayCurrency:
    """Build a `DisplayCurrency` from the cached exchange-rate history's smoothed rate as of a date.

    Only ever requires history for the currencies actually in play —
    `code` itself, plus every account's, other-asset's, and goal
    contribution's own currency when `store` is given — never every
    `CurrencyCode` this app could theoretically support, so a store with
    no EUR accounts yet isn't blocked from a USD-only net worth just
    because EUR was never synced.

    Parameters
    ----------
    code
        The currency to display aggregates in.
    store
        The accounting store, to find every currency actually in use;
        `None` (e.g. a standalone rate lookup) only requires `code` itself.
    as_of
        The date to compute the smoothed rate as of; defaults to today.

    Returns
    -------
    DisplayCurrency

    Raises
    ------
    HTTPException
        400 if exchange rates have never been synced (or lack history for
        a needed currency) — sync first, rather than silently guessing a rate.
    """
    needed = {code}
    if store is not None:
        needed.update(account.currency for account in store.accounts.values())
        needed.update(asset.currency for asset in store.other_assets)
        needed.update(contribution.currency for contribution in store.goal_contributions.values())
    history = exchange_rates.load_rate_history(state.config)
    try:
        rates = exchange_rates.current_rates_to_base(history, as_of or datetime.now(tz=UTC).date(), needed)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return DisplayCurrency(code, rates)
