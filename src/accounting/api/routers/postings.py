"""Posting-resolution endpoints — mirrors `accounting.ledger.*`: categorization, duplicates, transfers, pending."""

from __future__ import annotations

import io
import uuid
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    DismissSuggestionRequest,
    DuplicateGroup,
    PostingIdResponse,
    PostingMergeIdResponse,
    PostingMergeUpsert,
    PostingRow,
    SuggestionIdResponse,
    TransferLinkCreate,
    TransferLinkIdResponse,
    TransferSuggestion,
    ValidatePendingRequest,
    ValidatePendingResult,
)
from accounting.api.dependencies import _resolved_postings_and_store, state
from accounting.importers.ingest import load_ledger
from accounting.ledger.categorization import resolved_transfer_rule_ids_by_transaction
from accounting.ledger.duplicates import DuplicateGroup as DuplicateGroupData
from accounting.ledger.duplicates import find_duplicate_candidates
from accounting.ledger.pending import resolve_pending_suggestion
from accounting.ledger.transfers import find_unmatched_transfer_candidates, make_transfer_link
from accounting.models import (
    DismissedSuggestion,
    ManualOverride,
    Posting,
    PostingMerge,
    PostingSplit,
    PostingSplitLeg,
    TransferLink,
)
from accounting.repositories.interpretation import (
    delete_posting_split,
    dismiss_suggestion,
    dismissed_suggestion_ids,
    list_dismissed_suggestions,
    load_overrides,
    load_overrides_for_postings,
    remove_posting_merge,
    remove_transfer_link,
    save_overrides_for_postings,
    save_posting_split,
    undismiss_suggestion,
)
from accounting.store import (
    load_store,
    save_store,
)
from accounting.utils.statement_archive import StatementArchive
from db.current_user import get_current_user_id
from db.money import ZERO, quantize_money
from db.session import get_db

router = APIRouter()


@router.get("/postings")
def get_postings(
    session: Annotated[Session, Depends(get_db)], user_id: Annotated[uuid.UUID, Depends(get_current_user_id)]
) -> list[PostingRow]:
    """Return every posting, resolved against the current rules and manual overrides.

    Each row also carries `pending_source` (`"ai"`, `"pattern"`, or
    `None`) and `pending_selected` — an automated categorizer's
    not-yet-confirmed suggestion, and whether it's currently checked for
    the next "validate selection" action (see `ledger.pending`) —
    `resolved_by_transfer_rule_id`, naming which `TransferRule` (if any)
    resolved this posting's transaction, purely for display (see
    `ledger.categorization.resolved_transfer_rule_ids_by_transaction`) —
    and `manual_transfer_override_posting_id`, the same thing for a manual
    "flag as transfer" (`ManualOverride.account_id`) instead of a rule. A
    manual override always wins if both somehow apply to the same
    transaction (it's applied after rules — see
    `api.dependencies._resolved_postings_and_store`), so
    `resolved_by_transfer_rule_id` is suppressed whenever
    `manual_transfer_override_posting_id` is set for that transaction —
    see `PostingRow`'s own docstring.

    Returns
    -------
    list[PostingRow]
        One row per posting.
    """
    postings, store = _resolved_postings_and_store(session, user_id)
    overrides = load_overrides(session, user_id)
    # Recomputed from the raw ledger rather than threaded through
    # `_resolved_postings_and_store`'s return value — that function's
    # signature is shared by every other endpoint in this module, and this
    # is purely a display concern only `get_postings` needs.
    raw = load_ledger(session, user_id)
    resolved_by_rule = resolved_transfer_rule_ids_by_transaction(raw, store.rules, store.accounts)
    rows = postings.to_dicts()

    posting_id_to_transaction_id = {row["posting_id"]: row["transaction_id"] for row in rows}
    manual_override_posting_by_transaction: dict[str, str] = {}
    for posting_id, posting_override in overrides.items():
        if posting_override.account_id is None:
            continue
        transaction_id = posting_id_to_transaction_id.get(posting_id)
        if transaction_id is not None:
            manual_override_posting_by_transaction[transaction_id] = posting_id

    for row in rows:
        override = overrides.get(row["posting_id"])
        row["pending_source"] = override.pending_source if override is not None else None
        row["pending_selected"] = override.pending_selected if override is not None else True
        manual_override_posting_id = manual_override_posting_by_transaction.get(row["transaction_id"])
        row["manual_transfer_override_posting_id"] = manual_override_posting_id
        row["resolved_by_transfer_rule_id"] = (
            None if manual_override_posting_id is not None else resolved_by_rule.get(row["transaction_id"])
        )
    return [PostingRow(**row) for row in rows]


