"""The ledger analytics projection — and the one place exact money becomes a float.

## This module is the T1 boundary. Read this before changing it.

Money is exact `Decimal` everywhere it is stored and modelled: the
`NUMERIC(18, 4)` columns in `accounting.db`, and the `Money`-typed fields on
`accounting.models`. See `db.money` for the scale and rounding policy.

Every *aggregation* over the ledger — balances, net worth, the income
statement, budgets, goals, and every chart behind them — is computed with
Polars over a columnar frame, and that frame's `amount` column is
`Float64`. Polars' own `Decimal` dtype is still marked unstable and its
aggregation semantics differ from the Python `decimal` module's, so
adopting it here would trade a known, bounded imprecision for an unknown
one.

So this is a deliberate, single, named boundary rather than a leak:

- **Exact on the way in.** A value is quantized to the storage scale before
  it is written (`db.money.quantize_money`), so what Postgres holds is
  exactly what the domain layer decided.
- **Float only inside the projection.** `db.money.to_analytics_float` is
  the only sanctioned `Decimal -> float` conversion, re-exported here as
  `to_analytics_amount`. Every frame is built through
  `LEDGER_FRAME_SCHEMA`, so no other shape can appear.
- **Exact again on the way out.** Anything read out of a frame and then
  persisted, compared for equality, or returned as an authoritative balance
  goes back through `db.money.to_decimal`/`quantize_money`.

The residual imprecision is therefore confined to aggregation, bounded by
`NUMERIC(18, 4)` inputs, and re-quantized before it can be stored. That is
"half of T1": the persistence and domain layers are exact today, the
analytics layer is not yet.

TODO(PR4/PR5): the remaining half. Two follow-ups are gated on this
boundary existing, and both should start here rather than at a call site:
the money-safety work in the frontend (which consumes what these
aggregations produce), and the property-based money tests that would pin
down the aggregation error this boundary currently tolerates. The
`_AMOUNT_TOLERANCE`/`_ZERO_SUM_TOLERANCE` constants that still exist in
`ledger.transfers`, `ledger.duplicates`, and `ledger.replay` are float
slack *for this projection specifically*, and they retire when it does —
they are no longer masking imprecision anywhere money is stored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from db.money import to_analytics_float as to_analytics_amount

if TYPE_CHECKING:
    from collections.abc import Sequence

    from accounting.models import Posting

LEDGER_FRAME_SCHEMA: dict[str, type[pl.DataType] | pl.DataType] = {
    "posting_id": pl.Utf8,
    "transaction_id": pl.Utf8,
    "account_id": pl.Utf8,
    "posted_at": pl.Datetime("us"),
    "amount": pl.Float64,
    "currency": pl.Utf8,
    "category_id": pl.Utf8,
    "subcategory_id": pl.Utf8,
    "budget_id": pl.Utf8,
    "tag_ids": pl.List(pl.Utf8),
    "description": pl.Utf8,
    "meta": pl.Object,
}
"""The shape every ledger and dashboard module reads.

Previously `Posting.polars_schema`. It was moved off the model deliberately:
the domain model is exact and this projection is not, so deriving one from
the other invited exactly the confusion this module's docstring exists to
prevent. `amount` is `Float64` here and `Money` (`Decimal`) on `Posting`;
those are two different types on purpose.

This frame is flat, and deliberately wider than the storage under it.
`posted_at` and `description` are stored once per *transaction* (see
`accounting.db.core.Transaction`) and appear here once per *leg*:
`importers.ingest.load_ledger` joins them back on. That keeps the boundary
this module documents at one place — every ledger, dashboard, and API module
reads one flat row shape, and none of them has to know, or re-derive, which
of these columns is a fact about the event rather than about the leg.
"""


def empty_ledger_frame() -> pl.DataFrame:
    """Build the empty frame, correctly typed.

    Returns
    -------
    polars.DataFrame
        Zero rows, `LEDGER_FRAME_SCHEMA` columns.
    """
    return pl.DataFrame(schema=LEDGER_FRAME_SCHEMA)


def postings_to_ledger_frame(postings: Sequence[Posting]) -> pl.DataFrame:
    """Project validated `Posting` models into the analytics frame.

    Crosses the boundary this module documents: `Posting.amount` is an
    exact `Decimal`, and the frame's `amount` column is `Float64`.

    Parameters
    ----------
    postings
        Already-validated postings.

    Returns
    -------
    polars.DataFrame
        `LEDGER_FRAME_SCHEMA`-shaped, sorted by date then posting id.
    """
    if not postings:
        return empty_ledger_frame()
    rows = []
    for posting in postings:
        row = posting.model_dump()
        row["amount"] = to_analytics_amount(posting.amount)
        rows.append(row)
    return pl.DataFrame(rows, schema=LEDGER_FRAME_SCHEMA).sort("posted_at", "posting_id")
