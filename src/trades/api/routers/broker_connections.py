"""The broker connections an account may pull its value from.

A collection of rows, not a preference — which is why it is here rather
than in `settings.py`, where it used to sit as the one route in that file
whose path was not under `/settings`. `settings.py` holds key/value
preferences a user sets; a broker connection is created as a side effect of
a sync and only ever read back.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import trades.db as tdb
from db.current_user import get_current_user_id
from db.session import get_db
from trades.api.api_models import BrokerConnection

router = APIRouter()


@router.get("/broker-connections")
def get_broker_connections(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[BrokerConnection]:
    """List this user's broker connections — the only things an account may pull its value from.

    Returns
    -------
    list[BrokerConnection]
        Ordered by broker then id, so the frontend's list is stable across
        requests. Empty until a sync has actually created a connection.
    """
    rows = session.query(tdb.BrokerConnection).filter_by(user_id=user_id).all()
    connections = [BrokerConnection(connection_id=row.id, broker=row.broker) for row in rows]
    return sorted(connections, key=lambda connection: (connection.broker, str(connection.connection_id)))
