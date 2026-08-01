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

## A read that commits

`drain` is a write on the read path, which is unusual enough to state: it
commits, and then the request goes on to query. That matters because
`db.session.set_rls_user` is transaction-local by design, and Postgres
resets an undeclared GUC to the *empty string* rather than to null — so
every subsequent RLS policy on the same session would compare
`user_id = NULL` and match nothing. Not an error: a silently empty page.
`drain` therefore re-arms the session itself, the same thing
`ledger.transfers.reconcile_and_persist_rule_links` does after its own
mid-request commit, and `tests/db/test_rls_isolation.py` holds it under a
real policy.

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

from dataclasses import dataclass
from datetime import datetime, time
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, false, func, insert, or_, select
from sqlalchemy.orm import aliased

import accounting.db as adb
from accounting.api.api_models import (
    CONFIRMED,
    NO_SUBCATEGORY,
    UNCATEGORIZED,
    LinkedLeg,
    PostingFilters,
    PostingPageCounts,
)
from accounting.importers.ingest import load_ledger
from accounting.ledger.resolution import (
    ResolvedPostings,
    apply_overlays,
    overlay_context,
    resolve_postings,
    resolved_display_rows,
)
from accounting.repositories.interpretation import load_overrides_for_postings
from accounting.taxonomy import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID
from db.base import any_text, any_uuid
from db.money import quantize_money
from db.session import set_rls_user

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy import ColumnElement
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
    # The commit above ends the transaction `app.current_user_id` was set
    # local to, and Postgres resets an undeclared GUC to the empty string
    # rather than to null — so every RLS policy on this session would go on
    # matching `user_id = NULL` and silently return nothing. This is a *read*
    # that commits, which is unusual, and the failure it would cause is an
    # empty page rather than an error. See `db.session.set_rls_user`.
    set_rls_user(session, user_id)
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
    set_rls_user(session, user_id)  # See `drain`.
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


# --------------------------------------------------------------------------
# Reading the projection: the filter, the sort, the page and the counts.
#
# Everything below is an ordinary query over resolved columns. That is the
# whole point of the projection existing — no overlay precedence is expressed
# here, and none may be. The values these predicates read were computed by
# `ledger.resolution` and stored; this only selects among them.
# --------------------------------------------------------------------------

_PLACEHOLDER_ACCOUNT_IDS = (UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID)
"""Never rendered as a row of their own, so never on a page and never in a count.

The same exclusion `web/src/lib/transactionFilters.ts` applies first, moved
to where the rows are selected rather than after they have all been fetched.
"""

_SORT_COLUMNS: dict[str, Any] = {
    "posted_at": adb.ResolvedPosting.posted_at,
    "account_id": adb.ResolvedPosting.account_id,
    "description": adb.ResolvedPosting.description,
    "amount": adb.ResolvedPosting.amount,
    "category_id": adb.ResolvedPosting.category_id,
    "subcategory_id": adb.ResolvedPosting.subcategory_id,
    "tag_ids": func.array_to_string(adb.ResolvedPosting.tag_ids, ","),
}
"""One sortable column per sortable heading on the transactions table.

`tag_ids` sorts on its comma-joined text, which is what the client-side sort
it replaces did — `useSortableRows` compares `String(x)` for anything that is
not a number, and `String(['a','b'])` is `'a,b'`.
"""


def _categories_with_children(user_id: uuid.UUID) -> Any:  # noqa: ANN401 — a SQLAlchemy scalar subquery
    """Select the natural key of every category that has at least one subcategory.

    Read live rather than stored on the projection. "Needs categorizing"
    combines a resolved value with reference data, and storing it would make
    a category gaining a child invalidate the projection for a reason
    resolution does not have — see `accounting.db.projection.ResolvedPosting`.

    Returns
    -------
    sqlalchemy.ScalarSelect
    """
    parent = aliased(adb.Category)
    return (
        select(parent.natural_key)
        .select_from(adb.Category)
        .join(parent, (adb.Category.parent_category_id == parent.id) & (parent.user_id == adb.Category.user_id))
        .where(adb.Category.user_id == user_id, adb.Category.retired_at.is_(None))
        .scalar_subquery()
    )


def _needs_categorizing(user_id: uuid.UUID) -> ColumnElement[bool]:
    """Build the "Needs categorizing" predicate, mirroring `web/src/components/accounting/transactionCategorization.ts`.

    A row wants a category when it is a real income/expense leg — an
    internal transfer is never categorizable — and either has no category,
    still carries an unvalidated suggestion, or sits under a parent whose
    subcategory has not been picked yet.

    Returns
    -------
    sqlalchemy.ColumnElement[bool]
    """
    row = adb.ResolvedPosting
    return row.is_real_income_expense & (
        row.pending_source.is_not(None)
        | row.category_id.is_(None)
        | (row.category_id.in_(_categories_with_children(user_id)) & row.subcategory_id.is_(None))
    )


