"""The overlay pipeline: the raw ledger plus every interpretation layered over it, in declared precedence order.

This is the single implementation of "what a posting resolves to". Which
stages exist and what order they run in is
`accounting.precedence.OVERLAY_PRECEDENCE`; this module walks it, and does
not define it.

It used to live in `api.dependencies`, as two private functions the routers
imported. That was the wrong home once anything below the API layer needed
it: `repositories.projection` caches this pipeline's output, and a
repository reaching up into a router's dependency module would be an import
cycle as well as an inversion. Nothing about the pipeline changed in the
move — the same appliers, the same order, the same completeness check.

Three entry points, narrowing:

- `resolve_postings` — the full answer, including the raw rows and overlay
  collections it was resolved from, for a caller that needs both;
- `resolved_postings` — just the frame, for the callers that only want that;
- `apply_overlays` — the frame-level step alone, over raw rows the caller
  already has and appliers it already built. `repositories.projection` uses
  it to resolve one batch of transactions at a time without re-reading the
  whole-collection overlays per batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from accounting.importers.ingest import load_ledger
from accounting.ledger.categorization import (
    apply_category_redirects,
    apply_manual_overrides,
    apply_posting_merges,
    apply_posting_splits,
    apply_rules,
)
from accounting.ledger.transfers import apply_transfer_links
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
from accounting.repositories.taxonomy import load_category_redirects
from accounting.taxonomy import seeded_accounts

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Sequence
    from datetime import date

    from sqlalchemy.orm import Session

    from accounting.models import Account, ManualOverride, TransferRule
    from accounting.precedence import OverlayStage


@dataclass(frozen=True)
class OverlayContext:
    """Everything the overlay stages need that is *not* the raw rows being resolved.

    Built once per request (or once per projection rebuild) and reused for
    every batch of rows, because none of it is scoped to a batch: rules,
    accounts, splits, merges, links and category redirects are all
    whole-collection reads that would otherwise be repeated per batch. The
    per-batch part is `overrides`, which is scoped to the postings in hand.

    Exists so `apply_overlays` can be called repeatedly without
    `repositories.projection` re-deriving the appliers — and so that when it
    is, it is provably the same appliers, not a second assembly of them.
    """

    rules: list[TransferRule]
    """Every counterparty-resolution rule."""
    accounts: dict[str, Account]
    """Every account, keyed by natural key."""
    redirects: dict[str, str | None]
    """Retired category natural key to its successor's — see `ledger.categorization.apply_category_redirects`."""
    appliers: dict[OverlayStage, Callable[[pl.DataFrame], pl.DataFrame]]
    """One applier per declared stage, everything but the override stage already bound."""
    override_applier: Callable[[dict[str, ManualOverride]], Callable[[pl.DataFrame], pl.DataFrame]]
    """Builds the override stage's applier for one batch's own overrides."""


def overlay_context(session: Session, user_id: uuid.UUID, *, rules: list[TransferRule] | None = None) -> OverlayContext:
    """Read every whole-collection overlay once, and bind one applier per declared stage.

    Each entry in `appliers` is the whole of what that stage does to the
    frame, already closed over the rows it needs. The mapping is keyed by
    `accounting.precedence.OverlayStage` and asserted complete below, so a
    stage added to the vocabulary without an applier fails loudly rather
    than by silently never running — which is the property that makes
    precedence data rather than the line order of a function body.

    Every stage here is per-posting, so none of them needs the caller's
    date window: whatever `load_ledger` returned is already scoped. The
    retired `manual_transfer` stage was the sole exception — it generated
    postings that bypassed that filter and had to re-apply it by hand — and
    it stopped being one when a manual transfer became a real transaction.

    The `override` stage is the one that cannot be bound here: its rows are
    scoped to the postings being resolved, which differ per batch. It is
    handed back as a builder instead, so the *set* of stages is still
    complete and still checked in one place.

    Parameters
    ----------
    session, user_id
        See `resolve_postings`.
    rules
        Already-loaded transfer rules, when the caller has them (see
        `resolve_postings`, which hands them back to its own caller).
        `None` reads them here.

    Returns
    -------
    OverlayContext

    Raises
    ------
    RuntimeError
        If a declared stage has no applier registered here — an overlay
        that would otherwise silently never be applied.
    """
    if rules is None:
        rules = load_transfer_rules(session, user_id)
    accounts = seeded_accounts(session, user_id)
    posting_splits = load_posting_splits(session, user_id)
    posting_merges = load_posting_merges(session, user_id)
    transfer_links = load_transfer_links(session, user_id)

    def _override_applier(overrides: dict[str, ManualOverride]) -> Callable[[pl.DataFrame], pl.DataFrame]:
        return lambda postings: apply_manual_overrides(postings, overrides)

    appliers: dict[OverlayStage, Callable[[pl.DataFrame], pl.DataFrame]] = {
        "counterparty": lambda postings: apply_rules(postings, rules, accounts),
        "split": lambda postings: apply_posting_splits(postings, posting_splits),
        # Replaced per batch by `apply_overlays`; present so the completeness
        # check below covers every declared stage exactly once.
        "override": _override_applier({}),
        "merge": lambda postings: apply_posting_merges(postings, posting_merges),
        "link": lambda postings: apply_transfer_links(postings, transfer_links),
    }
    missing = [stage for stage in OVERLAY_PRECEDENCE if stage not in appliers]
    if missing:
        message = f"No applier registered for declared overlay stage(s): {', '.join(missing)}"
        raise RuntimeError(message)
    return OverlayContext(
        rules=rules,
        accounts=accounts,
        redirects=load_category_redirects(session, user_id),
        appliers=appliers,
        override_applier=_override_applier,
    )


