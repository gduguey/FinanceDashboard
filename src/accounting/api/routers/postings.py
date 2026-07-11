"""Posting-resolution endpoints — mirrors `accounting.ledger.*`: categorization, duplicates, transfers, pending."""

from __future__ import annotations

import io
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
    PostingRow,
    SuggestionIdResponse,
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
from accounting.ledger.transfers import find_unmatched_transfer_candidates
from accounting.models import DismissedSuggestion, ManualOverride, Posting, PostingMerge, PostingSplit, PostingSplitLeg
from accounting.store import load_overrides, load_store, save_overrides, save_store
from accounting.utils.statement_archive import DEFAULT_USER_ID, StatementArchive
from db.session import get_db

router = APIRouter()


@router.get("/postings")
def get_postings(session: Annotated[Session, Depends(get_db)]) -> list[PostingRow]:
    """Return every posting, resolved against the current rules and manual overrides.

    Each row also carries `pending_source` (`"ai"`, `"pattern"`, or
    `None`) and `pending_selected` — an automated categorizer's
    not-yet-confirmed suggestion, and whether it's currently checked for
    the next "validate selection" action (see `ledger.pending`) — and
    `resolved_by_transfer_rule_id`, naming which `TransferRule` (if any) resolved this
    posting's transaction, purely for display (see
    `ledger.categorization.resolved_transfer_rule_ids_by_transaction`).

    Returns
    -------
    list[PostingRow]
        One row per posting.
    """
    postings, store = _resolved_postings_and_store(state.config, session)
    overrides = load_overrides(session)
    # Recomputed from the raw ledger rather than threaded through
    # `_resolved_postings_and_store`'s return value — that function's
    # signature is shared by every other endpoint in this module, and this
    # is purely a display concern only `get_postings` needs.
    raw = load_ledger(session)
    resolved_by_rule = resolved_transfer_rule_ids_by_transaction(raw, store.rules, store.accounts)
    rows = postings.to_dicts()
    for row in rows:
        override = overrides.get(row["posting_id"])
        row["pending_source"] = override.pending_source if override is not None else None
        row["pending_selected"] = override.pending_selected if override is not None else True
        row["resolved_by_transfer_rule_id"] = resolved_by_rule.get(row["transaction_id"])
    return [PostingRow(**row) for row in rows]


@router.get("/ledger/export")
def get_ledger_export(session: Annotated[Session, Depends(get_db)]) -> list[Posting]:
    """Export the raw ledger, exactly as imported — before any rule, override, split, or merge is applied.

    Returns
    -------
    list[Posting]
        Every posting for the user's own backup. See `GET /postings` for
        the same data after every rule/override/split/merge is applied on
        top — what the Transactions page actually shows.
    """
    return [Posting(**row) for row in load_ledger(session).to_dicts()]


@router.get("/statements/export")
def get_statements_export() -> Response:
    """Zip every raw statement archived from an import (CSV or PDF, verbatim as uploaded) for download.

    Returns
    -------
    fastapi.Response
        A `.zip` attachment, one entry per archived file, empty if
        nothing has been imported yet.
    """
    buffer = io.BytesIO()
    archive = StatementArchive(state.config.raw_statement_dir, f"statements/{DEFAULT_USER_ID}")
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
    posting_id: str, override: ManualOverride, session: Annotated[Session, Depends(get_db)]
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
    overrides = load_overrides(session)
    existing = overrides.get(posting_id)
    if existing is not None:
        merged = existing.model_dump()
        merged.update(override.model_dump(include=override.model_fields_set))
        override = ManualOverride(**merged)
    overrides[posting_id] = override
    save_overrides(overrides, session)
    return override


_SPLIT_ZERO_SUM_TOLERANCE = 1e-6


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
    posting_id: str, legs: list[PostingSplitLeg], session: Annotated[Session, Depends(get_db)]
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
    postings, store = _resolved_postings_and_store(state.config, session)
    current_amount = _current_amount_for_split(postings, posting_id)
    if current_amount is None:
        raise HTTPException(status_code=404, detail=f"Posting {posting_id!r} not found")
    total = sum(leg.amount for leg in legs)
    if abs(total - current_amount) > _SPLIT_ZERO_SUM_TOLERANCE:
        raise HTTPException(
            status_code=400, detail=f"Legs sum to {total}, not the posting's own amount of {current_amount}"
        )
    split = PostingSplit(posting_id=posting_id, legs=legs)
    store = store.model_copy(update={"posting_splits": {**store.posting_splits, posting_id: split}})
    save_store(store, session)
    return split


