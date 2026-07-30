"""The wire shape of every investing entity an endpoint returns.

The counterpart of `accounting.api.entities`, and for the same reason: a
route's return annotation is its whole response contract, so annotating one
against `trades.models` would ship every field added there for internal
reasons to every client. There is one entity here rather than twenty because
`trades`' endpoints answer with computed views (`api_models.Overview`,
`Position`, …) rather than with stored rows — `GET /ledger/export` is the
one that hands back the ledger itself.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from db.money import Money, Shares
from trades.config import LedgerEventType


class LedgerEvent(BaseModel):
    """One immutable row of the transaction ledger, as exported.

    Mirrors `trades.models.LedgerEvent`'s nine wire fields and none of its
    machinery: not `polars_schema`, the Polars projection's column types,
    which is a fact about the analytics frame rather than about the row a
    client reads, and not the two validators, which police what may be
    *written* to the ledger. This model only ever describes rows already
    stored, so re-policing them here could only turn a backup export of an
    already-persisted row into a 500.
    """

    event_id: str = Field(min_length=1)
    event_datetime: datetime
    symbol: str = Field(min_length=1)
    event_type: LedgerEventType
    shares: Shares | None = Field(default=None, gt=0)
    price: Money | None = Field(default=None, gt=0)
    amount: Money = Field(ge=0)
    currency: str = Field(min_length=1)
    meta: dict[str, str] = Field(default_factory=dict)
