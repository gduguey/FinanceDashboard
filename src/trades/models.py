"""Pydantic schemas validated at the two external boundaries of this codebase:
raw trade rows read from the broker CSV, and price rows pulled from the
Yahoo Finance API. Everything downstream operates on plain, already-trusted
pandas DataFrames.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RawTrade(BaseModel):
    """One row of the broker's export, before any derived columns."""

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


class PriceObservation(BaseModel):
    """One validated (symbol, day, close) triple from the price API."""

    symbol: str = Field(min_length=1)
    price_date: date
    close: float = Field(gt=0)
