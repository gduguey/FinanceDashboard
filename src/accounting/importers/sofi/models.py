"""Raw row shape for SoFi's CSV export — the same shape for both checking and savings accounts."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class SofiRow(BaseModel):
    """One row of a SoFi checking or savings export."""

    model_config = ConfigDict(populate_by_name=True)

    transaction_date: date = Field(alias="Date")
    description: str = Field(alias="Description")
    type: str = Field(alias="Type")
    amount: float = Field(alias="Amount")
    current_balance: str = Field(alias="Current balance", default="")
    status: str = Field(alias="Status", default="")
