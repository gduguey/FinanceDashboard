"""Raw row shapes for Chase's two CSV export formats, exactly as Chase writes their column headers."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from db.money import Money


class ChaseCheckingRow(BaseModel):
    """One row of a Chase checking/deposit-account export."""

    model_config = ConfigDict(populate_by_name=True)

    details: str = Field(alias="Details")
    posting_date: str = Field(alias="Posting Date")
    description: str = Field(alias="Description")
    amount: Money = Field(alias="Amount")
    type: str = Field(alias="Type")
    balance: str = Field(alias="Balance", default="")
    check_or_slip: str = Field(alias="Check or Slip #", default="")


class ChaseCreditCardRow(BaseModel):
    """One row of a Chase credit-card export."""

    model_config = ConfigDict(populate_by_name=True)

    transaction_date: str = Field(alias="Transaction Date")
    post_date: str = Field(alias="Post Date")
    description: str = Field(alias="Description")
    category: str = Field(alias="Category", default="")
    type: str = Field(alias="Type")
    amount: Money = Field(alias="Amount")
    memo: str = Field(alias="Memo", default="")
