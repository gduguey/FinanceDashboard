"""Posting-resolution endpoints — mirrors `accounting.ledger.*`: categorization, duplicates, transfers, pending."""

from __future__ import annotations

import io
import uuid
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    DismissSuggestionRequest,
    DuplicateGroup,
    FilteredBulkRequest,
    LedgerExportPage,
    PostingMergeUpsert,
    PostingPage,
    PostingQuery,
    PostingRow,
    TransferLinkCreate,
    TransferSuggestion,
    ValidatePendingResult,
)
from accounting.api.dependencies import state
from accounting.api.entities import (
    DismissedSuggestion,
    ManualOverride,
    Posting,
    PostingMerge,
    PostingSplit,
    PostingSplitLeg,
    TransferLink,
)
from accounting.api.locations import created_or_replaced, location_of
from accounting.ledger.duplicates import DuplicateGroup as DuplicateGroupData
from accounting.ledger.duplicates import find_duplicate_candidates
from accounting.ledger.pending import resolve_pending_suggestion
from accounting.ledger.resolution import resolved_postings
from accounting.ledger.transfers import find_unmatched_transfer_candidates, make_transfer_link
from accounting.models import DismissedSuggestion as DomainDismissedSuggestion
from accounting.models import PostingMerge as DomainPostingMerge
from accounting.repositories.interpretation import (
    delete_posting_split,
    dismiss_suggestion,
    dismissed_suggestion_ids,
    insert_transfer_links,
    list_dismissed_suggestions,
    load_overrides_for_postings,
    load_posting_merges,
    load_posting_splits,
    load_transfer_links,
    remove_posting_merge,
    remove_transfer_link,
    save_overrides_for_postings,
    save_posting_split,
    undismiss_suggestion,
    upsert_posting_merge,
)
from accounting.repositories.ledger import (
    ledger_posting_count,
    load_ledger_page,
    transaction_keys_by_posting_key,
)
from accounting.repositories.projection import (
    distinct_months,
    drain,
    filtered_page,
    linked_legs,
    matching_posting_ids,
    page_rows,
)
from accounting.utils.statement_archive import StatementArchive
from db.current_user import get_current_user_id
from db.money import ZERO, quantize_money
from db.session import get_db
from http_api.pagination import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX

router = APIRouter()

_LINK_MEMBERSHIP_CONSTRAINT = "uq_transfer_linked_transactions_user_transaction"
"""The unique index holding "a transaction is in at most one transfer link" — see `accounting.db.transfers`."""


