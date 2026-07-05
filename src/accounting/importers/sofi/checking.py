"""Map SoFi's checking CSV export onto canonical postings."""

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


def standardize_sofi_checking(csv_text: str, account_id: str) -> pl.DataFrame:
    """Map SoFi checking export rows onto postings against `account_id`.

    Dispatches to `standardize_sofi_csv_v2` when `csv_text` is in SoFi's
    newer export shape — both are registered under the same
    `("SoFi", "checking")` importer key, since the file itself (not the
    account kind) determines which shape a given export is in.

    Parameters
    ----------
    csv_text
        The raw CSV file contents, exactly as uploaded.
    account_id
        The real SoFi checking account these rows belong to.

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
                source="sofi-checking",
                row_id=transaction_row_id,
                account_id=account_id,
                counterparty_account_id=counterparty,
                leg=leg,
            )
        )
    return postings_to_frame(postings)
