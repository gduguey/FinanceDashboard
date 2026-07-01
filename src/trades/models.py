"""Pydantic schemas validated at the external boundaries of this codebase:
raw trade rows read from a broker CSV export, price rows pulled from the
Yahoo Finance API, and trade/position/cash rows pulled from a broker's own
API (currently IBKR's Flex Web Service). Everything downstream operates on
plain, already-trusted pandas DataFrames.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

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


class IbkrTrade(BaseModel):
    """One `<Trade>` row from an IBKR Flex Query report.

    Field aliases match the XML attribute names exactly, so a `<Trade>`
    element's `.attrib` dict validates directly with no manual field mapping
    — extra attributes the report includes but this model doesn't need
    (there are ~80 possible) are ignored, not rejected.
    """

    model_config = ConfigDict(populate_by_name=True)

    account_id: str = Field(alias="accountId")
    transaction_id: str = Field(alias="transactionID", min_length=1)
    trade_id: str = Field(alias="tradeID", min_length=1)
    symbol: str = Field(alias="symbol", min_length=1)
    asset_category: str = Field(alias="assetCategory")
    currency: str = Field(alias="currency")
    buy_sell: Literal["BUY", "SELL", "BUY (Ca.)", "SELL (Ca.)"] = Field(alias="buySell")
    trade_date: date = Field(alias="tradeDate")
    quantity: float = Field(alias="quantity")
    trade_price: float = Field(alias="tradePrice", ge=0)
    trade_money: float = Field(alias="tradeMoney")
    ib_commission: float = Field(alias="ibCommission")
    net_cash: float = Field(alias="netCash")


class IbkrPosition(BaseModel):
    """One `<OpenPosition>` row from an IBKR Flex Query report."""

    model_config = ConfigDict(populate_by_name=True)

    account_id: str = Field(alias="accountId")
    symbol: str = Field(alias="symbol", min_length=1)
    asset_category: str = Field(alias="assetCategory")
    currency: str = Field(alias="currency")
    report_date: date = Field(alias="reportDate")
    quantity: float = Field(alias="position")
    mark_price: float = Field(alias="markPrice", ge=0)
    position_value: float = Field(alias="positionValue")


class IbkrCashBalance(BaseModel):
    """One `<CashReportCurrency>` row from an IBKR Flex Query report.

    With the query's "Base Currency Summary" option, `currency` comes back
    as the literal string `"BASE_SUMMARY"` rather than a real currency code.
    """

    model_config = ConfigDict(populate_by_name=True)

    account_id: str = Field(alias="accountId")
    currency: str = Field(alias="currency")
    from_date: date = Field(alias="fromDate")
    to_date: date = Field(alias="toDate")
    ending_cash: float = Field(alias="endingCash")
    ending_settled_cash: float = Field(alias="endingSettledCash")