def _multi_select(
    matches: ColumnElement[bool], *, selected: Sequence[str], exclude: bool
) -> ColumnElement[bool] | None:
    """Apply one multi-select's own semantics: empty means no restriction, `exclude` inverts.

    An exclusion is `IS NOT TRUE`, never `NOT (...)`, and that is not
    stylistic. SQL's three-valued logic makes `NOT (category_id IN ('x'))`
    evaluate to `NULL` — not `TRUE` — for a row whose category is null, so a
    plain negation silently drops every uncategorized row from "exclude this
    category". The client-side pass this replaces returns `!includes(...)`,
    which keeps them. `IS NOT TRUE` is the operator that means what the
    checkbox means.

    Returns
    -------
    sqlalchemy.ColumnElement[bool] or None
        `None` when the filter selects nothing and so restricts nothing.
    """
    if not selected:
        return None
    return matches.is_not(True) if exclude else matches


def _transfer_flag_predicate(flags: Sequence[str]) -> ColumnElement[bool]:
    """Whether a row carries any of the named transfer flags.

    Mirrors `transferFlagsForPosting` in
    `web/src/lib/transactionFilters.ts`, including the part that is easy to
    get wrong: `excluded` is a historical fact rather than a transfer
    classification, so it neither makes a row a transfer nor stops it being
    `none`.

    Returns
    -------
    sqlalchemy.ColumnElement[bool]
    """
    row = adb.ResolvedPosting
    # `IS NOT DISTINCT FROM`, not `=`. `transfer_link_source` is nullable, so
    # `is_linked_transfer AND (source = 'rule')` is `NULL` — not `FALSE` — for a
    # linked row with no source, which makes `none` null too and drops the row
    # out of *every* transfer-flag filter at once. Unreachable through
    # `ledger.transfers.apply_transfer_links`, which sets both columns from the
    # same join, but this is the same three-valued trap the exclusions already
    # had once (see `_multi_select`) and it costs nothing to close.
    by_rule = row.resolved_by_transfer_rule_id.is_not(None) | (
        row.is_linked_transfer & row.transfer_link_source.is_not_distinct_from("rule")
    )
    by_hand = row.manual_transfer_override_posting_id.is_not(None) | (
        row.is_linked_transfer & row.transfer_link_source.is_not_distinct_from("manual")
    )
    available: dict[str, ColumnElement[bool]] = {
        "rule": by_rule,
        "manual": by_hand,
        "excluded": row.is_excluded_from_rule.is_(True),
        "none": ~(by_rule | by_hand),
    }
    # `false()` as the seed rather than a bare `or_()`, which is deprecated
    # and which an empty flag list would otherwise reach — `_multi_select`
    # discards the result in that case, but building it is still a warning.
    return or_(false(), *(available[flag] for flag in flags))


