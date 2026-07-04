"""Pydantic schemas for IBKR's Flex Query XML rows."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def parse_ibkr_datetime(value: object) -> object:
    """Convert an IBKR timestamp string to naive UTC.

    IBKR appends a timezone abbreviation to every timestamp — always
    "EDT" or "EST", the daylight/standard names for its US Eastern
    reporting zone. That abbreviation is read directly to get the exact
    UTC offset, rather than assumed from a configured zone: the report
    already says what it means, so there's nothing to configure.

    Parameters
    ----------
    value
        The raw field value from the XML attribute, before pydantic parsing.

    Returns
    -------
    object
        `value` converted to a naive UTC `datetime`, if it was a string.
        Passed through unchanged otherwise (e.g. an already-parsed `datetime`).

    Raises
    ------
    ValueError
        If a string value's trailing abbreviation isn't "EDT" or "EST".
    """
    if not isinstance(value, str):
        return value
    local_text, _, abbreviation = value.rpartition(" ")
    utc_offset_hours = {"EDT": 4, "EST": 5}.get(abbreviation)
    if utc_offset_hours is None:
        message = f"Unrecognized IBKR timezone abbreviation {abbreviation!r} in {value!r} (expected EDT or EST)."
        raise ValueError(message)
    naive_local = datetime.strptime(local_text, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007
    return naive_local + timedelta(hours=utc_offset_hours)


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
    date_time: datetime = Field(alias="dateTime")
    quantity: float = Field(alias="quantity")
    trade_price: float = Field(alias="tradePrice", ge=0)
    trade_money: float = Field(alias="tradeMoney")
    ib_commission: float = Field(alias="ibCommission")
    net_cash: float = Field(alias="netCash")
    notes: str = Field(alias="notes", default="")

    _parse_date_time = field_validator("date_time", mode="before")(parse_ibkr_datetime)


class IbkrCashTransaction(BaseModel):
    """One `<CashTransaction>` row from an IBKR Flex Query report.

    Only present if the Flex Query's "Cash Transactions" section is
    enabled. IBKR reports every real transaction twice —
    `level_of_detail="DETAIL"` (real ID) and a same-day "SUMMARY" rollup
    (blank ID) — so `transaction_id`/`symbol` skip `IbkrTrade`-style
    `min_length` checks; `preprocessing.standardize_ibkr_cash_transactions`
    drops the SUMMARY rows before anything becomes a ledger event.
    """

    model_config = ConfigDict(populate_by_name=True)

    account_id: str = Field(alias="accountId")
    transaction_id: str = Field(alias="transactionID", default="")
    symbol: str = Field(alias="symbol", default="")
    currency: str = Field(alias="currency")
    date_time: datetime = Field(alias="dateTime")
    amount: float = Field(alias="amount")
    type: str = Field(alias="type", min_length=1)
    description: str = Field(alias="description", default="")
    action_id: str = Field(alias="actionID", default="")
    level_of_detail: Literal["DETAIL", "SUMMARY"] = Field(alias="levelOfDetail")

    _parse_date_time = field_validator("date_time", mode="before")(parse_ibkr_datetime)
