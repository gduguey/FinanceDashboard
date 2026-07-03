"""Pydantic schemas validated at the external boundaries of this codebase"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trades.config import LedgerEventType


class RawTrade(BaseModel):
    """The canonical "invested schedule" trade row (`transactions.py`'s
    input): a broker CSV export row, or a `BUY` event pulled off the ledger
    by `preprocessing.standardize_ibkr_trades`. Field names double as the
    column names everywhere this shape is used — there's no separate
    schema declaration to keep in sync."""

    model_config = ConfigDict(populate_by_name=True)

    trade_date: date = Field(alias="Date")
    symbol: str = Field(alias="Symbol", min_length=1)
    shares: float = Field(alias="Shares", gt=0)
    usd_spent: float = Field(alias="USD Spent", gt=0)

    @field_validator("usd_spent", mode="before")
    @classmethod
    def parse_currency(cls, value: object) -> object:
        """Broker export formats money as "$1,234.56"; strip it to a float."""
        if isinstance(value, str):
            return value.replace("$", "").replace(",", "").strip()
        return value

    @field_validator("symbol", mode="before")
    @classmethod
    def strip_symbol(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class LedgerEvent(BaseModel):
    """One immutable row of the transaction ledger — the source of truth
    everything else (positions, cost basis, returns) is meant to be
    computed from by replaying the whole ledger (see docs/architecture.md,
    "The ledger"). Field names double as the ledger's column names.

    `shares`/`price` are `None` for event types with no share count or
    per-share price (everything except `BUY`/`SELL`). `amount` is always
    the non-negative magnitude of the cash effect — direction comes from
    `event_type` alone, never re-encoded as a sign.
    """

    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(min_length=1)
    event_datetime: datetime
    symbol: str = Field(min_length=1)
    event_type: LedgerEventType
    shares: float | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, gt=0)
    amount: float = Field(ge=0)
    currency: str = Field(min_length=1)
    meta: dict[str, str] = Field(default_factory=dict)

    @field_validator("symbol", mode="before")
    @classmethod
    def strip_symbol(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class PriceObservation(BaseModel):
    """One validated (symbol, day, close) triple from the price API."""

    symbol: str = Field(min_length=1)
    price_date: date
    close: float = Field(gt=0)