def _escape_like(term: str) -> str:
    """Escape the two wildcards `ILIKE` would otherwise read out of a user's search term.

    A search for `50%` matches descriptions containing "50%", not every
    description starting with "50".

    Returns
    -------
    str
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _nullable_multi_select(
    column: Any,  # noqa: ANN401 — any nullable text column of the projection
    *,
    selected: Sequence[str],
    sentinel: str,
    exclude: bool,
) -> ColumnElement[bool] | None:
    """Build the clause for a multi-select whose "none of them" option is a sentinel value.

    The three that have one — categories, subcategories and pending source —
    differ in nothing but which column and which sentinel, so the shape is
    written once. See `api.api_models.UNCATEGORIZED` for why a sentinel
    rather than a null.

    Returns
    -------
    sqlalchemy.ColumnElement[bool] or None
        `None` when the filter selects nothing and so restricts nothing.
    """
    named = [value for value in selected if value != sentinel]
    matches = column.in_(named) if named else false()
    if sentinel in selected:
        matches |= column.is_(None)
    return _multi_select(matches, selected=selected, exclude=exclude)


def filter_predicates(user_id: uuid.UUID, filters: PostingFilters) -> list[ColumnElement[bool]]:
    """Turn the filter bar into predicates over the projection.

    One clause per control, conjunctive, in the order
    `web/src/lib/transactionFilters.ts` evaluates them — so the two can be
    read side by side, which is what a reviewer needs in order to believe
    they agree.

    Parameters
    ----------
    user_id
        Whose rows to select. Row-Level Security enforces this too; the
        predicate is what lets the planner use the `(user_id, ...)` indexes.
    filters
        The filter bar's current state.

    Returns
    -------
    list[sqlalchemy.ColumnElement[bool]]
    """
    row = adb.ResolvedPosting
    predicates: list[ColumnElement[bool]] = [
        row.user_id == user_id,
        row.account_id.not_in(_PLACEHOLDER_ACCOUNT_IDS),
    ]
    if filters.needs_categorizing:
        predicates.append(_needs_categorizing(user_id))
    if filters.search:
        predicates.append(row.description.ilike(f"%{_escape_like(filters.search)}%", escape="\\"))
    if filters.account is not None:
        matches = row.account_id == filters.account
        # `IS NOT TRUE` here too, for uniformity rather than necessity —
        # `account_id` is `NOT NULL`, but an exclusion that reads differently
        # from the four beside it is an invitation to change the wrong one.
        predicates.append(matches.is_not(True) if filters.account_exclude else matches)

    optional = [
        _nullable_multi_select(
            row.category_id, selected=filters.categories, sentinel=UNCATEGORIZED, exclude=filters.categories_exclude
        ),
        _nullable_multi_select(
            row.subcategory_id,
            selected=filters.subcategories,
            sentinel=NO_SUBCATEGORY,
            exclude=filters.subcategories_exclude,
        ),
        _nullable_multi_select(
            row.pending_source, selected=filters.pending, sentinel=CONFIRMED, exclude=filters.pending_exclude
        ),
        _multi_select(row.tag_ids.overlap(filters.tags), selected=filters.tags, exclude=filters.tags_exclude),
        _multi_select(
            _transfer_flag_predicate(filters.transfer_flags),
            selected=filters.transfer_flags,
            exclude=filters.transfer_flags_exclude,
        ),
    ]
    predicates += [clause for clause in optional if clause is not None]
    predicates += _date_predicates(filters)

    if filters.income_expense is not None:
        # Only a real income/expense leg has a side at all — an internal
        # transfer is neither, however its amount is signed.
        predicates.append(row.is_real_income_expense.is_(True))
        predicates.append(row.amount < 0 if filters.income_expense == "expense" else row.amount >= 0)
    if filters.categorized == "categorized":
        predicates.append(row.category_id.is_not(None))
    if filters.categorized == "uncategorized":
        predicates.append(row.category_id.is_(None))
    return predicates


def _date_predicates(filters: PostingFilters) -> list[ColumnElement[bool]]:
    """Bound the window by a single month, or by an explicit range — the filter bar offers one or the other.

    Returns
    -------
    list[sqlalchemy.ColumnElement[bool]]
    """
    row = adb.ResolvedPosting
    predicates: list[ColumnElement[bool]] = []
    if filters.month is not None:
        predicates.append(func.to_char(row.posted_at, "YYYY-MM") == filters.month)
    if filters.start is not None:
        predicates.append(row.posted_at >= datetime.combine(filters.start, time.min))
    if filters.end is not None:
        predicates.append(row.posted_at <= datetime.combine(filters.end, time.max))
    return predicates


@dataclass(frozen=True)
class FilteredPage:
    """One page of transaction ids selected from the projection, plus the counts beside it."""

    transaction_ids: list[str]
    """The page's transactions, in the requested order. Natural keys, as the projection stores them."""
    counts: PostingPageCounts
    """What the whole filter matches, ignoring the page window."""