def apply_overlays(raw: pl.DataFrame, context: OverlayContext, overrides: dict[str, ManualOverride]) -> pl.DataFrame:
    """Resolve one set of raw rows: the taxonomy lookup, then every stage in declared order.

    One step runs before the walk and is deliberately not a stage:
    `ledger.categorization.apply_category_redirects` resolves the category a
    posting was *imported* under into the one it means today. Postings are
    raw and never rewritten (DB-audit D14), so a category rename, merge, or
    delete retires a `categories` row instead of touching them (see
    `accounting.db.core.Category`) — and this is where that retirement is
    read back. It is a dimension lookup rather than an overlay, and it
    necessarily precedes all five stages because each of them reads or
    writes a category.

    Safe to call per batch of transactions rather than over all history,
    because every stage is local to one transaction: none of them needs a
    *different* transaction's rows to resolve a given one. That locality is
    what `repositories.ledger.visible_transaction_page` already relies on to
    cut a page, and what `repositories.projection` relies on to recompute
    one transaction rather than the ledger. Two of the whole-collection
    overlays are the reason the batch is a batch of *transactions*, not of
    postings: merges rewrite the kept transaction's description and links
    mark both sides, so both are loaded whole into `context` regardless of
    which rows are in hand.

    Parameters
    ----------
    raw
        The rows to resolve, as `importers.ingest.load_ledger` returns them.
    context
        The whole-collection overlays and the per-stage appliers.
    overrides
        Every persisted override covering `raw`'s postings.

    Returns
    -------
    polars.DataFrame
        The fully resolved postings.
    """
    appliers = {**context.appliers, "override": context.override_applier(overrides)}
    resolved = apply_category_redirects(raw, context.redirects)
    for stage in OVERLAY_PRECEDENCE:
        resolved = appliers[stage](resolved)
    return resolved


