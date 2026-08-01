"""Keeping the resolved-posting projection equal to the pipeline, and reading pages out of it.

`accounting.db.projection` is the schema and the triggers that mark rows
stale. This is the other half: the drain that makes them fresh again, and
the queries the transactions screen is served from once they are.

## Drain on read, not recompute on write

`drain` runs at the top of every read that trusts the projection. The
alternative — recomputing inside each write path — would need every write
path to remember, which is the failure this whole design exists to remove.
Here the read cannot serve a stale row even for a write path nobody has
heard of, because the marker it checks was written by a database trigger
rather than by application code.

The cost is the same work either way; only who waits for it moves. A
narrow change (an override, a split, a merge, a link, an import of N
transactions) recomputes those transactions alone — 27 ms for one, measured
over a 50k-transaction ledger. A wide change (a transfer rule, an account,
a category) enqueues the whole ledger and the next read pays for a full
rebuild: 0.77 s at 10k transactions, 3.8 s at 50k, and about 13 s
extrapolated to the 170k-transaction audit database. A transfer-rule save
already reconciles links over the whole ledger, so it was never a cheap
write; this roughly doubles it rather than introducing a new class of cost.

## Claiming, and two readers at once

The queue is claimed with `DELETE ... RETURNING` inside the caller's
transaction. Two concurrent drains for the same user therefore serialise on
the row locks: the second blocks, then finds the rows gone and returns
nothing, by which time the first has committed both the queue delete and the
projection rows it wrote. If the first rolls back, the markers come back with
it. There is no window in which a marker is neither claimed nor pending.

Recomputing is deterministic, so even an overlap that did occur would write
identical rows.

## The batch is a batch of transactions

Never of postings. Every stage is local to a transaction but not to a leg —
`ledger.categorization.apply_rules` needs to see both legs to decide
eligibility, and a split turns one leg into several. `ledger.resolution`
documents the same requirement for a page; a recompute is the same
constraint under a different name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, insert, select

import accounting.db as adb
from accounting.importers.ingest import load_ledger
from accounting.ledger.resolution import (
    ResolvedPostings,
    apply_overlays,
    overlay_context,
    resolve_postings,
    resolved_display_rows,
)
from accounting.repositories.interpretation import load_overrides_for_postings
from db.base import any_uuid
from db.money import quantize_money

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from accounting.ledger.resolution import OverlayContext

RECOMPUTE_BATCH_TRANSACTIONS = 5_000
"""How many transactions one recompute pass resolves and writes at a time.

A full rebuild of a large ledger would otherwise build one frame of every
posting the user owns and hold it while writing. The batch bounds peak
memory without changing the answer, because resolution is local to a
transaction (see `ledger.resolution.apply_overlays`). Five thousand is
`http_api.pagination.PAGE_LIMIT_MAX`, i.e. the largest page the API will
ever serve from one of these — so the common case of a cold projection
answering one page is a single batch.

