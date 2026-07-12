"""Map SoFi's newer CSV export (checking, savings, or a vault) onto canonical postings.

Unlike the older `SofiRow` shape (see `importers.sofi.savings`/`checking`),
this one covers vaults too — SoFi previously only ever exposed vault
history via the monthly statement PDF (`importers.sofi.statement_pdf`);
this format lets a vault be dragged straight in as a CSV instead.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from accounting.importers.common import RawLeg, posting_pair, postings_to_frame, row_hash
from accounting.importers.sofi.models import SofiCsvV2Row
from accounting.store import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID

if TYPE_CHECKING:
    import polars as pl

    from accounting.models import Posting

HEADER = (
    "Authorized Date",
    "Posted Date",
    "Status",
    "Account Name",
    "Description",
    "Primary Category",
    "Detailed Category",
    "Amount",
)

_INTEREST_EARNED_CATEGORY_ID = "income:interest-earned"
_ACCOUNT_NAME = re.compile(r"^(?P<label>.+?)\s*\*{2,3}(?P<last4>\d{4})$")
_CHECKING_KEYWORDS = ("checking",)
_SAVINGS_KEYWORDS = ("saving", "hysa")
_SAVINGS_TRANSFER_DESCRIPTION = re.compile(r"transfer\s+(?:to|from)\s+savings", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedAccountName:
    """What one row's `Account Name` field says about which account it belongs to.

    `kind` is `None` when `label` names neither "checking" nor "savings"/
    "hysa" — meaning `label` is itself a vault's own name (e.g. "Emergency
    Fund"), not a description of the parent account.
    """

    label: str
    last4: str
    kind: Literal["checking", "savings"] | None


def is_sofi_csv_v2(csv_text: str) -> bool:
    """Check whether `csv_text` is in this newer shape, by its header line.

    Returns
    -------
    bool
    """
    first_line = csv_text.splitlines()[0] if csv_text else ""
    return tuple(column.strip() for column in first_line.split(",")) == HEADER


def parse_account_name(account_name: str) -> ParsedAccountName | None:
    """Parse one row's `Account Name` field into a label, last-4 digits, and a checking/savings guess.

    Parameters
    ----------
    account_name
        e.g. `"Emergency Fund ***3680"`, `"SoFi HYSA ***3680"`, `"Checking ***9169"`.

    Returns
    -------
    ParsedAccountName or None
        `None` if `account_name` doesn't end in `***XXXX`-style digits at all.
    """
    match = _ACCOUNT_NAME.match(account_name.strip())
    if match is None:
        return None
    label = match["label"].strip()
    lowered = label.lower()
    kind: Literal["checking", "savings"] | None = None
    if any(keyword in lowered for keyword in _CHECKING_KEYWORDS):
        kind = "checking"
    elif any(keyword in lowered for keyword in _SAVINGS_KEYWORDS):
        kind = "savings"
    return ParsedAccountName(label=label, last4=match["last4"], kind=kind)


def standardize_sofi_csv_v2(csv_text: str, account_id: str) -> pl.DataFrame:
    """Map this newer SoFi export's rows onto postings against `account_id`.

    Two structural facts, read directly from the file rather than left
    for `ledger.categorization` to infer from free text: an "Interest"
    row is tagged `income:interest-earned` immediately (mirroring the PDF
    importer's own handling of the same fact), and a "Transfer To/From
    Savings" row on a *vault's* own export is pointed straight at that
    vault's parent savings account — derivable from `account_id` itself
    (`{parent}:vault:{name}`), never a guess from description text.

    Parameters
    ----------
    csv_text
        The raw CSV file contents, exactly as uploaded.
    account_id
        The real account (checking, savings, or vault) these rows belong to.

    Returns
    -------
    polars.DataFrame
        Posting-shaped rows, two per input row, validated through `Posting`.
    """
    parent_account_id = account_id.split(":vault:", maxsplit=1)[0] if ":vault:" in account_id else None
    postings: list[Posting] = []
    for raw in csv.DictReader(io.StringIO(csv_text)):
        row = SofiCsvV2Row.model_validate(raw)
        posted_at = datetime.combine(row.posted_date, datetime.min.time())
        is_interest = row.detailed_category.strip().lower() == "interest"
        is_savings_transfer = bool(_SAVINGS_TRANSFER_DESCRIPTION.search(row.description))

        if is_savings_transfer and parent_account_id is not None:
            counterparty = parent_account_id
        elif row.amount >= 0:
            counterparty = UNCATEGORIZED_INCOME_ACCOUNT_ID
        else:
            counterparty = UNCATEGORIZED_EXPENSE_ACCOUNT_ID

        transaction_row_id = row_hash(account_id, row.posted_date.isoformat(), str(row.amount), row.description)
        leg = RawLeg(
            posted_at=posted_at,
            amount=row.amount,
            currency="USD",
            description=row.description,
            meta={
                "primary_category": row.primary_category,
                "detailed_category": row.detailed_category,
                "row_hash": transaction_row_id,
            },
        )
        postings.extend(
            posting_pair(
                source="sofi-csv-v2",
                row_id=transaction_row_id,
                account_id=account_id,
                counterparty_account_id=counterparty,
                leg=leg,
                category_id=_INTEREST_EARNED_CATEGORY_ID if is_interest else None,
            )
        )
    return postings_to_frame(postings)


__all__ = ["ParsedAccountName", "is_sofi_csv_v2", "parse_account_name", "standardize_sofi_csv_v2"]