@dataclass(frozen=True)
class ResolvedPostings:
    """The resolved frame, plus the raw ledger and overlay rows it was resolved from.

    Exists so `GET /postings` can build its display-only columns —
    `resolved_by_transfer_rule_id`, `manual_transfer_override_posting_id`,
    `pending_source`, `pending_selected` — from what resolution already
    read, instead of re-reading it. That endpoint used to load the full
    ledger twice per request, and the overrides, rules and accounts twice
    each, purely because `resolved_postings` returned only the frame.
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


def resolve_postings(
    session: Session,
    user_id: uuid.UUID,
    *,
    since: date | None = None,
    until: date | None = None,
    limit: int | None = None,
    offset: int = 0,
    transaction_ids: Sequence[uuid.UUID] | None = None,
) -> ResolvedPostings:
    """Resolve the ledger and hand back both the result and the rows it was resolved from.

    `resolved_postings` is this function for the callers that only want the
    frame; read its docstring for what resolution actually does and why
    `apply_category_redirects` runs outside the stage walk.

    With `limit` set, the ledger read is bounded to one page of
    *transactions* — see `repositories.ledger.visible_transaction_page` for
    why the page is cut there and not at a posting, and why merged-away
    duplicates are excluded before the `LIMIT` rather than filtered out of
    the frame afterwards. The overlay stages then run over that page instead
    of over all history, which is what makes the read O(what is shown).

    With `transaction_ids` set, the read is bounded to exactly those
    transactions and nothing is counted — the shape
    `repositories.projection` recomputes a batch with, and the shape
    `GET /postings` uses once its page has been selected from the
    projection.

    Two overlay collections stay unbounded on purpose even for a bounded
    call. Transfer links are loaded whole because a link's *partner* is
    usually a much older transaction, and scoping the load to the page would
    flip a genuinely linked row to `is_linked_transfer=False`. Posting
    merges are loaded whole because `apply_posting_merges` also rewrites the
    *kept* transaction's description, which is on the page even when the
    duplicate it absorbed is not. Both are small next to the ledger, and
    both were measured at well under a millisecond.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose ledger to resolve.
    since
        First day to include, inclusive. `None` (the default, used by
        every caller except the dashboard aggregation endpoints) means no
        bound — the true full history.
    until
        Last day to include, inclusive. Same reasoning as `since`.
    limit
        How many transactions to resolve, newest first. `None` (the
        default, used by every dashboard aggregation) is all of them.
    offset
        How many transactions to skip. Ignored unless `limit` is set.
    transaction_ids
        Resolve exactly these transactions (by row id). Mutually exclusive
        with `limit`; an empty sequence resolves nothing.

    Returns
    -------
    ResolvedPostings
    """
    paged_total: int | None = None
    if transaction_ids is not None:
        raw = load_ledger(session, user_id, transaction_ids=transaction_ids)
        overrides = load_overrides_for_postings(session, user_id, raw["posting_id"].to_list())
    elif limit is None:
        raw = load_ledger(session, user_id, since=since, until=until)
        overrides = load_overrides(session, user_id)
    else:
        page = visible_transaction_page(session, user_id, limit=limit, offset=offset, since=since, until=until)
        raw = load_ledger(session, user_id, transaction_ids=page.transaction_ids)
        # Scoped to the page's own postings; the whole-table read would
        # otherwise be the one thing left that scaled with total history.
        overrides = load_overrides_for_postings(session, user_id, raw["posting_id"].to_list())
        paged_total = page.total
    context = overlay_context(session, user_id)
    resolved = apply_overlays(raw, context, overrides)
    # Counted off the *resolved* frame for an unpaged call, not the raw one:
    # `apply_posting_merges` drops a merged-away duplicate's postings, and the
    # paged branch excludes those in SQL before its own `LIMIT`. Counting raw
    # transactions here would give the same field two different meanings
    # depending on how it was called.
    total = paged_total if paged_total is not None else resolved.select("transaction_id").n_unique()
    return ResolvedPostings(
        resolved=resolved,
        raw=raw,
        overrides=overrides,
        rules=context.rules,
        accounts=context.accounts,
        total=total,
    )


def resolved_postings(
    session: Session,
    user_id: uuid.UUID,
    *,
    since: date | None = None,
    until: date | None = None,
) -> pl.DataFrame:
    """Load the raw ledger and replay every interpretation overlay over it, in declared precedence order.

    Which stages exist and what order they run in is
    `accounting.precedence.OVERLAY_PRECEDENCE` — read that module for the
    rationale behind the order. `apply_overlays` walks it; it does not
    define it, and adding a stage does not mean finding the right line here
    to insert a call at.

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
        First day to include, inclusive. `None` (the default) means no
        bound — the true full history, required for anything that needs to
        see every posting ever (editing rules/splits/merges, imports, the
        raw transaction list). Passed straight through to `load_ledger`, so
        a caller that scopes this to its own date range never loads or
        resolves years of postings it was going to throw away in Python
        anyway. Safe to narrow this way because every resolution step only
        ever looks at one posting/transaction at a time — see
        `apply_overlays` on that locality and what depends on it.
    until
        Last day to include, inclusive. Same reasoning as `since`.

    Returns
    -------
    polars.DataFrame
        The fully resolved postings.
    """
    return resolve_postings(session, user_id, since=since, until=until).resolved


def resolved_postings_for_aggregation(
    session: Session,
    user_id: uuid.UUID,
    *,
    since: date | None = None,
    until: date | None = None,
) -> pl.DataFrame:
    """Like `resolved_postings`, but clears category/subcategory for unconfirmed suggestions.

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
        See `resolved_postings`.
    since, until
        See `resolved_postings` — every caller of *this* function is a
        dashboard aggregation already scoped to its own date range, so
        they should always be passed here.

    Returns
    -------
    polars.DataFrame
        The resolved postings, with any pending posting's category/subcategory nulled out.
    """
    postings = resolved_postings(session, user_id, since=since, until=until)
    overrides = load_overrides(session, user_id)
    pending_ids = [posting_id for posting_id, override in overrides.items() if override.pending_source is not None]
    if not pending_ids:
        return postings
    cleared = pl.when(pl.col("posting_id").is_in(pending_ids)).then(None).otherwise(pl.col("category_id"))
    cleared_sub = pl.when(pl.col("posting_id").is_in(pending_ids)).then(None).otherwise(pl.col("subcategory_id"))
    return postings.with_columns(category_id=cleared, subcategory_id=cleared_sub)
