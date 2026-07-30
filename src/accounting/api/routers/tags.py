"""Tag endpoints — read one tag, create, rename-with-merge, delete. No whole-list replace."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    TagCreate,
    TagRenamePreviewResponse,
    TagRenameRequest,
    TagRenameResponse,
)
from accounting.api.locations import CREATED_WITH_LOCATION, location_of
from accounting.models import Tag
from accounting.repositories.taxonomy import delete_tag, load_tags, remap_tag_ids, replace_tags
from accounting.taxonomy import plan_tag_rename, slugify
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()


@router.get("/tags/{tag_id}")
def get_tag(
    tag_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Tag:
    """Return one tag by id.

    The address `post_tag` advertises in its `Location` — see
    `api.locations.location_of` for why the header is resolved against
    this route rather than formatted by hand.

    Returns
    -------
    Tag

    Raises
    ------
    HTTPException
        404 if no tag has this id.
    """
    tag = load_tags(session, user_id).get(tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail=f"Tag {tag_id!r} not found")
    return tag


@router.post("/tags", status_code=201, responses=CREATED_WITH_LOCATION)
def post_tag(
    request: TagCreate,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Tag:
    """Create a new tag, refusing a same-name (case-insensitive) duplicate.

    The only way to add one: `PUT /tags`, a whole-list replace where a
    client-computed id colliding with an existing one silently overwrote
    it, is gone. This only ever adds a tag — a name collision is rejected
    outright rather than clobbering the existing entry.

    A genuine `201`: the id is the name's slug, but the two 409s below
    leave creation as this route's only outcome, so it never replaces
    anything and the status is not a hedge.

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
    location_of(http_request, response, "get_tag", tag_id=tag_id)
    return new_tag


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag_route(
    tag_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Delete one tag, without touching any other. Idempotent, no version check.

    Replaces deleting a tag by re-sending the whole tag list minus one
    (which risked a stale second delete resurrecting a just-removed tag);
    see `repositories.taxonomy.delete_tag`. A tag still applied to postings is
    removed from them too, via the `posting_tags` FK cascade.


    Raises
    ------
    HTTPException
        404 if no tag with `tag_id` exists.
    """
    if not delete_tag(session, user_id, tag_id):
        raise HTTPException(status_code=404, detail=f"Tag {tag_id!r} not found")
    session.commit()


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