@router.delete("/postings/{posting_id}/split")
def delete_posting_split(posting_id: str, session: Annotated[Session, Depends(get_db)]) -> PostingIdResponse:
    """Undo a posting split, restoring the single original posting.

    Returns
    -------
    PostingIdResponse
    """
    store = load_store(session)
    remaining = {pid: split for pid, split in store.posting_splits.items() if pid != posting_id}
    store = store.model_copy(update={"posting_splits": remaining})
    save_store(store, session)
    return PostingIdResponse(posting_id=posting_id)


@router.put("/posting-merges")
def put_posting_merges(
    merges: dict[str, PostingMerge], session: Annotated[Session, Depends(get_db)]
) -> dict[str, PostingMerge]:
    """Replace the whole posting-merge map, keyed by `merge_id`.

    Returns
    -------
    dict[str, PostingMerge]
        The merges just persisted.
    """
    store = load_store(session)
    store = store.model_copy(update={"posting_merges": merges})
    save_store(store, session)
    return store.posting_merges


@router.post("/postings/validate-pending")
def post_validate_pending(
    payload: ValidatePendingRequest, session: Annotated[Session, Depends(get_db)]
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
    overrides = load_overrides(session)
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
    save_overrides(overrides, session)
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
    *, window_days: int = 3, session: Annotated[Session, Depends(get_db)]
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
    postings, store = _resolved_postings_and_store(state.config, session)
    rows = find_unmatched_transfer_candidates(postings, window_days=window_days).to_dicts()
    return [
        TransferSuggestion(**row, suggestion_id=_transfer_suggestion_id(row))
        for row in rows
        if _transfer_suggestion_id(row) not in store.dismissed_suggestions
    ]


@router.get("/duplicate-suggestions")
def get_duplicate_suggestions(
    *, window_days: int = 3, session: Annotated[Session, Depends(get_db)]
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
    postings, store = _resolved_postings_and_store(state.config, session)
    groups = find_duplicate_candidates(postings, window_days=window_days)
    return [
        DuplicateGroup(**asdict(group), suggestion_id=_duplicate_suggestion_id(group))
        for group in groups
        if _duplicate_suggestion_id(group) not in store.dismissed_suggestions
    ]


@router.get("/dismissed-suggestions")
def get_dismissed_suggestions(session: Annotated[Session, Depends(get_db)]) -> list[DismissedSuggestion]:
    """List every archived (dismissed) suggestion, most recently dismissed first.

    Returns
    -------
    list[DismissedSuggestion]
    """
    store = load_store(session)
    return sorted(store.dismissed_suggestions.values(), key=lambda entry: entry.dismissed_at, reverse=True)


@router.post("/dismissed-suggestions")
def post_dismissed_suggestion(
    request: DismissSuggestionRequest, session: Annotated[Session, Depends(get_db)]
) -> DismissedSuggestion:
    """Archive a suggestion so it stops being proposed, without discarding it.

    Returns
    -------
    DismissedSuggestion
        The archived entry just persisted.
    """
    store = load_store(session)
    entry = DismissedSuggestion(
        suggestion_id=request.suggestion_id,
        kind=request.kind,
        description=request.description,
        dismissed_at=datetime.now(tz=UTC),
    )
    store = store.model_copy(
        update={"dismissed_suggestions": {**store.dismissed_suggestions, entry.suggestion_id: entry}}
    )
    save_store(store, session)
    return entry


@router.delete("/dismissed-suggestions/{suggestion_id}")
def delete_dismissed_suggestion(
    suggestion_id: str, session: Annotated[Session, Depends(get_db)]
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
    store = load_store(session)
    if suggestion_id not in store.dismissed_suggestions:
        raise HTTPException(status_code=404, detail=f"No dismissed suggestion {suggestion_id!r}")
    remaining = {key: value for key, value in store.dismissed_suggestions.items() if key != suggestion_id}
    store = store.model_copy(update={"dismissed_suggestions": remaining})
    save_store(store, session)
    return SuggestionIdResponse(suggestion_id=suggestion_id)
