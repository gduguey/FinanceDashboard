"""Tag endpoints — the tag list plus its create, rename-with-merge and delete writes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    TagCreate,
    TagIdResponse,
    TagRenamePreviewResponse,
    TagRenameRequest,
    TagRenameResponse,
)
from accounting.models import Tag
from accounting.repositories.taxonomy import delete_tag, load_tags, remap_tag_ids, replace_tags
from accounting.taxonomy import plan_tag_rename, seed_new_user_defaults, slugify
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()


@router.put("/tags")
def put_tags(
    tags: dict[str, Tag],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> dict[str, Tag]:
    """Replace the whole tag list.

    Returns
    -------
    dict[str, Tag]
        The tags just persisted, keyed by `tag_id`.
    """
    # See `put_categories`: a brand-new user's placeholder accounts and default
    # category tree are seeded together, and only on first contact.
    seed_new_user_defaults(session, user_id)
    replace_tags(session, user_id, tags.values())
    session.commit()
    return tags


@router.post("/tags")
def post_tag(
    request: TagCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Tag:
    """Create a new tag, refusing a same-name (case-insensitive) duplicate.

    Unlike `put_tags` (a whole-list replace, where a client-computed id
    that happens to collide with an existing one silently overwrites it),
    this only ever adds a tag — a name collision is rejected outright
    rather than clobbering the existing entry.

    Returns
    -------
    Tag
        The tag just persisted, including its computed `tag_id`.

    Raises
    ------
    HTTPException
        409 if a tag with this name (case-insensitive) already exists, or a
        distinct name collides with an existing tag's slug id.
    """
    existing_tags = load_tags(session, user_id)
    normalized_name = request.name.strip().lower()
    collision = any(tag.name.strip().lower() == normalized_name for tag in existing_tags.values())
    if collision:
        raise HTTPException(status_code=409, detail=f"A tag named {request.name!r} already exists")

    tag_id = f"tag:{slugify(request.name)}"
    if tag_id in existing_tags:
        # See post_category: the name check is case-insensitive, but the slug id
        # is lossy — guard against two distinct names colliding on it.
        raise HTTPException(
            status_code=409, detail=f"The name {request.name!r} is too similar to an existing tag — pick another"
        )
    new_tag = Tag(tag_id=tag_id, name=request.name)
    replace_tags(session, user_id, [new_tag], prune=False)
    session.commit()
    return new_tag


@router.delete("/tags/{tag_id}")
def delete_tag_route(
    tag_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TagIdResponse:
    """Delete one tag, without touching any other. Idempotent, no version check.

    Replaces deleting a tag by re-sending the whole tag list minus one
    (which risked a stale second delete resurrecting a just-removed tag);
    see `repositories.taxonomy.delete_tag`. A tag still applied to postings is
    removed from them too, via the `posting_tags` FK cascade.

    Returns
    -------
    TagIdResponse
        The tag id just deleted.

    Raises
    ------
    HTTPException
        404 if no tag with `tag_id` exists.
    """
    if not delete_tag(session, user_id, tag_id):
        raise HTTPException(status_code=404, detail=f"Tag {tag_id!r} not found")
    session.commit()
    return TagIdResponse(tag_id=tag_id)


@router.get("/tags/{tag_id}/rename-preview")
def get_tag_rename_preview(
    tag_id: str,
    name: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TagRenamePreviewResponse:
    """Report whether renaming `tag_id` to `name` would merge it into an existing tag.

    Calls the same pure `plan_tag_rename` `post_tag_rename` itself uses,
    but never persists anything — a caller can show a confirmation dialog
    first, and only actually call `POST /tags/{tag_id}/rename` once the
    user accepts.

    Returns
    -------
    TagRenamePreviewResponse
        `will_merge` is true if this rename would fold into an existing
        tag rather than just changing a name; `target_name` is that
        existing tag's name, or `None` when `will_merge` is false.

    Raises
    ------
    HTTPException
        404 if `tag_id` doesn't exist.
    """
    existing_tags = load_tags(session, user_id)
    if tag_id not in existing_tags:
        raise HTTPException(status_code=404, detail=f"Tag {tag_id!r} not found")

    _tags, id_remap = plan_tag_rename(existing_tags, tag_id, name)
    target_id = id_remap.get(tag_id)
    target_name = existing_tags[target_id].name if target_id is not None else None
    return TagRenamePreviewResponse(will_merge=target_id is not None, target_name=target_name)


@router.post("/tags/{tag_id}/rename")
def post_tag_rename(
    tag_id: str,
    request: TagRenameRequest,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TagRenameResponse:
    """Rename a tag, merging it into an existing same-named tag if there is one.

    A merge repoints every reference to the merged-away id — the
    `posting_tags` and `posting_override_tags` join tables (see
    `repositories.taxonomy.remap_tag_ids`) — before the merged-away tag itself is
    deleted, so a foreign key never briefly points at a row about to
    disappear.

    Returns
    -------
    TagRenameResponse
        `tags` is the full tag map after the change; `merged` is true if
        this rename actually folded into an existing tag rather than just
        changing a name.

    Raises
    ------
    HTTPException
        404 if `tag_id` doesn't exist.
    """
    existing_tags = load_tags(session, user_id)
    if tag_id not in existing_tags:
        raise HTTPException(status_code=404, detail=f"Tag {tag_id!r} not found")

    tags, id_remap = plan_tag_rename(existing_tags, tag_id, request.name)

    # Every reference to a merged-away tag must be repointed *before*
    # `replace_tags` prunes that tag row below — `posting_tags` foreign-keys
    # into `tags`, and while its cascade would let the delete through, it would
    # take the postings' tag rows with it instead of moving them.
    remap_tag_ids(id_remap, session, user_id)
    # With a prune: a merge removes the merged-away tag row itself.
    replace_tags(session, user_id, tags.values())
    session.commit()

    return TagRenameResponse(tags=tags, merged=bool(id_remap))
