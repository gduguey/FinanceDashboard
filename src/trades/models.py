"""Pydantic schemas validated at the external boundaries of this codebase."""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, field_validator

from trades.config import LedgerEventType


class RawTrade(BaseModel):
    """The canonical "invested schedule" trade row.

    A broker CSV export row, or a `BUY` event pulled off the ledger. Field
    names double as the column names everywhere this shape is used.
    """

    model_config = ConfigDict(populate_by_name=True)

    trade_date: date = Field(alias="Date")
    symbol: str = Field(alias="Symbol", min_length=1)
    shares: float = Field(alias="Shares", gt=0)
    usd_spent: float = Field(alias="USD Spent", gt=0)

    polars_schema: ClassVar[dict[str, type[pl.DataType] | pl.DataType]] = {
        "trade_date": pl.Date,
        "symbol": pl.Utf8,
        "shares": pl.Float64,
        "usd_spent": pl.Float64,
    }

    @field_validator("usd_spent", mode="before")
    @classmethod
    def parse_currency(cls, value: object) -> object:
        """Strip a "$1,234.56"-formatted string down to a plain number.

        Parameters
        ----------
        value
            The raw field value, before pydantic's type coercion.

        Returns
        -------
        object
            The value with currency formatting removed, unchanged if it
            was not a string.
        """
        if isinstance(value, str):
            return value.replace("$", "").replace(",", "").strip()
        return value

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
