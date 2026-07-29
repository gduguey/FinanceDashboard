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

from dataclasses import dataclass
from datetime import datetime, time
from typing import TYPE_CHECKING, Any

import polars as pl
from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import aliased

import accounting.db as adb
from accounting.ledger.frame import LEDGER_FRAME_SCHEMA
from db.base import any_text, any_uuid
from db.money import to_analytics_float as to_analytics_amount

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterable, Sequence
    from datetime import date

    from sqlalchemy import Row
    from sqlalchemy.orm import Session

    from accounting.models import TransactionOrigin


def ledger_statement(
    user_id: uuid.UUID,
    *,
    since: date | None = None,
    until: date | None = None,
    origin: TransactionOrigin | None = None,
    transaction_ids: Sequence[uuid.UUID] | None = None,
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
    transaction_ids
        Restrict to the postings of these transactions — how a page of
        `visible_transaction_page` becomes a frame. Matched as one array
        parameter, so a page of any size is one bind. `None` means every
        transaction; an *empty* sequence means none, and yields no rows.

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
    if transaction_ids is not None:
        statement = statement.where(any_uuid(adb.Transaction.id, transaction_ids))
    if origin is not None:
        statement = statement.where(adb.Transaction.origin == origin)
    if since is not None:
        statement = statement.where(adb.Transaction.posted_at >= datetime.combine(since, time.min))
    if until is not None:
        statement = statement.where(adb.Transaction.posted_at <= datetime.combine(until, time.max))
    return statement


@dataclass(frozen=True)
class TransactionPage:
    """One page of transactions, plus how many there are in total."""

    transaction_ids: list[uuid.UUID]
    """The page's transactions, newest first. Empty past the end of the collection."""
    total: int
    """How many transactions are visible in total, ignoring `limit`/`offset` — what a client needs to page."""


def visible_transaction_page(
    session: Session,
    user_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
    since: date | None = None,
    until: date | None = None,
) -> TransactionPage:
    """Select one page of transaction ids, newest first, and count how many there are.

    **Pages are cut by transaction, never by posting**, and that is a
    correctness requirement rather than a preference. Three things downstream
    need every leg of a transaction present in the same frame:
    `ledger.categorization.apply_rules` only treats a transaction as
    rule-eligible when it can see exactly two legs, one of them a
    placeholder; the transfer badge and the "mark as transfer" target are
    both resolved from a transaction's *other* leg. Cut the page at a
    posting and a transaction straddling the boundary silently resolves
    differently than it would in full history.

    **Merged-away duplicates are excluded here, before `LIMIT`, not filtered
    out of the frame afterwards.** `apply_posting_merges` drops every posting
    of a duplicate transaction, so filtering after the cut would return fewer
    rows than asked for and skew every subsequent offset. The anti-join below
    is that same drop, expressed where it has to happen.

    `posted_at` is the sort key because it is the only column no overlay
    stage rewrites — merges rewrite `description`, splits rewrite `amount`,
    and rules and overrides rewrite `account_id`, so ordering or filtering on
    any of those pre-pipeline would select a different set than the resolved
    values a client sees. `id` breaks ties, and being a UUIDv7 it is itself
    time-ordered, so the order is stable and total.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose transactions to page.
    limit, offset
        The window. Validated by the caller — see `api.api_models.PostingPage`.
    since, until
        Optional inclusive date bounds, matching `ledger_statement`.

    Returns
    -------
    TransactionPage
    """
    merged_away = (
        select(adb.PostingMergeDuplicate.duplicate_transaction_id)
        .where(adb.PostingMergeDuplicate.user_id == user_id)
        .scalar_subquery()
    )
    # Built once and used for both the count and the page, so `total` can
    # never describe a different collection than the rows beside it.
    predicates = [adb.Transaction.user_id == user_id, adb.Transaction.id.not_in(merged_away)]
    if since is not None:
        predicates.append(adb.Transaction.posted_at >= datetime.combine(since, time.min))
    if until is not None:
        predicates.append(adb.Transaction.posted_at <= datetime.combine(until, time.max))
    total = session.execute(select(func.count()).select_from(adb.Transaction).where(*predicates)).scalar_one()
    page = (
        select(adb.Transaction.id)
        .where(*predicates)
        .order_by(adb.Transaction.posted_at.desc(), adb.Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return TransactionPage(transaction_ids=list(session.execute(page).scalars()), total=total)


def transaction_keys_by_posting_key(
    session: Session, user_id: uuid.UUID, transaction_keys: Iterable[str]
) -> dict[str, str]:
    """Map posting natural key to transaction natural key, for the named transactions only.

    What a caller holding a handful of transaction ids needs in order to ask
    a question about their postings — "has any leg of this transaction been
    split?" being the one that exists today. The alternative was loading the
    entire ledger to build the same map for two ids.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose postings to look at.
    transaction_keys
        The transactions' natural keys. Passed as one array parameter, so
        this is safe for any number of them.

    Returns
    -------
    dict[str, str]
        Posting natural key to the natural key of the transaction it belongs
        to. Empty if none of `transaction_keys` names a transaction.
    """
    rows = session.execute(
        select(adb.Posting.natural_key, adb.Transaction.natural_key)
        .select_from(adb.Posting)
        .join(
            adb.Transaction,
            (adb.Posting.transaction_id == adb.Transaction.id) & (adb.Transaction.user_id == adb.Posting.user_id),
        )
        .where(adb.Posting.user_id == user_id, any_text(adb.Transaction.natural_key, transaction_keys))
    ).all()
    return {posting_key: transaction_key for posting_key, transaction_key in rows}  # noqa: C416 — Row is not a tuple


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
