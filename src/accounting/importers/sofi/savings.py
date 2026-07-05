"""Map SoFi's savings CSV export onto canonical postings."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import TYPE_CHECKING

from accounting.importers.common import RawLeg, posting_pair, postings_to_frame, row_hash
from accounting.importers.sofi.csv_v2 import is_sofi_csv_v2, standardize_sofi_csv_v2
from accounting.importers.sofi.models import SofiRow
from accounting.store import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID

if TYPE_CHECKING:
    import polars as pl

    from accounting.models import Posting


def standardize_sofi_savings(csv_text: str, account_id: str) -> pl.DataFrame:
    """Map SoFi savings export rows onto postings against `account_id`.

    Vault transfers ("To Travel Vault", "From House Vault") aren't
    recognized here — they still get a generic placeholder counterparty
    like every other row. Recognizing the vault name and repointing the
    counterparty at a real `kind="vault"` sub-account is
    `ledger.categorization`'s job (Phase 2), since it's a categorization
    decision, not a fact about the file format.

    Parameters
    ----------
    csv_text
        The raw CSV file contents, exactly as uploaded.
    account_id
        The real SoFi savings account these rows belong to.

    Returns
    -------
    polars.DataFrame
        Posting-shaped rows, two per input row, validated through `Posting`.
    """
    if is_sofi_csv_v2(csv_text):
        return standardize_sofi_csv_v2(csv_text, account_id)
    postings: list[Posting] = []
    for raw in csv.DictReader(io.StringIO(csv_text)):
        row = SofiRow.model_validate(raw)
        posted_at = datetime.combine(row.transaction_date, datetime.min.time())
        counterparty = UNCATEGORIZED_INCOME_ACCOUNT_ID if row.amount >= 0 else UNCATEGORIZED_EXPENSE_ACCOUNT_ID
        transaction_row_id = row_hash(account_id, row.transaction_date.isoformat(), str(row.amount), row.description)
        leg = RawLeg(
            posted_at=posted_at,
            amount=row.amount,
            currency="USD",
            description=row.description,
            meta={"source_type": row.type, "row_hash": transaction_row_id},
        )
        postings.extend(
            posting_pair(
                source="sofi-savings",
                row_id=transaction_row_id,
                account_id=account_id,
                counterparty_account_id=counterparty,
                leg=leg,
            )
        )
    return postings_to_frame(postings)
