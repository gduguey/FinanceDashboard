"""The SQL that produces `ledger.frame.LEDGER_FRAME_SCHEMA`, and the projection of its rows into that frame.

Every read path in `accounting` starts here. `importers.ingest.load_ledger`
is the thin wrapper callers actually use; this module is the statement it
runs and the frame builder it hands the rows to.

Two things are deliberate about the shape of the statement below.

**It resolves natural keys in SQL, not in Python.** The frame is keyed by
natural key throughout (`LEDGER_FRAME_SCHEMA`), while every stored foreign
key holds an opaque `id` — so something has to translate. Doing it with
joins rather than with follow-up `db.base.natural_keys_by_id` calls removes
four round trips *and* four `IN (...)` lists from the hottest read in the
app; the latter is what used to put a bind parameter per row in front of
Postgres's 65,535-parameter ceiling.

**It selects columns, not entities.** `session.execute(select(cols))`
returns rows; `session.query(Posting, Transaction)` returns ORM instances,
with identity-map bookkeeping and per-attribute descriptors for state
nothing here mutates. On a 200k-posting ledger that difference measured
**~12.3 s versus ~3.3 s** for the identical join — the single largest cost
in the read path before this module existed. Nothing downstream wants an
entity: `ledger_rows_to_frame` reads each row once and never again.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING, Any

import polars as pl
from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import aliased

import accounting.db as adb
from accounting.ledger.frame import LEDGER_FRAME_SCHEMA
from db.money import to_analytics_float as to_analytics_amount

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence
    from datetime import date

    from sqlalchemy import Row

    from accounting.models import TransactionOrigin


def ledger_statement(
    user_id: uuid.UUID,
    *,
    since: date | None = None,
    until: date | None = None,
    origin: TransactionOrigin | None = None,
) -> Select[Any]:
    """Build the `SELECT` whose rows are `LEDGER_FRAME_SCHEMA`, one per posting.

    See `importers.ingest.load_ledger` for what each filter means to a
    caller; this function only turns them into predicates.

    `transactions` is always joined, not just when `origin` is given:
    `posted_at` and `description` are facts about the event (see
    `accounting.db.core.Transaction`) and the frame carries one of each per
    *leg*, so this join is what denormalizes them back onto every posting
    of one transaction. The date bounds filter `transactions`, where the
    index lives, and stay naive to match that naive column — psycopg
    rejects an aware/naive comparison outright.

    Every join carries its own `user_id` predicate. Row-Level Security
    already guarantees it, so these are not what makes the query safe;
    they are what lets the planner use the `(user_id, ...)` composite
    indexes rather than filtering after the fact.

    Parameters
    ----------
    user_id
        Whose ledger to select.
    since, until, origin
        See `importers.ingest.load_ledger`.

    Returns
    -------
    sqlalchemy.Select
        Columns in `LEDGER_FRAME_SCHEMA` order, unordered rows. `tag_ids`
        is `NULL` rather than an empty array for an untagged posting — see
        `ledger_rows_to_frame`.
    """
    category = aliased(adb.Category)
    subcategory = aliased(adb.Category)
    # One correlated aggregate rather than a second unbounded pass over
    # `posting_tags`: the previous shape scanned every tag row this user
    # owns on every read, then resolved the distinct tag ids it found in a
    # third query.
    tag_keys = (
        select(func.array_agg(aggregate_order_by(adb.Tag.natural_key, adb.Tag.natural_key)))
        .select_from(adb.PostingTag)
        .join(adb.Tag, (adb.PostingTag.tag_id == adb.Tag.id) & (adb.Tag.user_id == adb.PostingTag.user_id))
        .where(adb.PostingTag.posting_id == adb.Posting.id, adb.PostingTag.user_id == adb.Posting.user_id)
        .correlate(adb.Posting)
        .scalar_subquery()
    )
    statement = (
        select(
            adb.Posting.natural_key.label("posting_id"),
            adb.Transaction.natural_key.label("transaction_id"),
            adb.Account.natural_key.label("account_id"),
            adb.Transaction.posted_at.label("posted_at"),
            adb.Posting.amount.label("amount"),
            adb.Posting.currency.label("currency"),
            category.natural_key.label("category_id"),
            subcategory.natural_key.label("subcategory_id"),
            adb.Budget.natural_key.label("budget_id"),
            tag_keys.label("tag_ids"),
            adb.Transaction.description.label("description"),
            adb.Posting.meta.label("meta"),
        )
        .select_from(adb.Posting)
        .join(
            adb.Transaction,
            (adb.Posting.transaction_id == adb.Transaction.id) & (adb.Transaction.user_id == adb.Posting.user_id),
        )
        .join(
            adb.Account,
            (adb.Posting.account_id == adb.Account.id) & (adb.Account.user_id == adb.Posting.user_id),
        )
        .outerjoin(
            category,
            (adb.Posting.category_id == category.id) & (category.user_id == adb.Posting.user_id),
        )
        .outerjoin(
            subcategory,
            (adb.Posting.subcategory_id == subcategory.id) & (subcategory.user_id == adb.Posting.user_id),
        )
        .outerjoin(
            adb.Budget,
            (adb.Posting.budget_id == adb.Budget.id) & (adb.Budget.user_id == adb.Posting.user_id),
        )
        .where(adb.Posting.user_id == user_id)
    )
    if origin is not None:
        statement = statement.where(adb.Transaction.origin == origin)
    if since is not None:
        statement = statement.where(adb.Transaction.posted_at >= datetime.combine(since, time.min))
    if until is not None:
        statement = statement.where(adb.Transaction.posted_at <= datetime.combine(until, time.max))
    return statement


def ledger_rows_to_frame(rows: Sequence[Row[Any]]) -> pl.DataFrame:
    """Project `ledger_statement`'s rows into a `LEDGER_FRAME_SCHEMA` frame.

    Built column at a time rather than row at a time: Polars stores
    columns, so handing it one list per column skips building 200k
    intermediate dicts on the way in.

    `amount` is the one column that is *not* passed through as stored.
    Postgres returns `MONEY` as an exact `Decimal` and the frame's column
    is `Float64`, so this is the `Decimal -> float` crossing that
    `ledger.frame` declares as the single sanctioned analytics seam (T1) —
    hence the explicit `to_analytics_amount` call rather than letting
    Polars coerce inside the constructor. Same value either way; the point
    is that the crossing stays greppable and cannot move.

    Parameters
    ----------
    rows
        `ledger_statement`'s result rows.

    Returns
    -------
    polars.DataFrame
        `LEDGER_FRAME_SCHEMA`-shaped, sorted by date then posting id.
    """
    frame = pl.DataFrame(
        {
            "posting_id": [row.posting_id for row in rows],
            "transaction_id": [row.transaction_id for row in rows],
            "account_id": [row.account_id for row in rows],
            "posted_at": [row.posted_at for row in rows],
            "amount": [to_analytics_amount(row.amount) for row in rows],
            "currency": [row.currency for row in rows],
            "category_id": [row.category_id for row in rows],
            "subcategory_id": [row.subcategory_id for row in rows],
            "budget_id": [row.budget_id for row in rows],
            # `array_agg` over no rows is `NULL`, and the frame's contract is
            # an empty list for an untagged posting.
            "tag_ids": [row.tag_ids if row.tag_ids is not None else [] for row in rows],
            "description": [row.description for row in rows],
            "meta": [row.meta for row in rows],
        },
        schema=LEDGER_FRAME_SCHEMA,
    )
    return frame.sort("posted_at", "posting_id")
