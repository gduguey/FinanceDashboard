"""Other-asset endpoints — read, create and delete the manually-valued assets held outside any account."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from accounting.api.api_models import OtherAssetCreate
from accounting.api.entities import OtherAsset
from accounting.api.locations import CREATED_WITH_LOCATION, location_of
from accounting.models import OtherAsset as DomainOtherAsset
from accounting.repositories.taxonomy import (
    delete_other_asset,
    insert_other_asset,
    load_other_assets,
)
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()


@router.get("/other-assets/{asset_id}")
def get_other_asset(
    asset_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> OtherAsset:
    """Return one manually-entered asset by id — the address `post_other_asset` advertises.

    Returns
    -------
    OtherAsset

    Raises
    ------
    HTTPException
        404 if no asset has this id.
    """
    asset = next((a for a in load_other_assets(session, user_id) if a.asset_id == asset_id), None)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Other asset {asset_id!r} not found")
    return OtherAsset.from_domain(asset)


@router.post("/other-assets", status_code=201, responses=CREATED_WITH_LOCATION)
def post_other_asset(
    request: OtherAssetCreate,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> OtherAsset:
    """Create one new manually-entered asset, without touching any other asset already saved.

    `asset_id` is server-minted — two assets can validly share every
    other field (e.g. two rental properties both named "Rental"), so
    there's no natural key two "the same" asset would collide on. That is
    also why the `201` is unconditional: with no natural key there is
    nothing this route could replace. Editing an asset's value means
    deleting it and creating the new one; `PUT /other-assets` used to
    replace the whole list and is gone.

    Returns
    -------
    OtherAsset
        The asset just persisted.
    """
    asset = DomainOtherAsset(
        asset_id=f"asset:{uuid.uuid4().hex}",
        name=request.name,
        value=request.value,
        currency=request.currency,
        note=request.note,
    )
    insert_other_asset(session, user_id, asset)
    location_of(http_request, response, "get_other_asset", asset_id=asset.asset_id)
    return OtherAsset.from_domain(asset)


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