@router.get("/postings")
def get_postings(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    query: Annotated[PostingQuery, Query()],
) -> PostingPage:
    """Return one filtered, sorted page of postings, resolved against the current rules and manual overrides.

    Served from `accounting.resolved_postings`, the stored output of the same
    overlay pipeline an unpaged read would run — see
    `accounting.db.projection` for what keeps the two equal, and
    `repositories.projection.drain`, which this calls first so a read can
    never serve a row a write has invalidated. Every filter below reads a
    *resolved* value, which is why they could not be evaluated in SQL before
    that table existed: the category a redirect or an override rewrote, the
    account a rule repointed, the description a merge rewrote, the amount a
    split changed.

    Each row carries `pending_source` (`"ai"`, `"pattern"`, or `None`) and
    `pending_selected` — an automated categorizer's not-yet-confirmed
    suggestion, and whether it's currently checked for the next "validate
    selection" action (see `ledger.pending`) —
    `resolved_by_transfer_rule_id`, naming which `TransferRule` (if any)
    resolved this posting's transaction, purely for display (see
    `ledger.categorization.resolved_transfer_rule_ids_by_transaction`) — and
    `manual_transfer_override_posting_id`, the same thing for a manual "flag
    as transfer" (`ManualOverride.account_id`) instead of a rule. A manual
    override always wins if both somehow apply to the same transaction (it's
    applied after rules — see `ledger.resolution.apply_overlays`), so
    `resolved_by_transfer_rule_id` is suppressed whenever
    `manual_transfer_override_posting_id` is set for that transaction — see
    `PostingRow`'s own docstring.

    `limit` counts **transactions**, not postings, and the page carries every
    leg of every transaction it covers — so `len(items)` is normally larger
    than `limit`, and larger still where a transaction has been split.
    `repositories.projection.filtered_page` explains why the page cannot be
    cut at a matching row instead, and how a transaction with several
    matching rows takes its place in the order.

    A `limit` above `PAGE_LIMIT_MAX` is clamped rather than rejected; see
    that constant for why.

    Parameters
    ----------
    query
        The filter bar, the sort and the page window — see `PostingQuery`,
        which explains why all three arrive as one model rather than as a
        filter beside four loose parameters.

    Returns
    -------
    PostingPage
        The page's postings, the total transaction count a client needs in
        order to ask for the next page, and the two other counts the screen
        shows (see `PostingPageCounts`).
    """
    limit = min(query.limit, PAGE_LIMIT_MAX)
    drain(session, user_id)
    page = filtered_page(
        session, user_id, query, sort=query.sort, descending=query.descending, limit=limit, offset=query.offset
    )
    rows = page_rows(session, user_id, page.transaction_ids)
    legs = linked_legs(session, user_id, [row["linked_transaction_id"] for row in rows])
    for row in rows:
        row["linked_leg"] = legs.get(row["linked_transaction_id"]) if row["linked_transaction_id"] else None
    return PostingPage(
        items=[PostingRow(**row) for row in rows],
        window_unit="transaction",
        total=page.counts.matched_transactions,
        limit=limit,
        offset=query.offset,
        counts=page.counts,
    )


@router.get("/postings/months")
def get_posting_months(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[str]:
    """Every `YYYY-MM` this user has a posting in, newest first — the month picker's options.

    Bounded by construction: one row per month a user has ever transacted in,
    which is tens of entries for a decade of history. It was previously a
    `Set` built over every posting on the client (`web/src/lib/months.ts`),
    which is only cheap while something else is already holding the whole
    ledger in memory — and nothing is, now.

    Returns
    -------
    list[str]
    """
    drain(session, user_id)
    return distinct_months(session, user_id)


@router.get("/ledger/export")
def get_ledger_export(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    limit: Annotated[int, Query(ge=1, description="How many postings to return, oldest first.")] = PAGE_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0, description="How many postings to skip.")] = 0,
) -> LedgerExportPage:
    """Export one page of the raw ledger, exactly as imported — before any rule, override, split, or merge.

    An export's caller wants the whole ledger by definition, so this is
    bounded rather than filtered: a page is capped at `PAGE_LIMIT_MAX` and
    the client walks `offset` until it has `total` postings. That is
    deliberately not the same as returning a truncated file — a partial
    backup presented as a complete one is worse than several requests. A
    streaming response would suit this endpoint better still, but choosing a
    media type for it is a contract question rather than a read-path one.

    `limit` counts postings here, not transactions as it does on
    `GET /postings`, because the raw ledger has no overlay applied and so
    nothing needing a transaction's legs kept together.

    Parameters
    ----------
    limit
        How many postings to return, oldest first. Clamped to `PAGE_LIMIT_MAX`.
    offset
        How many postings to skip.

    Returns
    -------
    LedgerExportPage
        The page's raw postings for the user's own backup, plus the total. See
        `GET /postings` for the same data after every
        rule/override/split/merge is applied on top — what the Transactions
        page actually shows.
    """
    limit = min(limit, PAGE_LIMIT_MAX)
    page = load_ledger_page(session, user_id, limit=limit, offset=offset)
    return LedgerExportPage(
        items=[Posting(**row) for row in page.to_dicts()],
        window_unit="posting",
        total=ledger_posting_count(session, user_id),
        limit=limit,
        offset=offset,
    )


