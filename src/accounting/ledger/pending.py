"""Accept/reject lifecycle for a posting's not-yet-confirmed category suggestion.

An automated categorizer (an LLM call, see `api.post_ai_suggest_category`,
or a `CategoryPattern` match, see `api.post_pattern_suggest_category`)
never categorizes a posting outright — it stages a `ManualOverride` whose
`pending_source` names which categorizer produced it and snapshots the
posting's previous category/subcategory, so the posting keeps rendering
as "temporary" until the user resolves it here: accepting keeps the
suggested category, rejecting restores exactly what was there before.
"""

from __future__ import annotations

from accounting.models import ManualOverride, PendingSuggestionSource

_MERGEABLE_FIELDS = ("account_id", "category_id", "subcategory_id", "tag_ids")


def stage_pending_suggestion(
    existing: ManualOverride | None,
    category_id: str,
    subcategory_id: str | None,
    source: PendingSuggestionSource,
    previous_category_id: str | None,
    previous_subcategory_id: str | None,
) -> ManualOverride:
    """Build the override that stages a not-yet-confirmed suggestion, preserving any other field already set.

    Parameters
    ----------
    existing
        Whatever override already exists for this posting, if any — its
        `account_id`/`tag_ids` are preserved, only the category fields
        (and the pending markers) are overwritten.
    category_id, subcategory_id
        The suggested category — applied optimistically.
    source
        Which categorizer produced this suggestion.
    previous_category_id, previous_subcategory_id
        The posting's fully-resolved category/subcategory *before* this
        suggestion — snapshotted here (rather than recomputed later) so
        rejection can restore them exactly, even when the previous value
        came from a `TransferRule` rather than a prior override.

    Returns
    -------
    ManualOverride
    """
    merged = existing.model_dump() if existing is not None else {}
    merged.update({
        "category_id": category_id,
        "subcategory_id": subcategory_id,
        "pending_source": source,
        "pending_selected": True,
        "pending_previous_category_id": previous_category_id,
        "pending_previous_subcategory_id": previous_subcategory_id,
    })
    return ManualOverride(**merged)


def resolve_pending_suggestion(existing: ManualOverride) -> ManualOverride | None:
    """Resolve one posting's pending suggestion per its own `pending_selected` flag.

    Accepts (keeps the suggested category, clears the pending markers) if
    `pending_selected` is `True`; otherwise rejects (restores the
    snapshotted previous category/subcategory, clears the pending
    markers). Only ever meaningful on an override that has `pending_source` set.

    Returns
    -------
    ManualOverride or None
        The resolved override, or `None` if resolving it leaves every
        field empty — the caller should drop the entry entirely rather
        than persist a no-op override.
    """
    if existing.pending_selected:
        resolved = existing.model_copy(
            update={
                "pending_source": None,
                "pending_previous_category_id": None,
                "pending_previous_subcategory_id": None,
            }
        )
    else:
        resolved = existing.model_copy(
            update={
                "category_id": existing.pending_previous_category_id,
                "subcategory_id": existing.pending_previous_subcategory_id,
                "pending_source": None,
                "pending_previous_category_id": None,
                "pending_previous_subcategory_id": None,
            }
        )
    if all(getattr(resolved, field) is None for field in _MERGEABLE_FIELDS):
        return None
    return resolved