@router.get("/ledger/export")
def get_ledger_export(
    session: Annotated[Session, Depends(get_db)], user_id: Annotated[uuid.UUID, Depends(get_current_user_id)]
) -> list[Posting]:
    """Export the raw ledger, exactly as imported — before any rule, override, split, or merge is applied.

    Returns
    -------
    list[Posting]
        Every posting for the user's own backup. See `GET /postings` for
        the same data after every rule/override/split/merge is applied on
        top — what the Transactions page actually shows.
    """
    return [Posting(**row) for row in load_ledger(session, user_id).to_dicts()]


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
    save_overrides_for_postings([posting_id], {posting_id: override}, session, user_id)
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
    postings, _store = _resolved_postings_and_store(session, user_id)
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
    save_posting_split(split, session, user_id)
    return split


@router.delete("/postings/{posting_id}/split")
def delete_posting_split_route(
    posting_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> PostingIdResponse:
    """Undo a posting split, restoring the single original posting.

    Returns
    -------
    PostingIdResponse
    """
    delete_posting_split(session, user_id, posting_id)
    session.commit()
    return PostingIdResponse(posting_id=posting_id)


@router.put("/posting-merges")
def put_posting_merges(
    merges: dict[str, PostingMerge],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> dict[str, PostingMerge]:
    """Replace the whole posting-merge map, keyed by `merge_id`.

    Returns
    -------
    dict[str, PostingMerge]
        The merges just persisted.
    """
    store = load_store(session, user_id)
    store = store.model_copy(update={"posting_merges": merges})
    save_store(store, session, user_id)
    return store.posting_merges


def _merge_id(kept_transaction_id: str) -> str:
    """Derive a posting merge's id from the transaction it keeps.

    Returns
    -------
    str
    """
    return f"merge:{kept_transaction_id}"


@router.post("/posting-merges")
def post_posting_merge(
    request: PostingMergeUpsert,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> PostingMerge:
    """Upsert one duplicate-resolution decision, without touching any other merge already recorded.

    Returns
    -------
    PostingMerge
        The merge just persisted.
    """
    merge = PostingMerge(
        merge_id=_merge_id(request.kept_transaction_id),
        kept_transaction_id=request.kept_transaction_id,
        duplicate_transaction_ids=request.duplicate_transaction_ids,
        description=request.description,
    )
    store = load_store(session, user_id)
    store = store.model_copy(update={"posting_merges": {**store.posting_merges, merge.merge_id: merge}})
    save_store(store, session, user_id)
    return merge


@router.delete("/posting-merges/{merge_id}")
def delete_posting_merge(
    merge_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> PostingMergeIdResponse:
    """Undo one duplicate-resolution decision, restoring the merged-away transactions to the ledger.

    Returns
    -------
    PostingMergeIdResponse

    Raises
    ------
    HTTPException
        404 if no merge with this id exists.
    """
    if not remove_posting_merge(session, user_id, merge_id):
        raise HTTPException(status_code=404, detail=f"Posting merge {merge_id!r} not found")
    session.commit()
    return PostingMergeIdResponse(merge_id=merge_id)


@router.post("/transfer-links")
def post_transfer_link(
    request: TransferLinkCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TransferLink:
    """Confirm two transactions as the two sides of one real-world transfer.

    Neither transaction's own posting is ever touched — see
    `ledger.transfers.apply_transfer_links` for how this changes
    classification instead. Re-confirming the exact same pair (from
    either side) is a no-op, returning the existing link.

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
        *different* transfer link.
    """
    if request.transaction_id_a == request.transaction_id_b:
        raise HTTPException(status_code=400, detail="Cannot link a transaction to itself")

    link = make_transfer_link(request.transaction_id_a, request.transaction_id_b, source="manual")
    store = load_store(session, user_id)

    already_this_link = next((existing for existing in store.transfer_links if existing.link_id == link.link_id), None)
    if already_this_link is not None:
        return already_this_link

    linked_transaction_ids = {
        transaction_id
        for existing in store.transfer_links
        for transaction_id in (existing.transaction_id_a, existing.transaction_id_b)
    }
    for transaction_id in (link.transaction_id_a, link.transaction_id_b):
        if transaction_id in linked_transaction_ids:
            raise HTTPException(
                status_code=409, detail=f"Transaction {transaction_id!r} is already part of another transfer link"
            )

    raw = load_ledger(session, user_id)
    posting_to_transaction = dict(zip(raw["posting_id"].to_list(), raw["transaction_id"].to_list(), strict=True))
    split_transaction_ids = {
        posting_to_transaction[posting_id]
        for posting_id in store.posting_splits
        if posting_id in posting_to_transaction
    }
    for transaction_id in (link.transaction_id_a, link.transaction_id_b):
        if transaction_id in split_transaction_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Transaction {transaction_id!r} has already been split and can't be linked",
            )

    store = store.model_copy(update={"transfer_links": [*store.transfer_links, link]})
    save_store(store, session, user_id)
    return link


@router.delete("/transfer-links/{link_id}")
def delete_transfer_link(
    link_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TransferLinkIdResponse:
    """Undo a confirmed transfer link, restoring both transactions to their prior classification.

    Returns
    -------
    TransferLinkIdResponse

    Raises
    ------
    HTTPException
        404 if no link with this id exists.
    """
    if not remove_transfer_link(session, user_id, link_id):
        raise HTTPException(status_code=404, detail=f"Transfer link {link_id!r} not found")
    session.commit()
    return TransferLinkIdResponse(link_id=link_id)


@router.post("/postings/validate-pending")
def post_validate_pending(
    payload: ValidatePendingRequest,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> ValidatePendingResult:
    """Resolve every listed posting's pending suggestion per its own `pending_selected` flag.

    Only ever touches postings named in `payload.posting_ids` — the
    caller's current filtered view — so a pending suggestion sitting
    outside that view is never affected by this call, per the "validate
    selection" button's contract. A posting with no override, or one
    whose override isn't pending, is silently skipped.

    Returns
    -------
    ValidatePendingResult
    """
    overrides = load_overrides_for_postings(session, user_id, payload.posting_ids)
    accepted = reverted = 0
    for posting_id in payload.posting_ids:
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
    save_overrides_for_postings(payload.posting_ids, overrides, session, user_id)
    return ValidatePendingResult(accepted=accepted, reverted=reverted)


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
        (see `POST /dismissed-suggestions`).
    """
    postings, store = _resolved_postings_and_store(session, user_id)
    candidates = find_unmatched_transfer_candidates(
        postings, window_days=window_days, existing_links=store.transfer_links
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
        group already dismissed (see `POST /dismissed-suggestions`).
    """
    postings, _store = _resolved_postings_and_store(session, user_id)
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
    return list_dismissed_suggestions(session, user_id)


@router.post("/dismissed-suggestions")
def post_dismissed_suggestion(
    request: DismissSuggestionRequest,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> DismissedSuggestion:
    """Archive a suggestion so it stops being proposed, without discarding it.

    Returns
    -------
    DismissedSuggestion
        The archived entry just persisted.
    """
    entry = DismissedSuggestion(
        suggestion_id=request.suggestion_id,
        kind=request.kind,
        description=request.description,
        dismissed_at=datetime.now(tz=UTC),
    )
    dismiss_suggestion(session, user_id, entry)
    return entry


@router.delete("/dismissed-suggestions/{suggestion_id}")
def delete_dismissed_suggestion(
    suggestion_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> SuggestionIdResponse:
    """Restore a dismissed suggestion so it can be proposed again.

    Returns
    -------
    SuggestionIdResponse
        The entry just restored.

    Raises
    ------
    HTTPException
        404 if no archived entry has this id.
    """
    if not undismiss_suggestion(session, user_id, suggestion_id):
        raise HTTPException(status_code=404, detail=f"No dismissed suggestion {suggestion_id!r}")
    return SuggestionIdResponse(suggestion_id=suggestion_id)