The whole-collection overlays are read once per drain rather than once per
batch, which is what `ledger.resolution.OverlayContext` exists for.
"""

_PROJECTION_COLUMNS = (
    "user_id",
    "posting_id",
    "transaction_id",
    "transaction_row_id",
    "account_id",
    "posted_at",
    "amount",
    "currency",
    "category_id",
    "subcategory_id",
    "budget_id",
    "tag_ids",
    "description",
    "meta",
    "pending_source",
    "pending_selected",
    "resolved_by_transfer_rule_id",
    "manual_transfer_override_posting_id",
    "is_linked_transfer",
    "linked_transaction_id",
    "transfer_link_source",
    "is_real_income_expense",
    "is_excluded_from_rule",
)
"""Every column written by a recompute — the mapped columns bar the two timestamps."""


def _transaction_row_ids_by_key(
    session: Session, user_id: uuid.UUID, batch: Sequence[uuid.UUID]
) -> dict[str, uuid.UUID]:
    """Map each batch transaction's natural key to its row id.

    The projection is keyed by natural keys, because that is what the frame
    and the wire carry, but it is *recomputed* by row id, because that is
    what the dirty queue holds and what survives the transaction being
    deleted. This is the one place the two meet, scoped to the batch.

    Returns
    -------
    dict[str, uuid.UUID]
        Empty for a batch whose transactions have all been deleted.
    """
    rows = session.execute(
        select(adb.Transaction.natural_key, adb.Transaction.id).where(
            adb.Transaction.user_id == user_id, any_uuid(adb.Transaction.id, batch)
        )
    ).all()
    return {natural_key: row_id for natural_key, row_id in rows}  # noqa: C416 — Row is not a tuple


def _recompute_batch(session: Session, user_id: uuid.UUID, batch: Sequence[uuid.UUID], context: OverlayContext) -> int:
    """Replace one batch of transactions' projection rows with a fresh pipeline run over them.

    Delete-then-insert rather than upsert, and that is load-bearing: a
    recompute has to be able to produce *fewer* rows than are stored. An
    undone split collapses three rows back into one, a merge drops a
    duplicate transaction's rows entirely, and a deleted transaction leaves
    none at all. An upsert would leave every one of those behind.

    The insert is `executemany`-shaped (a statement plus a list of
    parameter dicts) rather than one `VALUES` list, so SQLAlchemy's own
    `insertmanyvalues` batches it. Twenty-three columns per row would
    otherwise put a 3,000-transaction batch past Postgres' 65,535-parameter
    ceiling — the same cliff `repositories.interpretation` chunks by hand
    for an insert it cannot express this way.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose projection to update.
    batch
        Transaction row ids to recompute.
    context
        The whole-collection overlays, read once per drain.

    Returns
    -------
    int
        How many projection rows this batch wrote.
    """
    session.execute(
        delete(adb.ResolvedPosting).where(
            adb.ResolvedPosting.user_id == user_id, any_uuid(adb.ResolvedPosting.transaction_row_id, batch)
        )
    )
    raw = load_ledger(session, user_id, transaction_ids=list(batch))
    if raw.is_empty():
        # Every transaction in the batch has been deleted. The delete above
        # is the whole of the recompute.
        return 0
    overrides = load_overrides_for_postings(session, user_id, raw["posting_id"].to_list())
    resolved = apply_overlays(raw, context, overrides)
    if resolved.is_empty():
        # Every transaction in the batch was merged away, so it resolves to
        # no rows at all — which is exactly what the projection should hold.
        return 0
    resolution = ResolvedPostings(
        resolved=resolved, raw=raw, overrides=overrides, rules=context.rules, accounts=context.accounts, total=0
    )
    row_ids = _transaction_row_ids_by_key(session, user_id, batch)
    rows = [
        {
            **{column: row[column] for column in _PROJECTION_COLUMNS if column in row},
            "user_id": user_id,
            # Back across the T1 boundary: the frame's `amount` is the float
            # `ledger.frame` sanctions for aggregation, and this column is
            # `NUMERIC(18, 4)`. See `db.money`.
            "amount": quantize_money(row["amount"]),
            "transaction_row_id": row_ids[row["transaction_id"]],
        }
        for row in resolved_display_rows(resolution)
    ]
    session.execute(insert(adb.ResolvedPosting), rows)
    return len(rows)


def drain(session: Session, user_id: uuid.UUID) -> int:
    """Bring this user's projection back into agreement with the pipeline, and commit.

    Claims every marker the staleness triggers have enqueued, recomputes
    those transactions in batches, and deletes their markers in the same
    transaction as the rows it wrote — so the queue and the projection can
    never disagree about what has been done.

    A no-op, and one cheap indexed query, when nothing is dirty. That is the
    common case: a read after a read.

    Parameters
    ----------
    session
        An open database session; `session.commit()` is called if anything
        was claimed.
    user_id
        Whose projection to refresh.

    Returns
    -------
    int
        How many transactions were recomputed — zero when the projection was
        already current.
    """
    claimed = list(
        session.execute(
            delete(adb.ResolvedPostingDirty)
            .where(adb.ResolvedPostingDirty.user_id == user_id)
            .returning(adb.ResolvedPostingDirty.transaction_id)
        ).scalars()
    )
    if not claimed:
        return 0
    context = overlay_context(session, user_id)
    for start in range(0, len(claimed), RECOMPUTE_BATCH_TRANSACTIONS):
        _recompute_batch(session, user_id, claimed[start : start + RECOMPUTE_BATCH_TRANSACTIONS], context)
    session.commit()
    return len(claimed)


def rebuild(session: Session, user_id: uuid.UUID) -> int:
    """Recompute this user's whole projection from scratch, whatever the queue says.

    Not on any request path. It exists for the equivalence test, and as the
    one honest repair if a projection is ever suspected of being wrong —
    `drain` can only fix what it was told about, and this needs no marker at
    all.

    Parameters
    ----------
    session
        An open database session; `session.commit()` is called.
    user_id
        Whose projection to rebuild.

    Returns
    -------
    int
        How many transactions were recomputed.
    """
    session.execute(delete(adb.ResolvedPosting).where(adb.ResolvedPosting.user_id == user_id))
    session.execute(delete(adb.ResolvedPostingDirty).where(adb.ResolvedPostingDirty.user_id == user_id))
    every = list(session.execute(select(adb.Transaction.id).where(adb.Transaction.user_id == user_id)).scalars())
    context = overlay_context(session, user_id)
    for start in range(0, len(every), RECOMPUTE_BATCH_TRANSACTIONS):
        _recompute_batch(session, user_id, every[start : start + RECOMPUTE_BATCH_TRANSACTIONS], context)
    session.commit()
    return len(every)


def fresh_display_rows(session: Session, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Resolve this user's whole ledger through the pipeline and render it as display rows.

    The reference side of the equivalence test: what the projection is
    supposed to equal, computed the slow way, with no cache involved. Not on
    any request path — this is the 0.57 s-at-10k read the projection exists
    to replace.

    Returns
    -------
    list[dict]
        Every resolved posting, newest first, as `resolved_display_rows`
        renders it.
    """
    return resolved_display_rows(resolve_postings(session, user_id))
