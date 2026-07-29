"""Category-pattern endpoints — create, edit and delete the description-match rules that suggest categories."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from accounting.api.api_models import CategoryPatternCreate, CategoryPatternIdResponse, CategoryPatternUpdate
from accounting.importers.common import row_hash
from accounting.models import CategoryPattern
from accounting.repositories.interpretation import (
    delete_category_pattern,
    replace_category_patterns,
    update_category_pattern,
    upsert_category_pattern,
)
from accounting.taxonomy import seed_new_user_defaults
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()


def _category_pattern_id(description_contains: str, category_id: str, subcategory_id: str | None) -> str:
    """Derive a category pattern's natural key from its own matching criteria.

    Returns
    -------
    str
    """
    return f"pattern:{row_hash(description_contains, category_id, subcategory_id or '')}"


@router.post("/category-patterns")
def post_category_pattern(
    request: CategoryPatternCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategoryPattern:
    """Create one new category pattern, without touching any other pattern already saved.

    Posting this again for the same `(description_contains, category_id,
    subcategory_id)` replaces that pattern rather than creating a duplicate.

    Returns
    -------
    CategoryPattern
        The pattern just persisted.
    """
    pattern = CategoryPattern(
        pattern_id=_category_pattern_id(request.description_contains, request.category_id, request.subcategory_id),
        description_contains=request.description_contains,
        category_id=request.category_id,
        subcategory_id=request.subcategory_id,
        priority=request.priority,
    )
    # The default category tree this pattern's `category_id` foreign-keys into has to exist first;
    # a no-op read for everyone but a brand-new user.
    seed_new_user_defaults(session, user_id)
    upsert_category_pattern(session, user_id, pattern)
    return pattern


@router.put("/category-patterns")
def put_category_patterns(
    category_patterns: dict[str, CategoryPattern],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> dict[str, CategoryPattern]:
    """Replace the whole category-pattern list — the description-match suggestion source, distinct from `TransferRule`.

    Returns
    -------
    dict[str, CategoryPattern]
        The patterns just persisted, keyed by `pattern_id`.
    """
    seed_new_user_defaults(session, user_id)  # see the equivalent note in `post_category_pattern`
    replace_category_patterns(session, user_id, category_patterns.values())
    session.commit()
    return category_patterns


@router.patch("/category-patterns/{pattern_id}")
def patch_category_pattern(
    pattern_id: str,
    request: CategoryPatternUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategoryPattern:
    """Update one existing category pattern in place, without touching any other pattern already saved.

    A true per-resource write — see `repositories.interpretation.update_category_pattern`. Guarded by
    `request.expected_version`, this pattern's own row version.

    Returns
    -------
    CategoryPattern
        The pattern as persisted after the update.

    Raises
    ------
    HTTPException
        404 if no pattern with `pattern_id` exists.
    """
    pattern = CategoryPattern(
        pattern_id=pattern_id,
        description_contains=request.description_contains,
        category_id=request.category_id,
        subcategory_id=request.subcategory_id,
        priority=request.priority,
        active=request.active,
    )
    updated = update_category_pattern(session, user_id, pattern, request.expected_version)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Category pattern {pattern_id!r} not found")
    session.commit()
    return updated


@router.delete("/category-patterns/{pattern_id}")
def delete_category_pattern_route(
    pattern_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategoryPatternIdResponse:
    """Delete one category pattern, without touching any other pattern already saved.

    No version check — see `repositories.interpretation.delete_category_pattern`.

    Returns
    -------
    CategoryPatternIdResponse
        The pattern id just deleted.

    Raises
    ------
    HTTPException
        404 if no pattern with `pattern_id` exists.
    """
    deleted = delete_category_pattern(session, user_id, pattern_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Category pattern {pattern_id!r} not found")
    session.commit()
    return CategoryPatternIdResponse(pattern_id=pattern_id)
