"""Map Chase's checking/deposit-account CSV export onto canonical postings."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import TYPE_CHECKING

from accounting.importers.chase.models import ChaseCheckingRow
from accounting.importers.common import RawLeg, parse_us_date, posting_pair, postings_to_frame, row_hash
from accounting.taxonomy import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID

if TYPE_CHECKING:
    import polars as pl

    from accounting.models import Posting


def standardize_chase_checking(csv_text: str, account_id: str) -> pl.DataFrame:
    """Map Chase checking export rows onto postings against `account_id`.

    Every row's counterparty is one of the two uncategorized placeholders,
    chosen by sign — no rule matching, no transfer detection here.

    Parameters
    ----------
    csv_text
        The raw CSV file contents, exactly as uploaded.
    account_id
        The real Chase checking account these rows belong to.

    Returns
    -------
    polars.DataFrame
        Posting-shaped rows, two per input row, validated through `Posting`.
    """
    postings: list[Posting] = []
    for raw in csv.DictReader(io.StringIO(csv_text)):
        row = ChaseCheckingRow.model_validate(raw)
        posted_at = datetime.combine(parse_us_date(row.posting_date), datetime.min.time())
        counterparty = UNCATEGORIZED_INCOME_ACCOUNT_ID if row.amount >= 0 else UNCATEGORIZED_EXPENSE_ACCOUNT_ID
        transaction_row_id = row_hash(account_id, row.posting_date, str(row.amount), row.description)
        leg = RawLeg(
            posted_at=posted_at,
            amount=row.amount,
            currency="USD",
            description=row.description,
            meta={"source_type": row.type, "source_details": row.details, "row_hash": transaction_row_id},
        )
        postings.extend(
            posting_pair(
                source="chase-checking",
                row_id=transaction_row_id,
                account_id=account_id,
                counterparty_account_id=counterparty,
                leg=leg,
            )
        )
    return postings_to_frame(postings)
