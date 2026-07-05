"""Map Chase's credit-card CSV export onto canonical postings."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import TYPE_CHECKING

from accounting.importers.chase.models import ChaseCreditCardRow
from accounting.importers.common import RawLeg, parse_us_date, posting_pair, postings_to_frame, row_hash
from accounting.store import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID

if TYPE_CHECKING:
    import polars as pl

    from accounting.models import Posting


def standardize_chase_credit_card(csv_text: str, account_id: str) -> pl.DataFrame:
    """Map Chase credit-card export rows onto postings against `account_id`.

    Uses `Post Date` over `Transaction Date` when both are present, the
    same preference the account's own statement balance is based on.
    Chase's own `Category` column is kept in `meta` for a future rule to
    read, never acted on here. Rows of `Type` `"Payment"` ("Payment Thank
    You-Mobile") are dropped entirely — Chase's credit-card export books
    a payment on both sides of the transfer: once here, implicitly, and
    once explicitly as an outgoing "Payment to Chase card ending in ..."
    row on the paying checking account. Keeping both would double-count
    every payment; the checking side is the one a rule can actually match
    (it names which card), so it's the side kept.

    Parameters
    ----------
    csv_text
        The raw CSV file contents, exactly as uploaded.
    account_id
        The real Chase credit-card account these rows belong to.

    Returns
    -------
    polars.DataFrame
        Posting-shaped rows, two per kept input row, validated through `Posting`.
    """
    postings: list[Posting] = []
    for raw in csv.DictReader(io.StringIO(csv_text)):
        row = ChaseCreditCardRow.model_validate(raw)
        if row.type == "Payment":
            continue
        date_text = row.post_date.strip() or row.transaction_date.strip()
        posted_at = datetime.combine(parse_us_date(date_text), datetime.min.time())
        counterparty = UNCATEGORIZED_INCOME_ACCOUNT_ID if row.amount >= 0 else UNCATEGORIZED_EXPENSE_ACCOUNT_ID
        transaction_row_id = row_hash(account_id, date_text, str(row.amount), row.description)
        leg = RawLeg(
            posted_at=posted_at,
            amount=row.amount,
            currency="USD",
            description=row.description,
            meta={"source_type": row.type, "source_category": row.category, "row_hash": transaction_row_id},
        )
        postings.extend(
            posting_pair(
                source="chase-credit-card",
                row_id=transaction_row_id,
                account_id=account_id,
                counterparty_account_id=counterparty,
                leg=leg,
            )
        )
    return postings_to_frame(postings)