def filtered_page(
    session: Session,
    user_id: uuid.UUID,
    filters: PostingFilters,
    *,
    sort: str,
    descending: bool,
    limit: int,
    offset: int,
) -> FilteredPage:
    """Select one page of matching transactions, ordered, with the counts the screen shows.

    **The page is cut by transaction, not by matching row.** A transaction's
    legs have to arrive together — the transfer badge reads a transaction's
    other leg, and "undo split" needs its siblings — which is the same
    requirement `repositories.ledger.visible_transaction_page` documents for
    the unfiltered page. So the filter selects *rows*, and the page window is
    the set of transactions those rows belong to.

    **A transaction sorts where its best-sorting matching row would.** For
    the overwhelmingly common case of one matching row per transaction that
    is just "sort by that row". Where a split has produced several, `MIN` for
    ascending and `MAX` for descending is the only aggregate that agrees with
    the row-level sort the client used to do — anything else would put the
    transaction somewhere none of its rows are.

    Nulls sort last in both directions, matching `useSortableRows`, so a
    missing category does not read as the top or the bottom of the list.

    Parameters
    ----------
    session
        An open database session. The caller must have drained first.
    user_id
        Whose rows to page.
    filters
        The filter bar's current state.
    sort
        One of `_SORT_COLUMNS`.
    descending
        Which way. Validated by the caller.
    limit, offset
        The window, counted in transactions.

    Returns
    -------
    FilteredPage
    """
    predicates = filter_predicates(user_id, filters)
    column = _SORT_COLUMNS[sort]
    key = (func.max(column) if descending else func.min(column)).label("sort_key")
    # A total order: `posted_at` ties are broken by the transaction's own key,
    # so paging cannot skip or repeat a transaction the way an unstable sort
    # over a `LIMIT` would.
    tiebreak = func.min(adb.ResolvedPosting.transaction_id).label("tiebreak")
    ordered = [
        key.desc().nullslast() if descending else key.asc().nullslast(),
        tiebreak.desc() if descending else tiebreak.asc(),
    ]
    page = (
        select(adb.ResolvedPosting.transaction_id)
        .where(*predicates)
        .group_by(adb.ResolvedPosting.transaction_id)
        .order_by(*ordered)
        .limit(limit)
        .offset(offset)
    )
    # One pass over the matched rows for all four figures. The two pending
    # counts ride along in the aggregate that was already running rather than
    # becoming queries of their own — see `PostingPageCounts`.
    pending = adb.ResolvedPosting.pending_source.is_not(None)
    matched = select(
        func.count(func.distinct(adb.ResolvedPosting.transaction_id)),
        func.count(),
        func.count().filter(pending),
        func.count().filter(pending & adb.ResolvedPosting.pending_selected),
    ).where(*predicates)
    # `needs_categorizing` is counted with its own filter lifted, so the tab's
    # badge reads the same number whichever tab is currently open.
    without_the_tab = filters.model_copy(update={"needs_categorizing": False})
    badge = select(func.count().filter(_needs_categorizing(user_id))).where(
        *filter_predicates(user_id, without_the_tab)
    )

    transactions, postings, pending_rows, selected_rows = session.execute(matched).one()
    return FilteredPage(
        transaction_ids=list(session.execute(page).scalars()),
        counts=PostingPageCounts(
            matched_transactions=transactions,
            matched_postings=postings,
            needs_categorizing=session.execute(badge).scalar_one(),
            pending=pending_rows,
            pending_selected=selected_rows,
        ),
    )


def page_rows(session: Session, user_id: uuid.UUID, transaction_ids: Sequence[str]) -> list[dict[str, Any]]:
    """Every stored row of the named transactions, in the order the page put them.

    Every leg, not only the ones the filter matched: see `filtered_page` on
    why a transaction's legs travel together. Placeholder legs included, for
    the same reason — the transfer badge and "mark as transfer" both read
    them, and the client drops them from the table itself.

    Ordered by `transaction_ids`' own order, which is the sort the client
    asked for, and by posting id within a transaction. Re-sorting by
    `posted_at` here instead — which this did until the sort tests caught it
    — silently ignored every sort but the default: the *window* honoured the
    requested order while the rows inside it came back by date regardless.

    Returns
    -------
    list[dict]
        Keyed by `api.api_models.PostingRow`'s own field names.
    """
    if not transaction_ids:
        return []
    rows = session.execute(
        select(adb.ResolvedPosting).where(
            adb.ResolvedPosting.user_id == user_id, any_text(adb.ResolvedPosting.transaction_id, transaction_ids)
        )
    ).scalars()
    position = {transaction_id: index for index, transaction_id in enumerate(transaction_ids)}
    return sorted(
        (_row_dict(row) for row in rows),
        key=lambda row: (position[row["transaction_id"]], row["posting_id"]),
    )


def _row_dict(row: adb.ResolvedPosting) -> dict[str, Any]:
    """One stored row as the wire shape, with the storage-only columns dropped.

    Returns
    -------
    dict
    """
    return {
        column: getattr(row, column)
        for column in _PROJECTION_COLUMNS
        if column not in {"user_id", "transaction_row_id"}
    }


