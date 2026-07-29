"""Other-asset endpoints — create, replace and delete the manually-valued assets held outside any account."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from accounting.api.api_models import OtherAssetCreate
from accounting.models import OtherAsset
from accounting.repositories.taxonomy import delete_other_asset, insert_other_asset, replace_other_assets
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()


@router.post("/other-assets")
def post_other_asset(
    request: OtherAssetCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> OtherAsset:
    """Create one new manually-entered asset, without touching any other asset already saved.

    `asset_id` is server-minted — two assets can validly share every
    other field (e.g. two rental properties both named "Rental"), so
    there's no natural key two "the same" asset would collide on.

    Returns
    -------
    OtherAsset
        The asset just persisted.
    """
    asset = OtherAsset(
        asset_id=f"asset:{uuid.uuid4().hex}",
        name=request.name,
        value=request.value,
        currency=request.currency,
        note=request.note,
    )
    insert_other_asset(session, user_id, asset)
    return asset


@router.put("/other-assets")
def put_other_assets(
    other_assets: list[OtherAsset],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[OtherAsset]:
    """Replace the whole manually-entered-asset list.

    Returns
    -------
    list[OtherAsset]
        The assets just persisted.
    """
    replace_other_assets(session, user_id, other_assets)
    session.commit()
    return other_assets


@router.delete("/other-assets/{asset_id}", status_code=204)
def delete_other_asset_route(
    asset_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Delete one manually-entered asset, without touching any other. Idempotent, no version check.

    Replaces deleting an asset by re-sending the whole list minus one; see
    `repositories.taxonomy.delete_other_asset`.


    Raises
    ------
    HTTPException
        404 if no asset with `asset_id` exists.
    """
    if not delete_other_asset(session, user_id, asset_id):
        raise HTTPException(status_code=404, detail=f"Other asset {asset_id!r} not found")
    session.commit()
