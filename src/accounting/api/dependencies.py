"""`state` and private helpers shared by 2+ of `accounting.api`'s routers.

A helper only ever called from within a single router's own file stays
defined there instead — see that router module for those. `state` lives
here (rather than in `accounting.api.api`) so every router can import it
without a circular import back through the module that imports them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import polars as pl
from fastapi import HTTPException
from sqlalchemy.orm import Session

from accounting.config import AccountingConfig
from accounting.importers.ingest import load_ledger
from accounting.ledger.categorization import (
    apply_category_redirects,
    apply_manual_overrides,
    apply_posting_merges,
    apply_posting_splits,
    apply_rules,
)
from accounting.ledger.currency import DisplayCurrency
from accounting.ledger.transfers import apply_transfer_links
from accounting.market_data import exchange_rates
from accounting.models import CurrencyCode
from accounting.precedence import OVERLAY_PRECEDENCE
from accounting.repositories.interpretation import (
    load_overrides,
    load_overrides_for_postings,
    load_posting_merges,
    load_posting_splits,
    load_transfer_links,
    load_transfer_rules,
)
from accounting.repositories.ledger import visible_transaction_page
from accounting.repositories.planning import load_goal_contributions
from accounting.repositories.taxonomy import load_category_redirects, load_other_assets
from accounting.taxonomy import seeded_accounts

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Iterable

    from accounting.models import Account, ManualOverride, TransferRule
    from accounting.precedence import OverlayStage


class _State:
    """Everything a running server needs, held off the shared `app` object so tests can swap it per-test."""

    def __init__(self) -> None:
        """Load the default `AccountingConfig`."""
        self.config = AccountingConfig()


state = _State()


def _overlay_appliers(
    session: Session,
    user_id: uuid.UUID,
    *,
    rules: list[TransferRule],
    accounts: dict[str, Account],
    overrides: dict[str, ManualOverride],
) -> dict[OverlayStage, Callable[[pl.DataFrame], pl.DataFrame]]:
    """Bind one applier per declared overlay stage, so the caller only has to walk the declared order.

    Each entry is the whole of what that stage does to the frame, already
    closed over the rows it needs. The mapping is keyed by
    `accounting.precedence.OverlayStage` and asserted complete below, so a
    stage added to the vocabulary without an applier fails at import-time
    review rather than by silently never running — which is the property
    that makes precedence data rather than the line order of a function
    body.

    Every stage here is per-posting, so none of them needs the caller's
    date window: whatever `load_ledger` returned is already scoped. The
    retired `manual_transfer` stage was the sole exception — it generated
    postings that bypassed that filter and had to re-apply it by hand — and
    it stopped being one when a manual transfer became a real transaction.

    Each stage reads only its own overlay table (plus, for the
    counterparty stage, the accounts a rule repoints at), so they are
    loaded one collection at a time here rather than as one snapshot of
    everything persisted.

    Three of the collections a stage needs are passed in rather than read
    here: `get_postings` needs the same rules, accounts and overrides for
    its own display columns, and loading them once for both is what stops
    that endpoint reading each of them twice per request.

    Parameters
    ----------
    session, user_id
        See `_resolve_postings`.
    rules, accounts, overrides
        Already loaded by `_resolve_postings`, which hands them back to its
        caller alongside the resolved frame.

    Returns
    -------
    dict[accounting.precedence.OverlayStage, collections.abc.Callable]
        One applier per stage, each mapping a frame to the frame that stage produces.

    Raises
    ------
    RuntimeError
        If a declared stage has no applier registered here — an overlay
        that would otherwise silently never be applied.
    """
    posting_splits = load_posting_splits(session, user_id)
    posting_merges = load_posting_merges(session, user_id)
    transfer_links = load_transfer_links(session, user_id)
    appliers: dict[OverlayStage, Callable[[pl.DataFrame], pl.DataFrame]] = {
        "counterparty": lambda postings: apply_rules(postings, rules, accounts),
        "split": lambda postings: apply_posting_splits(postings, posting_splits),
        "override": lambda postings: apply_manual_overrides(postings, overrides),
        "merge": lambda postings: apply_posting_merges(postings, posting_merges),
        "link": lambda postings: apply_transfer_links(postings, transfer_links),
    }
    missing = [stage for stage in OVERLAY_PRECEDENCE if stage not in appliers]
    if missing:
        message = f"No applier registered for declared overlay stage(s): {', '.join(missing)}"
        raise RuntimeError(message)
    return appliers


def _resolved_postings(
    session: Session,
    user_id: uuid.UUID,
    *,
    since: date | None = None,
    until: date | None = None,
) -> pl.DataFrame:
    """Load the raw ledger and replay every interpretation overlay over it, in declared precedence order.

    Which stages exist and what order they run in is
    `accounting.precedence.OVERLAY_PRECEDENCE` — read that module for the
    rationale behind the order. This function walks it; it does not define
    it, and adding a stage does not mean finding the right line here to
    insert a call at.

    One step runs before the walk and is deliberately not a stage:
    `ledger.categorization.apply_category_redirects` resolves the category
    a posting was *imported* under into the one it means today. Postings
    are raw and never rewritten (DB-audit D14), so a category rename,
    merge, or delete retires a `categories` row instead of touching them
    (see `accounting.db.core.Category`) — and this is where that retirement
    is read back. It is a dimension lookup rather than an overlay, and it
    necessarily precedes all five stages because each of them reads or
    writes a category.

    A rule only ever repoints a posting at an account that already
    exists, never creates one — so unlike importing a statement (which
    does register a new account), this is a pure read with no side
    effect to persist. Manual overrides are never written back here
    either; they already live in their own table and are only ever
    applied on top.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose ledger to resolve.
    since
        First day to include, inclusive. `None` (the default, used by
        every caller except the dashboard aggregation endpoints) means no
        bound — the true full history, required for anything that needs
        to see every posting ever (editing rules/splits/merges, imports,
        the raw transaction list). Passed straight through to `load_ledger`,
        so a caller that scopes this to its own date range never loads or
        resolves years of postings it was going to throw away in Python
        anyway. Safe to narrow this way because every resolution step
        (`apply_rules`/`apply_posting_splits`/`apply_manual_overrides`/
        `apply_posting_merges`) only ever looks at one posting/transaction
        at a time — none of them need a *different* posting's date to
        resolve a given one, `apply_posting_merges` included: it drops a
        duplicate purely by transaction id, from the posting merges
        (loaded in full, independently of `since`/`until`), never by
        checking whether the transaction it was merged into is also
        present in this same date-limited frame. Manual transfers used to
        be the one exception, generated outside `load_ledger` and so
        needing the window re-applied by hand; they are real postings now
        and the SQL filter covers them like everything else.
    until
        Last day to include, inclusive. Same reasoning as `since`.

    Returns
    -------
    polars.DataFrame
        The fully resolved postings.
    """
    return _resolve_postings(session, user_id, since=since, until=until).resolved


@dataclass(frozen=True)
class ResolvedPostings:
    """The resolved frame, plus the raw ledger and overlay rows it was resolved from.

    Exists so `GET /postings` can build its display-only columns —
    `resolved_by_transfer_rule_id`, `manual_transfer_override_posting_id`,
    `pending_source`, `pending_selected` — from what resolution already
    read, instead of re-reading it. That endpoint used to load the full
    ledger twice per request, and the overrides, rules and accounts twice
    each, purely because `_resolved_postings` returned only the frame.
    """

    resolved: pl.DataFrame
    """The postings with every overlay applied, in declared precedence order."""
    raw: pl.DataFrame
    """The ledger as stored, before any overlay — what `apply_rules` matched against."""
    overrides: dict[str, ManualOverride]
    """Every persisted per-posting override, keyed by posting id."""
    rules: list[TransferRule]
    """Every counterparty-resolution rule."""
    accounts: dict[str, Account]
    """Every account, keyed by natural key."""
    total: int
    """How many transactions the request's filters match, ignoring its page window.

    Equal to the number of transactions in `resolved` for an unpaged call.
    A client needs it to know whether there is another page."""


def _resolve_postings(
    session: Session,
    user_id: uuid.UUID,
    *,
    since: date | None = None,
    until: date | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> ResolvedPostings:
    """Resolve the ledger and hand back both the result and the rows it was resolved from.

    `_resolved_postings` is this function for the callers that only want
    the frame; read its docstring for what resolution actually does and why
    `apply_category_redirects` runs outside the stage walk.

    With `limit` set, the ledger read is bounded to one page of
    *transactions* — see `repositories.ledger.visible_transaction_page` for
    why the page is cut there and not at a posting, and why merged-away
    duplicates are excluded before the `LIMIT` rather than filtered out of
    the frame afterwards. The overlay stages then run over that page instead
    of over all history, which is what makes the read O(what is shown).

    Two overlay collections stay unbounded on purpose even for a paged call.
    Transfer links are loaded whole because a link's *partner* is usually a
    much older transaction, and scoping the load to the page would flip a
    genuinely linked row to `is_linked_transfer=False`. Posting merges are
    loaded whole because `apply_posting_merges` also rewrites the *kept*
    transaction's description, which is on the page even when the duplicate
    it absorbed is not. Both are small next to the ledger, and both were
    measured at well under a millisecond.

    Parameters
    ----------
    session, user_id, since, until
        See `_resolved_postings`.
    limit
        How many transactions to resolve, newest first. `None` (the
        default, used by every dashboard aggregation) is all of them.
    offset
        How many transactions to skip. Ignored unless `limit` is set.

    Returns
    -------
    ResolvedPostings
    """
    paged_total: int | None = None
    if limit is None:
        raw = load_ledger(session, user_id, since=since, until=until)
        overrides = load_overrides(session, user_id)
    else:
        page = visible_transaction_page(session, user_id, limit=limit, offset=offset, since=since, until=until)
        raw = load_ledger(session, user_id, transaction_ids=page.transaction_ids)
        # Scoped to the page's own postings; the whole-table read would
        # otherwise be the one thing left that scaled with total history.
        overrides = load_overrides_for_postings(session, user_id, raw["posting_id"].to_list())
        paged_total = page.total
    rules = load_transfer_rules(session, user_id)
    accounts = seeded_accounts(session, user_id)
    appliers = _overlay_appliers(session, user_id, rules=rules, accounts=accounts, overrides=overrides)
    resolved = apply_category_redirects(raw, load_category_redirects(session, user_id))
    for stage in OVERLAY_PRECEDENCE:
        resolved = appliers[stage](resolved)
    # Counted off the *resolved* frame for an unpaged call, not the raw one:
    # `apply_posting_merges` drops a merged-away duplicate's postings, and the
    # paged branch excludes those in SQL before its own `LIMIT`. Counting raw
    # transactions here would give the same field two different meanings
    # depending on how it was called.
    total = paged_total if paged_total is not None else resolved.select("transaction_id").n_unique()
    return ResolvedPostings(
        resolved=resolved, raw=raw, overrides=overrides, rules=rules, accounts=accounts, total=total
    )


def _resolved_postings_for_aggregation(
    session: Session,
    user_id: uuid.UUID,
    *,
    since: date | None = None,
    until: date | None = None,
) -> pl.DataFrame:
    """Like `_resolved_postings`, but clears category/subcategory for unconfirmed suggestions.

    A pending AI/pattern suggestion is applied optimistically everywhere
    else (see `ledger.pending`) so its category shows up immediately in the
    transaction table for review — but a chart, income statement, or
    budget shouldn't count it until it's actually been validated. Without
    this, `category_totals`/`monthly_income_expense`/`spend_curve_vs_average`/
    the budget endpoints would bucket a still-pending posting under its
    suggested category rather than leaving it in "Uncategorized".

    Parameters
    ----------
    session, user_id
        See `_resolved_postings`.
    since, until
        See `_resolved_postings` — every caller of *this* function is a
        dashboard aggregation already scoped to its own date range, so
        they should always be passed here.

    Returns
    -------
    polars.DataFrame
        The resolved postings, with any pending posting's category/subcategory nulled out.
    """
    postings = _resolved_postings(session, user_id, since=since, until=until)
    overrides = load_overrides(session, user_id)
    pending_ids = [posting_id for posting_id, override in overrides.items() if override.pending_source is not None]
    if not pending_ids:
        return postings
    cleared = pl.when(pl.col("posting_id").is_in(pending_ids)).then(None).otherwise(pl.col("category_id"))
    cleared_sub = pl.when(pl.col("posting_id").is_in(pending_ids)).then(None).otherwise(pl.col("subcategory_id"))
    return postings.with_columns(category_id=cleared, subcategory_id=cleared_sub)


def _account_has_postings(account_id: str, session: Session, user_id: uuid.UUID) -> bool:
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


def _currencies_in_use(session: Session, user_id: uuid.UUID) -> set[CurrencyCode]:
    """Find every currency this user actually holds money in, across all three places one can be named.

    Read once per request and passed to `_display_currency`, rather than
    re-read per date by an endpoint building a history series.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose currencies to collect.

    Returns
    -------
    set[CurrencyCode]
        Every account's, other asset's, and goal contribution's own currency.
    """
    return (
        {account.currency for account in seeded_accounts(session, user_id).values()}
        | {asset.currency for asset in load_other_assets(session, user_id)}
        | {contribution.currency for contribution in load_goal_contributions(session, user_id).values()}
    )


def _display_currency(
    code: CurrencyCode, currencies: Iterable[CurrencyCode] = (), as_of: date | None = None
) -> DisplayCurrency:
    """Build a `DisplayCurrency` from the cached exchange-rate history's smoothed rate as of a date.

    Only ever requires history for the currencies actually in play —
    `code` itself, plus whatever `currencies` names — never every
    `CurrencyCode` this app could theoretically support, so a user with
    no EUR accounts yet isn't blocked from a USD-only net worth just
    because EUR was never synced.

    Parameters
    ----------
    code
        The currency to display aggregates in.
    currencies
        Every other currency the caller's own figures are held in (see
        `_currencies_in_use`); empty (e.g. a standalone rate lookup) only
        requires `code` itself.
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
    needed = {code, *currencies}
    history = exchange_rates.load_rate_history(state.config)
    try:
        rates = exchange_rates.current_rates_to_base(history, as_of or datetime.now(tz=UTC).date(), needed)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return DisplayCurrency(code, rates)