def linked_legs(session: Session, user_id: uuid.UUID, transaction_ids: Sequence[str | None]) -> dict[str, LinkedLeg]:
    """Look up the real leg of each named transaction, for the transfer badge on the rows pointing at it.

    The one thing a transfer badge needs that is neither on its own row nor
    on a sibling: the *partner* transaction is by definition somewhere else,
    usually much older, so it is not on the page. Joining the projection back
    to itself for the page's `linked_transaction_id`s is O(page) and needs no
    second staleness axis — see `accounting.db.projection.ResolvedPosting` on
    why the partner is not denormalised onto the row instead.

    Placeholder legs are excluded: the badge names the account the money went
    to or came from, which is never one of the two uncategorized
    counterparties. A transaction with several real legs (a split) is
    represented by its first, which is what the client-side
    `realLegByTransactionId` this replaces also did.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose rows to read.
    transaction_ids
        The partners to look up; `None` entries and duplicates are ignored.

    Returns
    -------
    dict[str, LinkedLeg]
        Keyed by transaction natural key; a transaction with no real leg is
        simply absent.
    """
    wanted = {transaction_id for transaction_id in transaction_ids if transaction_id is not None}
    if not wanted:
        return {}
    rows = session.execute(
        select(adb.ResolvedPosting)
        .where(
            adb.ResolvedPosting.user_id == user_id,
            any_text(adb.ResolvedPosting.transaction_id, wanted),
            adb.ResolvedPosting.account_id.not_in(_PLACEHOLDER_ACCOUNT_IDS),
        )
        .order_by(adb.ResolvedPosting.transaction_id, adb.ResolvedPosting.posting_id)
    ).scalars()
    legs: dict[str, LinkedLeg] = {}
    for row in rows:
        legs.setdefault(
            row.transaction_id,
            LinkedLeg(
                transaction_id=row.transaction_id,
                account_id=row.account_id,
                description=row.description,
                posted_at=row.posted_at,
                amount=row.amount,
                currency=row.currency,  # type: ignore[arg-type]
            ),
        )
    return legs


def matching_posting_ids(session: Session, user_id: uuid.UUID, filters: PostingFilters) -> list[str]:
    """Every posting id the filter matches, across every page.

    What a filter-shaped bulk action resolves its target set from, inside the
    same transaction as the write it then performs — so the set cannot shift
    underneath the operation the way a client-supplied list of ids gathered
    over several requests could.

    Returns the matching *rows*, not every leg of their transactions:
    a bulk categorizer acts on the rows a user can see, and a placeholder leg
    is not one of them.

    Returns
    -------
    list[str]
    """
    return list(
        session.execute(
            select(adb.ResolvedPosting.posting_id)
            .where(*filter_predicates(user_id, filters))
            .order_by(adb.ResolvedPosting.posted_at.desc(), adb.ResolvedPosting.posting_id.asc())
        ).scalars()
    )


def matching_rows_for_patterns(session: Session, user_id: uuid.UUID, filters: PostingFilters) -> list[dict[str, Any]]:
    """Select the four resolved columns the bulk pattern matcher needs, for every row the filter matches.

    `description` is what a pattern matches on; `category_id` and
    `subcategory_id` are what the match is locked against, so a row that
    already carries a category only takes a suggestion agreeing with it.
    All four are projection columns, which is why this is a query rather
    than a resolve: the handler used to call `ledger.resolution.resolved_postings`
    and replay the entire ledger in Python to obtain them, which is the cost
    the projection exists to remove and which the cutover makes trivially
    easy to trigger over an unfiltered view.

    Returns
    -------
    list[dict[str, Any]]
        One dict per matching row, ordered as `matching_posting_ids` orders them.
    """
    row = adb.ResolvedPosting
    return [
        {
            "posting_id": posting_id,
            "description": description,
            "category_id": category_id,
            "subcategory_id": subcategory_id,
        }
        for posting_id, description, category_id, subcategory_id in session.execute(
            select(row.posting_id, row.description, row.category_id, row.subcategory_id)
            .where(*filter_predicates(user_id, filters))
            .order_by(row.posted_at.desc(), row.posting_id.asc())
        ).all()
    ]


def distinct_months(session: Session, user_id: uuid.UUID) -> list[str]:
    """Every `YYYY-MM` this user has a resolved posting in, newest first — the month picker's options (C2).

    Derived in SQL rather than from a resident array of every posting, which
    is what the transactions and budget screens used to build it from.
    `posted_at` is the one column no overlay stage rewrites, so this was
    always expressible; what it was waiting for is a screen that no longer
    holds the whole ledger anyway.

    Returns
    -------
    list[str]
    """
    month = func.to_char(adb.ResolvedPosting.posted_at, "YYYY-MM")
    return list(
        session.execute(
            select(month)
            .where(
                adb.ResolvedPosting.user_id == user_id,
                adb.ResolvedPosting.account_id.not_in(_PLACEHOLDER_ACCOUNT_IDS),
            )
            .group_by(month)
            .order_by(month.desc())
        ).scalars()
    )
