"""Raw row shapes for SoFi's CSV exports.

Two distinct shapes exist: `SofiRow` (`Date, Description, Type, Amount,
Current balance, Status`) is the older checking/savings export;
`SofiCsvRow` (`Authorized Date, Posted Date, Status, Account Name,
Description, Primary Category, Detailed Category, Amount`) is the newer
one, which also covers vaults — something the old format has no export
for at all (SoFi only ever offered vault history via the monthly
statement PDF, whose importer has since been retired and deleted).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from db.money import Money


class SofiRow(BaseModel):
    """One row of a SoFi checking or savings export, in the older CSV shape."""

    model_config = ConfigDict(populate_by_name=True)

    transaction_date: date = Field(alias="Date")
    description: str = Field(alias="Description")
    type: str = Field(alias="Type")
    amount: Money = Field(alias="Amount")
    current_balance: str = Field(alias="Current balance", default="")
    status: str = Field(alias="Status", default="")


class SofiCsvRow(BaseModel):
    """One row of a SoFi checking, savings, or vault export, in the newer CSV shape.

    `account_name` is the one field that identifies which account this
    row belongs to — e.g. `"Emergency Fund ***3680"` for a vault named
    "Emergency Fund" whose parent savings account ends in 3680, or (per
    SoFi's own terminology) `"SoFi HYSA ***3680"`/`"Checking ***9169"` for
    the savings/checking account itself. `detect.py` and this format's
    standardizer both parse it the same way (see `parse_account_name`).
    """

    model_config = ConfigDict(populate_by_name=True)

    authorized_date: date = Field(alias="Authorized Date")
    posted_date: date = Field(alias="Posted Date")
    status: str = Field(alias="Status", default="")
    account_name: str = Field(alias="Account Name")
    description: str = Field(alias="Description")
    primary_category: str = Field(alias="Primary Category", default="")
    detailed_category: str = Field(alias="Detailed Category", default="")
    amount: Money = Field(alias="Amount")
