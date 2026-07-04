"""Pydantic schemas validated at the external boundaries of this codebase."""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trades.config import LedgerEventType


class LedgerEvent(BaseModel):
    """One immutable row of the transaction ledger.

    Field names double as the ledger's column names. `shares`/`price` are
    `None` for event types with no share count or per-share price
    (everything except `BUY`/`SELL`). `amount` is always the non-negative
    magnitude of the cash effect; direction comes from `event_type` alone,
    never a sign.
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

    polars_schema: ClassVar[dict[str, type[pl.DataType] | pl.DataType]] = {
        "event_id": pl.Utf8,
        "event_datetime": pl.Datetime("us"),
        "symbol": pl.Utf8,
        "event_type": pl.Utf8,
        "shares": pl.Float64,
        "price": pl.Float64,
        "amount": pl.Float64,
        "currency": pl.Utf8,
        "meta": pl.Object,
    }

    @field_validator("symbol", mode="before")
    @classmethod
    def strip_symbol(cls, value: object) -> object:
        """Normalize a ticker symbol to stripped, upper-case form.

        Parameters
        ----------
        value
            The raw field value, before pydantic's type coercion.

        Returns
        -------
        object
            The normalized symbol, unchanged if it was not a string.
        """
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_event_type_invariants(self) -> LedgerEvent:
        """Validate that event-type-specific field constraints are satisfied.

        Event types have specific requirements about which fields must be present:
        - BUY, SELL: must have shares and price
        - All others: must NOT have shares or price (both None)

        Parameters
        ----------
        self
            The fully-parsed `LedgerEvent` instance, after pydantic has coerced
            all fields to their declared types.

        Returns
        -------
        LedgerEvent
            The same instance, unchanged if validation passes.

        Raises
        ------
        ValueError
            If event-type invariants are violated.
        """
        trading_events = ("BUY", "SELL")
        has_shares = self.shares is not None
        has_price = self.price is not None

        if self.event_type in trading_events:
            if not (has_shares and has_price):
                message = f"{self.event_type} event must have both shares and price."
                raise ValueError(message)
        elif has_shares or has_price:
            message = f"{self.event_type} event must not have shares or price."
            raise ValueError(message)

        return self


class PriceObservation(BaseModel):
    """One validated (symbol, day, close) triple from the price API."""

    symbol: str = Field(min_length=1)
    price_date: date
    close: float = Field(gt=0)

    polars_schema: ClassVar[dict[str, type[pl.DataType] | pl.DataType]] = {
        "price_date": pl.Date,
        "close": pl.Float64,
    }


class CpiObservation(BaseModel):
    """One (month, index value) pair from the FRED CPI series."""

    observation_date: date
    value: float = Field(gt=0)

    polars_schema: ClassVar[dict[str, type[pl.DataType] | pl.DataType]] = {
        "observation_date": pl.Date,
        "value": pl.Float64,
    }


class HysaRateObservation(BaseModel):
    """One (bank, rate-change date, APY) triple from apyarchives.com.

    A rate only gets a new row on the date it changed — not one row per
    day — so looking up "the rate on day X" means rolling back to the most
    recent row on or before X, the same as `prices.price_as_of`.
    """

    bank_id: str = Field(min_length=1)
    bank_name: str = Field(min_length=1)
    rate_date: date
    apy_pct: float = Field(ge=0)

    polars_schema: ClassVar[dict[str, type[pl.DataType] | pl.DataType]] = {
        "bank_id": pl.Utf8,
        "bank_name": pl.Utf8,
        "rate_date": pl.Date,
        "apy_pct": pl.Float64,
    }