@router.get("/statements/export")
def get_statements_export(user_id: Annotated[uuid.UUID, Depends(get_current_user_id)]) -> Response:
    """Zip every raw statement archived from an import (CSV or PDF, verbatim as uploaded) for download.

    Returns
    -------
    fastapi.Response
        A `.zip` attachment, one entry per archived file, empty if
        nothing has been imported yet.
    """
    buffer = io.BytesIO()
    archive = StatementArchive(state.config.raw_statement_dir, f"statements/{user_id}")
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for relative_path, data in archive.read_all():
            zip_file.writestr(relative_path, data)
    filename = f"accounting-statements-{datetime.now(tz=UTC).date().isoformat()}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put("/postings/{posting_id}/override")
def put_posting_override(
    posting_id: str,
    override: ManualOverride,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> ManualOverride:
    """Upsert one posting's manual override, merging into any override already stored for it.

    A request only ever carries the fields the caller actually means to
    change (e.g. setting a subcategory sends just `subcategory_id`) — so a
    field absent from this request must fall back to whatever was already
    stored, never reset to `None`, or an earlier edit (like a manually-set
    category) would be wiped out by a later, unrelated one (like picking a
    subcategory). `model_fields_set` is what distinguishes "the caller sent
    this field, possibly as null, to clear it" from "the caller didn't
    mention this field at all".

    Returns
    -------
    ManualOverride
        The override just persisted, merged with any prior one.
    """
    existing = load_overrides_for_postings(session, user_id, [posting_id]).get(posting_id)
    if existing is not None:
        merged = existing.model_dump()
        merged.update(override.model_dump(include=override.model_fields_set))
        override = ManualOverride(**merged)
    save_overrides_for_postings([posting_id], {posting_id: override.to_domain()}, session, user_id)
    return override


def _current_amount_for_split(postings: pl.DataFrame, posting_id: str) -> float | None:
    """Return the amount a split of `posting_id` must sum to — its own amount, or (if already split) its legs' total.

    Returns
    -------
    float or None
        `None` if no posting or split leg with this id exists at all.
    """
    direct = postings.filter(pl.col("posting_id") == posting_id)
    if not direct.is_empty():
        return float(direct["amount"][0])
    legs = postings.filter(pl.col("posting_id").str.starts_with(f"{posting_id}:split:"))
    if legs.is_empty():
        return None
    return float(legs["amount"].sum())


@router.put("/postings/{posting_id}/split")
def put_posting_split(
    posting_id: str,
    legs: list[PostingSplitLeg],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> PostingSplit:
    """Split one posting into several independently-categorized legs, e.g. a paycheck into wage + reimbursement.

    Overwrites any split already stored for this posting — unlike
    `put_posting_override`'s field-level merge, a split is one coherent
    set of legs, not independently-settable fields, so there's nothing
    meaningful to merge.

    Returns
    -------
    PostingSplit
        The split just persisted.

    Raises
    ------
    HTTPException
        404 if the posting doesn't exist; 400 if the legs don't sum to the posting's own amount.
    """
    postings = resolved_postings(session, user_id)
    current_amount = _current_amount_for_split(postings, posting_id)
    if current_amount is None:
        raise HTTPException(status_code=404, detail=f"Posting {posting_id!r} not found")
    # Both sides are exact `Decimal` now, so this is a true equality check —
    # no float slack, and a genuinely off-by-a-hundredth split is caught
    # instead of being absorbed by a tolerance.
    total = sum((leg.amount for leg in legs), start=ZERO)
    if total != quantize_money(current_amount):
        raise HTTPException(
            status_code=400, detail=f"Legs sum to {total}, not the posting's own amount of {current_amount}"
        )
    split = PostingSplit(posting_id=posting_id, legs=legs)
    save_posting_split(split.to_domain(), session, user_id)
    return split


@router.delete("/postings/{posting_id}/split", status_code=204)
def delete_posting_split_route(
    posting_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Undo a posting split, restoring the single original posting."""
    delete_posting_split(session, user_id, posting_id)
    session.commit()


def _merge_id(kept_transaction_id: str) -> str:
    """Derive a posting merge's id from the transaction it keeps.

    Returns
    -------
    str
    """
    return f"merge:{kept_transaction_id}"


@router.get("/posting-merges/{merge_id}")
def get_posting_merge(
    merge_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> PostingMerge:
    """Return one duplicate-resolution decision by id — the address `post_posting_merge` advertises.

    Returns
    -------
    PostingMerge

    Raises
    ------
    HTTPException
        404 if no merge has this id.
    """
    merge = load_posting_merges(session, user_id).get(merge_id)
    if merge is None:
        raise HTTPException(status_code=404, detail=f"Posting merge {merge_id!r} not found")
    return PostingMerge.from_domain(merge)


@router.post("/posting-merges", status_code=201, responses=created_or_replaced(PostingMerge))
def post_posting_merge(
    request: PostingMergeUpsert,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> PostingMerge:
    """Upsert one duplicate-resolution decision, without touching any other merge already recorded.

    A real upsert keyed on the kept transaction, so the status says which
    of the two things happened: `201` with a `Location` when this recorded
    a new decision, `200` when it replaced the decision already recorded
    for that transaction.

    Stays a `POST` on the collection rather than becoming
    `PUT /posting-merges/{merge_id}`. The id is `merge:{kept_transaction_id}`
    — derivable in principle, but `_merge_id`'s prefix is this module's
    private key format, and making every client build it would export that
    format as part of the contract.

    Returns
    -------
    PostingMerge
        The merge just persisted.
    """
    merge = DomainPostingMerge(
        merge_id=_merge_id(request.kept_transaction_id),
        kept_transaction_id=request.kept_transaction_id,
        duplicate_transaction_ids=request.duplicate_transaction_ids,
        description=request.description,
    )
    if upsert_posting_merge(merge, session, user_id):
        location_of(http_request, response, "get_posting_merge", merge_id=merge.merge_id)
    else:
        response.status_code = 200
    return PostingMerge.from_domain(merge)


@router.delete("/posting-merges/{merge_id}", status_code=204)
def delete_posting_merge(
    merge_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Undo one duplicate-resolution decision, restoring the merged-away transactions to the ledger.

    Raises
    ------
    HTTPException
        404 if no merge with this id exists.
    """
    if not remove_posting_merge(session, user_id, merge_id):
        raise HTTPException(status_code=404, detail=f"Posting merge {merge_id!r} not found")
    session.commit()


@router.get("/transfer-links/{link_id}")
def get_transfer_link(
    link_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TransferLink:
    """Return one confirmed transfer link by id — the address `post_transfer_link` advertises.

    Returns
    -------
    TransferLink

    Raises
    ------
    HTTPException
        404 if no link has this id.
    """
    link = next((existing for existing in load_transfer_links(session, user_id) if existing.link_id == link_id), None)
    if link is None:
        raise HTTPException(status_code=404, detail=f"Transfer link {link_id!r} not found")
    return TransferLink.from_domain(link)


@router.post("/transfer-links", status_code=201, responses=created_or_replaced(TransferLink))
def post_transfer_link(
    request: TransferLinkCreate,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TransferLink:
    """Confirm two transactions as the two sides of one real-world transfer.

    Neither transaction's own posting is ever touched — see
    `ledger.transfers.apply_transfer_links` for how this changes
    classification instead. Re-confirming the exact same pair (from
    either side) is a no-op, returning the existing link.

    That no-op is why the status is conditional: `201` with a `Location`
    when this call confirmed the pair, `200` when the identical link was
    already there. Not strictly an upsert — nothing is overwritten on the
    second call — but the same distinction, read from the `already_this_link`
    lookup below, in the transaction that writes.

    Returns
    -------
    TransferLink
        The link just persisted (or the already-existing one, if this
        exact pair was already linked).

    Raises
    ------
    HTTPException
        400 if either transaction names itself, or already has a
        `PostingSplit`; 409 if either transaction is already part of a
        *different* transfer link — whether that was already true when the
        request arrived, or became true concurrently while it was being
        served.
    sqlalchemy.exc.IntegrityError
        Any constraint violation that is *not* the one-link-per-transaction
        rule. Re-raised untouched rather than folded into the 409, so a
        genuinely unexpected violation stays a loud 500.
    """
    if request.transaction_id_a == request.transaction_id_b:
        raise HTTPException(status_code=400, detail="Cannot link a transaction to itself")

    link = make_transfer_link(request.transaction_id_a, request.transaction_id_b, source="manual")
    transfer_links = load_transfer_links(session, user_id)

    already_this_link = next((existing for existing in transfer_links if existing.link_id == link.link_id), None)
    if already_this_link is not None:
        response.status_code = 200
        return TransferLink.from_domain(already_this_link)

    linked_transaction_ids = {
        transaction_id
        for existing in transfer_links
        for transaction_id in (existing.transaction_id_a, existing.transaction_id_b)
    }
    for transaction_id in (link.transaction_id_a, link.transaction_id_b):
        if transaction_id in linked_transaction_ids:
            raise HTTPException(
                status_code=409, detail=f"Transaction {transaction_id!r} is already part of another transfer link"
            )

    # Scoped to the two transactions being linked. This used to load the whole
    # ledger to build a posting -> transaction map for exactly two ids.
    pair = (link.transaction_id_a, link.transaction_id_b)
    transaction_by_posting = transaction_keys_by_posting_key(session, user_id, pair)
    splits = load_posting_splits(session, user_id)
    for transaction_id in pair:
        if any(
            transaction == transaction_id and posting_id in splits
            for posting_id, transaction in transaction_by_posting.items()
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Transaction {transaction_id!r} has already been split and can't be linked",
            )

    # Every check above read before writing, so two requests linking the same
    # transaction to two *different* partners both pass them. The parent rows
    # they insert have different natural keys, so `insert_transfer_links`'
    # `ON CONFLICT DO NOTHING` skips neither; the membership rows then collide
    # on `uq_transfer_linked_transactions_user_transaction`. The database is
    # what actually holds "a transaction is in at most one link", so the loser
    # corrupts nothing — it just used to surface as an unhandled
    # `IntegrityError`, i.e. a 500 where the sequential path gives a 409.
    try:
        insert_transfer_links(session, user_id, [link])
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if _LINK_MEMBERSHIP_CONSTRAINT not in str(error.orig):
            raise
        detail = f"One of {link.transaction_id_a!r}, {link.transaction_id_b!r} is already part of another transfer link"
        raise HTTPException(status_code=409, detail=detail) from error
    location_of(http_request, response, "get_transfer_link", link_id=link.link_id)
    return TransferLink.from_domain(link)


@router.delete("/transfer-links/{link_id}", status_code=204)
def delete_transfer_link(
    link_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Undo a confirmed transfer link, restoring both transactions to their prior classification.

    Raises
    ------
    HTTPException
        404 if no link with this id exists.
    """
    if not remove_transfer_link(session, user_id, link_id):
        raise HTTPException(status_code=404, detail=f"Transfer link {link_id!r} not found")
    session.commit()


@router.post("/postings/validate-pending")
def post_validate_pending(
    payload: FilteredBulkRequest,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> ValidatePendingResult:
    """Resolve every pending suggestion the caller's current filter matches, per its own `pending_selected` flag.

    The set comes from the filter rather than from a list of ids, and is
    resolved **in the same transaction as the write** — so it cannot shift
    between the two, which a client-supplied list gathered over several
    requests could. It is the same `PostingFilters` `GET /postings` takes,
    so what this touches is by construction what the screen is showing.

    A posting with no override, or one whose override isn't pending, is
    silently skipped — it matched the filter, it simply had nothing to
    resolve.

    Returns
    -------
    ValidatePendingResult
        `matched` is the size of the set the filter resolved to; `accepted`
        and `reverted` how many of them had a suggestion, and which way it
        went.
    """
    drain(session, user_id)
    posting_ids = matching_posting_ids(session, user_id, payload.filters)
    overrides = load_overrides_for_postings(session, user_id, posting_ids)
    accepted = reverted = 0
    for posting_id in posting_ids:
        existing = overrides.get(posting_id)
        if existing is None or existing.pending_source is None:
            continue
        if existing.pending_selected:
            accepted += 1
        else:
            reverted += 1
        resolved = resolve_pending_suggestion(existing)
        if resolved is None:
            del overrides[posting_id]
        else:
            overrides[posting_id] = resolved
    save_overrides_for_postings(posting_ids, overrides, session, user_id)
    return ValidatePendingResult(matched=len(posting_ids), accepted=accepted, reverted=reverted)


@router.post("/postings/matching-ids")
def post_matching_posting_ids(
    payload: FilteredBulkRequest,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[str]:
    """Every posting id the caller's current filter matches, for the one bulk action that cannot be server-side.

    The AI categorizer runs one request per posting on purpose — the loop is
    a deliberate rate limit, not an oversight (see known gap 5) — so it needs
    the identity of the set it is about to walk. Everything else that acts on
    a filter does so server-side and never sees an id.

    Deliberately unpaged, and that is not a hole in the bounded-reads rule:
    the response is strictly smaller than the work it precedes, since the
    caller is about to make one LLM call per entry. Paging it would add
    round trips to a list the client must hold in full anyway to loop over
    it, and the alternative — truncating to a page — is exactly the silent
    truncation the whole screen was rebuilt to remove.

    Returns
    -------
    list[str]
        Matching posting ids, newest first, in the same order the table
        shows them under its default sort.
    """
    drain(session, user_id)
    return matching_posting_ids(session, user_id, payload.filters)


def _transfer_suggestion_id(row: dict[str, Any]) -> str:
    """Build a stable key for a transfer-suggestion pair, independent of which side comes first in the row.

    Recomputing the candidate list finds the exact same pair under the
    exact same key every time, so a dismissed suggestion reliably stays
    dismissed (see `models.DismissedSuggestion`).

    Returns
    -------
    str
        The pair's stable dismiss key.
    """
    return "transfer:" + ":".join(sorted([row["posting_id"], row["other_posting_id"]]))


def _duplicate_suggestion_id(group: DuplicateGroupData) -> str:
    """Build a stable key for a duplicate group, from its own already-stable `group_key`.

    Returns
    -------
    str
        The group's stable dismiss key.
    """
    return f"duplicate:{group.group_key}"


@router.get("/transfer-suggestions")
def get_transfer_suggestions(
    *,
    window_days: int = 3,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[TransferSuggestion]:
    """Suggest likely internal transfers no rule has already resolved.

    Parameters
    ----------
    window_days
        How many days apart the two postings can be and still count as
        one transfer — widen this if a transfer took longer than 3 days
        to land on both sides (e.g. an ACH transfer over a weekend).

    Returns
    -------
    list[TransferSuggestion]
        Each carries both sides' own `description` and a `suggestion_id`,
        for the caller to propose a `TransferRule` from or dismiss —
        never applied automatically. Excludes any pair already dismissed
        (see `PUT /dismissed-suggestions/{suggestion_id}`).
    """
    postings = resolved_postings(session, user_id)
    candidates = find_unmatched_transfer_candidates(
        postings, window_days=window_days, existing_links=load_transfer_links(session, user_id)
    )
    rows = candidates.to_dicts()
    suggestion_ids = [_transfer_suggestion_id(row) for row in rows]
    dismissed = dismissed_suggestion_ids(session, user_id, suggestion_ids)
    return [
        TransferSuggestion(**row, suggestion_id=suggestion_id)
        for row, suggestion_id in zip(rows, suggestion_ids, strict=True)
        if suggestion_id not in dismissed
    ]


@router.get("/duplicate-suggestions")
def get_duplicate_suggestions(
    *,
    window_days: int = 3,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[DuplicateGroup]:
    """Suggest likely duplicate transactions no merge decision has already resolved.

    Parameters
    ----------
    window_days
        How many days apart two transactions can be and still count as one duplicate.

    Returns
    -------
    list[DuplicateGroup]
        Sorted least-certain first, since those need the closest review.
        Each carries a `suggestion_id` for dismissing it. Excludes any
        group already dismissed (see `PUT /dismissed-suggestions/{suggestion_id}`).
    """
    postings = resolved_postings(session, user_id)
    groups = find_duplicate_candidates(postings, window_days=window_days)
    suggestion_ids = [_duplicate_suggestion_id(group) for group in groups]
    dismissed = dismissed_suggestion_ids(session, user_id, suggestion_ids)
    return [
        DuplicateGroup(**asdict(group), suggestion_id=suggestion_id)
        for group, suggestion_id in zip(groups, suggestion_ids, strict=True)
        if suggestion_id not in dismissed
    ]


@router.get("/dismissed-suggestions")
def get_dismissed_suggestions(
    session: Annotated[Session, Depends(get_db)], user_id: Annotated[uuid.UUID, Depends(get_current_user_id)]
) -> list[DismissedSuggestion]:
    """List every archived (dismissed) suggestion, most recently dismissed first.

    Returns
    -------
    list[DismissedSuggestion]
    """
    return [DismissedSuggestion.from_domain(entry) for entry in list_dismissed_suggestions(session, user_id)]


@router.get("/dismissed-suggestions/{suggestion_id}")
def get_dismissed_suggestion(
    suggestion_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> DismissedSuggestion:
    """Return one archived suggestion by id — the address `put_dismissed_suggestion` advertises.

    Returns
    -------
    DismissedSuggestion

    Raises
    ------
    HTTPException
        404 if no archived entry has this id.
    """
    entry = next(
        (
            archived
            for archived in list_dismissed_suggestions(session, user_id)
            if archived.suggestion_id == suggestion_id
        ),
        None,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No dismissed suggestion {suggestion_id!r}")
    return DismissedSuggestion.from_domain(entry)


@router.put(
    "/dismissed-suggestions/{suggestion_id}", status_code=201, responses=created_or_replaced(DismissedSuggestion)
)
def put_dismissed_suggestion(
    suggestion_id: str,
    request: DismissSuggestionRequest,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> DismissedSuggestion:
    """Archive a suggestion so it stops being proposed, without discarding it.

    A `PUT` at the id, not a `POST` to the collection, and the only upsert
    in this API that could honestly become one: the archive entry's id is
    the suggestion's own id, which the client already holds — it is reading
    it off `GET /transfer-suggestions` or `GET /duplicate-suggestions` and
    used to send it in the request body. Nothing is derived server-side, so
    the caller can name the address, which is what makes this an idempotent
    replace at a known URL rather than a submission to a collection.

    `201` with a `Location` when this created the archive entry, `200` when
    it replaced one already there — RFC 9110's own answer for `PUT`.

    Returns
    -------
    DismissedSuggestion
        The archived entry just persisted.
    """
    entry = DomainDismissedSuggestion(
        suggestion_id=suggestion_id,
        kind=request.kind,
        description=request.description,
        dismissed_at=datetime.now(tz=UTC),
    )
    if dismiss_suggestion(session, user_id, entry):
        location_of(http_request, response, "get_dismissed_suggestion", suggestion_id=suggestion_id)
    else:
        response.status_code = 200
    return DismissedSuggestion.from_domain(entry)


@router.delete("/dismissed-suggestions/{suggestion_id}", status_code=204)
def delete_dismissed_suggestion(
    suggestion_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Restore a dismissed suggestion so it can be proposed again.

    Raises
    ------
    HTTPException
        404 if no archived entry has this id.
    """
    if not undismiss_suggestion(session, user_id, suggestion_id):
        raise HTTPException(status_code=404, detail=f"No dismissed suggestion {suggestion_id!r}")
