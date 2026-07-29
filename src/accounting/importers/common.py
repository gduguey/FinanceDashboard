"""Shared helpers every bank-specific standardizer uses: date parsing, dedup hashing, and posting-pair construction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

import polars as pl

from accounting.ledger.frame import LEDGER_FRAME_SCHEMA
from accounting.models import Posting

if TYPE_CHECKING:
    from collections.abc import Sequence

    from accounting.models import CurrencyCode
    from db.money import Money


def parse_us_date(text: str) -> date:
    """Parse a `MM/DD/YYYY` date string, Chase's format in every export.

    Split by hand rather than `datetime.strptime`, which would construct
    an intermediate naive `datetime` this app never wants — every date
    here has no time component at all, not just an unspecified one.

    Parameters
    ----------
    text
        A date string like `"06/30/2026"`.

    Returns
    -------
    datetime.date
        The parsed date.
    """
    month, day, year = text.strip().split("/")
    return date(int(year), int(month), int(day))


@dataclass(frozen=True)
class RawLeg:
    """The per-row facts `posting_pair` needs beyond which two accounts are involved."""

    posted_at: datetime
    amount: Money
    currency: CurrencyCode
    description: str
    meta: dict[str, str]


def row_hash(*parts: str) -> str:
    """Build a stable content hash for deduplicating an imported row against what's already ingested.

    Every caller must format a monetary part as `f"{amount:.4f}"`
    (`db.money.MONEY_SCALE`) rather than `str(amount)`. Amounts are `Money`
    (`Decimal`), and `str(Decimal)` preserves whatever scale the source file
    wrote — so `1500.0`, `1500.00` and `1500` would hash to three different
    ids for one transaction, and a bank changing its export format would
    quietly stop every re-import from deduping. The rule cannot be enforced
    here because this takes opaque `*parts: str`; it is normalized at each
    call site instead.

    Parameters
    ----------
    parts
        Whatever identifies a row uniquely for one bank/account —
        typically (account_id, date, amount, description), with any amount
        already formatted at `MONEY_SCALE`.

    Returns
    -------
    str
        A short, stable hex digest.
    """
    return hashlib.sha256("\x1e".join(parts).encode()).hexdigest()[:16]


def posting_pair(
    *,
    source: str,
    row_id: str,
    account_id: str,
    counterparty_account_id: str,
    leg: RawLeg,
    category_id: str | None = None,
) -> list[Posting]:
    """Build the two postings — real account plus placeholder counterparty — for one imported row.

    The counterparty leg carries none of `leg.meta` (the dedup hash and
    source-format facts belong to the row that was actually parsed, not
    its balancing placeholder). `category_id`, when given, is set on the
    real leg only — for a row whose category is a structural fact of the
    source format itself (e.g. a statement's own "Interest Earned" rows)
    rather than something `ledger.categorization` has to infer from text.

    Parameters
    ----------
    source
        A short tag identifying which importer produced this row, e.g. `"chase-checking"`.
    row_id
        An id unique within this source, used to build both `transaction_id` and `posting_id`.
    account_id
        The real account this row happened on.
    counterparty_account_id
        The placeholder (or, later, resolved) counterparty account.
    leg
        The row's date, signed amount, currency, description, and provenance meta.
    category_id
        A category to set on the real leg immediately, bypassing rule matching.

    Returns
    -------
    list[Posting]
        Exactly two postings, summing to zero.
    """
    transaction_id = f"{source}:{row_id}"
    return [
        Posting(
            posting_id=f"{transaction_id}:0",
            transaction_id=transaction_id,
            account_id=account_id,
            posted_at=leg.posted_at,
            amount=leg.amount,
            currency=leg.currency,
            description=leg.description,
            meta=leg.meta,
            category_id=category_id,
        ),
        Posting(
            posting_id=f"{transaction_id}:1",
            transaction_id=transaction_id,
            account_id=counterparty_account_id,
            posted_at=leg.posted_at,
            amount=-leg.amount,
            currency=leg.currency,
            description=leg.description,
        ),
    ]


def postings_to_frame(postings: Sequence[Posting]) -> pl.DataFrame:
    """Turn validated `Posting` models into the flat frame shape every other accounting module reads.

    Parameters
    ----------
    postings
        Already-validated postings.

    Returns
    -------
    polars.DataFrame
        Columns matching `LEDGER_FRAME_SCHEMA`, sorted by date then id.
    """
    if not postings:
        return pl.DataFrame(schema=LEDGER_FRAME_SCHEMA)
    frame = pl.DataFrame([posting.model_dump() for posting in postings], schema=LEDGER_FRAME_SCHEMA)
    return frame.sort("posted_at", "posting_id")
