"""The resolved-posting projection: a cache of the overlay pipeline's output, and the triggers that keep it honest.

## What this is, and what it is emphatically not

`ledger.resolution` is the single implementation of "what a posting
resolves to". This module stores that function's *output* so SQL can filter,
sort and count the values a user actually sees. It is a cache of the
resolver, never a second resolver: no overlay precedence is expressed in SQL
anywhere below, and none may be. Mirroring the pipeline in SQL was costed and
rejected (known gap 6) because two implementations that must agree forever
eventually do not, and the disagreement is silent — a filtered list that
does not match the screen.

The alternative to caching was measured rather than assumed: resolving the
whole ledger per request is 0.57 s at 10k transactions and 2.5 s at 50k, and
12.4 s on the 170k-transaction audit database. Filtering the projection
instead is 4 ms and 23 ms at those two volumes.

## Why staleness is handled in the database

A projection that disagrees with the pipeline is silently wrong, so the
question is not "which write paths do we remember to hook" but "how is
forgetting made impossible". Three facts about this schema settle it:

- there is not one ORM `relationship()` in `src/`, so every cascade is
  DB-level and invisible to a SQLAlchemy unit-of-work listener;
- two of the hottest writers issue raw SQL —
  `repositories.interpretation._upsert_rule` for `categorization_rules` and
  `repositories.accounts.insert_manual_transfers` for
  `transactions`/`postings`;
- several routers write through `session.query(...).delete()`, which bypasses
  the ORM entirely.

A trigger sees all three. So every table in
`precedence.RESOLUTION_TRIGGERED_TABLES` carries statement-level triggers
that enqueue the affected transactions into `ResolvedPostingDirty`, and
`repositories.projection` drains that queue before it reads. A write path
that nobody told about the projection still marks its rows stale, because
the database did it.

Statement-level with a transition table, not row-level: an import writing
340k postings enqueues once per statement, set-based, rather than 340k
times. Four triggers per table rather than one — Postgres refuses a
transition table on a trigger declared for more than one event, so `INSERT`,
`UPDATE` (old rows), `UPDATE` (new rows) and `DELETE` are separate.

## The one join that cannot work, and what covers it

A child table's trigger resolves its transaction by joining up to
`postings`. When the parent is deleted in the *same statement* — a
`DELETE FROM transactions` cascading into `postings` and on into
`posting_overrides` — that join finds nothing, because an `AFTER ...
FOR EACH STATEMENT` trigger runs when the statement is already done. This
was measured, not reasoned: the cascade case enqueued zero rows until
`transactions` and `postings` carried triggers of their own, which resolve
their transaction directly from the transition table with no join at all.

Every overlay row hangs off a posting or a transaction, and both carry their
own triggers, so the only join that can fail is one the parent's trigger has
already covered. `tests/accounting/test_projection_triggers.py` holds that
case.

## What generalises

`_trigger_statements` is written against nothing accounting-specific: a
queue table, a set of source tables, and one SQL fragment per table mapping
a changed row to the keys it dirties. C4b (persisting FIFO-matched tax lots)
is the same shape of problem and could lift it — but deliberately not yet,
and it must not be hoisted into a shared package on speculation. The two
ledgers are independent, C4b's derived value is *cumulative* where this one
is local to a transaction, and there is no second caller today.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, Text, event, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
from accounting.precedence import RESOLUTION_TRIGGERED_TABLES
from db.base import MONEY, Base, Timestamped
from db.models import CURRENCY_CODE_COLUMN

if TYPE_CHECKING:
    from sqlalchemy import Connection, MetaData

DIRTY_TABLE = "resolved_postings_dirty"
"""The staleness queue's table name, referenced by every generated trigger."""

PROJECTION_TABLE = "resolved_postings"
"""The projection's own table name."""

TRIGGER_FUNCTION_PREFIX = f"{SCHEMA}.mark_resolved_dirty_"
"""Every generated trigger function starts with this, so the whole set is greppable in `pg_proc`."""

TRIGGER_NAME_PREFIX = "resolved_dirty_"
"""Every generated trigger starts with this, so coverage is greppable in `pg_trigger`."""


class ResolvedPosting(Base, Timestamped):
    """One posting as `ledger.resolution` resolves it — the row `GET /postings` returns, stored.

    The columns are deliberately the wire shape of
    `api.api_models.PostingRow`, plus two predicates the transactions
    screen filters on and one internal key. Making the projection *be* the
    response shape is what lets the equivalence test say the whole thing in
    one assertion: the stored rows for a transaction equal a fresh pipeline
    run over that transaction, field for field.

    Keyed by `(user_id, posting_id)` where `posting_id` is the natural key
    string, not a foreign key into `postings`. It cannot be one: a split
    resolves into legs with synthetic ids (`f"{posting_id}:split:{n}"`, see
    `ledger.categorization.apply_posting_splits`) that no `postings` row
    carries. `transaction_row_id` is the real `transactions.id` beside the
    natural key, because that is what the dirty queue holds and what a
    recompute deletes by — a transaction that has been deleted has no
    natural key left to look up.

    Two columns are stored that `PostingRow` does not carry, both because
    the transactions screen filters on them and both cheap to store because
    their inputs are already declared resolution sources:

    - `is_real_income_expense` mirrors
      `dashboard.income_statement.real_income_expense_legs` — not on a
      virtual placeholder account, sibling leg is, and not a linked
      transfer. Its input beyond the frame is `accounts.kind`, already a
      declared source.
    - `is_excluded_from_rule` is "this transaction is opted out of at least
      one transfer rule", from `categorization_rule_exclusions`, likewise
      already a declared source.

    What is deliberately *not* stored: any predicate that combines a
    resolved value with live reference data. "Needs categorizing" depends on
    whether a category has children, so it is a SQL expression over a join
    at read time (see `repositories.projection`). Storing it would make
    every `categories` write invalidate the projection for a reason
    resolution does not have.

    The linked transfer's *partner* is likewise not denormalised here. A
    page joins the projection back to itself on `linked_transaction_id` to
    build it (see `repositories.projection.linked_legs`). Copying the
    partner's account and description onto this row would create a second
    staleness axis — the partner changing would have to find and rewrite
    every row pointing at it — and one is already the hard part.
    """

    __tablename__ = PROJECTION_TABLE
    __table_args__ = (
        Index("ix_resolved_postings_user_posted_at", "user_id", "posted_at", "transaction_id"),
        Index("ix_resolved_postings_user_transaction_row", "user_id", "transaction_row_id"),
        Index("ix_resolved_postings_user_category", "user_id", "category_id"),
        Index("ix_resolved_postings_user_account", "user_id", "account_id"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    posting_id: Mapped[str] = mapped_column(primary_key=True)
    """The resolved posting's natural key — a `postings.natural_key`, or a split leg's synthetic id."""
    transaction_id: Mapped[str]
    """The transaction's natural key, as the frame and the wire carry it."""
    transaction_row_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    """The transaction's `id`, which the dirty queue holds and a recompute deletes by.

    Not a foreign key: a recompute has to be able to delete the rows of a
    transaction that no longer exists, and a `CASCADE` would have removed
    them before the drain ran, hiding the deletion from the one place that
    reconciles it."""
    account_id: Mapped[str]
    posted_at: Mapped[datetime]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(ForeignKey(CURRENCY_CODE_COLUMN))
    category_id: Mapped[str | None] = mapped_column(default=None)
    subcategory_id: Mapped[str | None] = mapped_column(default=None)
    budget_id: Mapped[str | None] = mapped_column(default=None)
    tag_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    """The resolved tag set. Postgres' own `ARRAY`, not the generic one: the tag filter is an
    overlap test (`tag_ids && ARRAY[...]`), and only the dialect type offers that operator."""
    description: Mapped[str] = mapped_column(default="")
    meta: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    pending_source: Mapped[str | None] = mapped_column(default=None)
    pending_selected: Mapped[bool] = mapped_column(default=True)
    resolved_by_transfer_rule_id: Mapped[str | None] = mapped_column(default=None)
    manual_transfer_override_posting_id: Mapped[str | None] = mapped_column(default=None)
    is_linked_transfer: Mapped[bool] = mapped_column(default=False)
    linked_transaction_id: Mapped[str | None] = mapped_column(default=None)
    transfer_link_source: Mapped[str | None] = mapped_column(default=None)
    is_real_income_expense: Mapped[bool] = mapped_column(default=False)
    is_excluded_from_rule: Mapped[bool] = mapped_column(default=False)


class ResolvedPostingDirty(Base, Timestamped):
    """One transaction whose projection rows are out of date, enqueued by a trigger and drained by a read.

    The whole of the invalidation contract: a row here means "recompute this
    transaction before trusting the projection for it". Presence is the only
    state — there is no "how stale" and no timestamp anything reads, because
    the recompute is a full replace of that transaction's rows either way.

    A *wide* change — a transfer rule, an account, a category — enqueues
    every one of the user's transactions rather than setting a separate
    "rebuild everything" flag. That is deliberate: one queue means one drain
    and one code path, and the alternative was a second table whose only
    purpose was to be checked in a second place. The insert is one set-based
    statement (`SELECT id FROM transactions WHERE user_id = ...`), which is
    a small constant next to the rebuild it precedes.

    No foreign key on `transaction_id`. The row has to survive the deletion
    of the transaction it names, because "this transaction is gone" is
    exactly a change the projection has to be told about — its rows need
    removing.
    """

    __tablename__ = DIRTY_TABLE
    __table_args__ = ({"schema": SCHEMA},)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    """The transaction's row `id` — see this class's docstring on why it is not a foreign key."""


_WHOLE_LEDGER = f"""
    SELECT t.user_id, t.id
    FROM {SCHEMA}.transactions AS t
    WHERE t.user_id IN (SELECT DISTINCT user_id FROM changed)
"""  # noqa: S608 — `SCHEMA` is this package's own constant, never caller input
"""Every transaction the changed rows' owners have — what a change with no bounded blast radius enqueues.

Used by the three tables whose rows can change the resolution of any
transaction at all: `categorization_rules` (a rule's `description_contains`
can match anything), `accounts` (a `kind` change moves
`ledger.categorization._safe_rule_matches`' safe set, and a newly created
account can make a rule that named it start matching) and `categories` (a
retirement rewrites every posting imported under the retired key).

Narrowing `categories` to the postings under the retired key is exactly
derivable from `ledger.categorization.apply_category_redirects`, which only
rewrites rows whose `category_id` is a key of the redirect map. It is
deliberately not done here — see item C9.
"""

_VIA_POSTING = f"""
    SELECT p.user_id, p.transaction_id
    FROM changed AS c
    JOIN {SCHEMA}.postings AS p ON p.id = c.posting_id AND p.user_id = c.user_id
"""  # noqa: S608 — `SCHEMA` is this package's own constant, never caller input
"""An overlay row that names a posting directly."""

AFFECTED_TRANSACTIONS: dict[str, str] = {
    # The two tables that resolve their transaction with no join at all, and
    # so are the only ones that still work when the parent of a cascade is
    # being deleted in the same statement. See this module's docstring.
    "transactions": "SELECT c.user_id, c.id FROM changed AS c",
    "postings": "SELECT c.user_id, c.transaction_id FROM changed AS c",
    "posting_tags": _VIA_POSTING,
    "posting_overrides": _VIA_POSTING,
    "posting_splits": _VIA_POSTING,
    # `suggestions` holds both lifecycles in one table. A dismissed row has a
    # null `posting_id` by `ck_suggestions_dismissed_shape` and reaches the
    # resolved ledger not at all, so the join drops it — which is the correct
    # answer, not an oversight.
    "suggestions": _VIA_POSTING,
    "posting_split_legs": f"""
        SELECT p.user_id, p.transaction_id
        FROM changed AS c
        JOIN {SCHEMA}.posting_splits AS s ON s.id = c.posting_split_id AND s.user_id = c.user_id
        JOIN {SCHEMA}.postings AS p ON p.id = s.posting_id AND p.user_id = s.user_id
    """,  # noqa: S608 — `SCHEMA` is this package's own constant, never caller input
    "posting_override_tags": f"""
        SELECT p.user_id, p.transaction_id
        FROM changed AS c
        JOIN {SCHEMA}.posting_overrides AS o ON o.id = c.override_id AND o.user_id = c.user_id
        JOIN {SCHEMA}.postings AS p ON p.id = o.posting_id AND p.user_id = o.user_id
    """,  # noqa: S608 — `SCHEMA` is this package's own constant, never caller input
    "categorization_rule_exclusions": "SELECT c.user_id, c.transaction_id FROM changed AS c",
    # A merge decision changes both sides: the duplicates it drops, and the
    # kept transaction, whose description it rewrites.
    "posting_merges": f"""
        SELECT c.user_id, c.kept_transaction_id FROM changed AS c
        UNION ALL
        SELECT d.user_id, d.duplicate_transaction_id
        FROM changed AS c
        JOIN {SCHEMA}.posting_merge_duplicates AS d ON d.merge_id = c.id AND d.user_id = c.user_id
    """,  # noqa: S608 — `SCHEMA` is this package's own constant, never caller input
    "posting_merge_duplicates": f"""
        SELECT c.user_id, c.duplicate_transaction_id FROM changed AS c
        UNION ALL
        SELECT m.user_id, m.kept_transaction_id
        FROM changed AS c
        JOIN {SCHEMA}.posting_merges AS m ON m.id = c.merge_id AND m.user_id = c.user_id
    """,  # noqa: S608 — `SCHEMA` is this package's own constant, never caller input
    "transfer_linked_transactions": "SELECT c.user_id, c.transaction_id FROM changed AS c",
    # The link row itself carries `source` and `rule_id`, both of which reach
    # the frame — and `rule_id` is set null when its rule is deleted, without
    # the membership rows being touched. So this needs its own trigger rather
    # than relying on the memberships'.
    "transfer_links": f"""
        SELECT m.user_id, m.transaction_id
        FROM changed AS c
        JOIN {SCHEMA}.transfer_linked_transactions AS m ON m.link_id = c.id AND m.user_id = c.user_id
    """,  # noqa: S608 — `SCHEMA` is this package's own constant, never caller input
    # A tag's natural key is what the frame carries, so a change to it reaches
    # every posting wearing the tag, through either junction. A delete needs no
    # coverage here — the cascade fires each junction's own trigger while the
    # postings are still there.
    "tags": f"""
        SELECT p.user_id, p.transaction_id
        FROM changed AS c
        JOIN {SCHEMA}.posting_tags AS pt ON pt.tag_id = c.id AND pt.user_id = c.user_id
        JOIN {SCHEMA}.postings AS p ON p.id = pt.posting_id AND p.user_id = pt.user_id
        UNION ALL
        SELECT p.user_id, p.transaction_id
        FROM changed AS c
        JOIN {SCHEMA}.posting_override_tags AS ot ON ot.tag_id = c.id AND ot.user_id = c.user_id
        JOIN {SCHEMA}.posting_overrides AS o ON o.id = ot.override_id AND o.user_id = ot.user_id
        JOIN {SCHEMA}.postings AS p ON p.id = o.posting_id AND p.user_id = o.user_id
    """,  # noqa: S608 — `SCHEMA` is this package's own constant, never caller input
    "accounts": _WHOLE_LEDGER,
    "categories": _WHOLE_LEDGER,
    "categorization_rules": _WHOLE_LEDGER,
}
"""Per source table, the `SELECT` that maps its changed rows to the transactions they dirty.

Each yields `(user_id, transaction_id)` over the transition table `changed`,
and is inlined into that table's own trigger function. Keyed by
`precedence.RESOLUTION_TRIGGERED_TABLES` and asserted equal to it below, so
a table declared a resolution input without a mapping — or given a mapping
without being declared an input — fails at import.

Nothing here expresses overlay precedence, and nothing here decides what a
posting resolves to. Each entry answers only "which transactions might have
changed", and is allowed to over-approximate; the recompute itself is
`ledger.resolution`, unchanged.
"""

_UNDECLARED = sorted(AFFECTED_TRANSACTIONS.keys() - RESOLUTION_TRIGGERED_TABLES)
_UNMAPPED = sorted(RESOLUTION_TRIGGERED_TABLES - AFFECTED_TRANSACTIONS.keys())
if _UNDECLARED or _UNMAPPED:  # pragma: no cover — an import-time guard, not a branch
    _message = (
        "AFFECTED_TRANSACTIONS must cover exactly `precedence.RESOLUTION_TRIGGERED_TABLES`; "
        f"mapped but not declared a resolution input: {_UNDECLARED or 'none'}; "
        f"declared an input but unmapped: {_UNMAPPED or 'none'}"
    )
    raise RuntimeError(_message)


_TRIGGER_EVENTS: tuple[tuple[str, str, str], ...] = (
    ("ins", "INSERT", "NEW"),
    ("upd_new", "UPDATE", "NEW"),
    ("upd_old", "UPDATE", "OLD"),
    ("del", "DELETE", "OLD"),
)
"""The four triggers each source table gets, and which transition table each reads.

Four rather than one because Postgres refuses a transition table on a
trigger declared for more than one event ("transition tables cannot be
specified for triggers with more than one event"). `UPDATE` therefore needs
two: a row whose key columns moved dirties both the transaction it left and
the one it joined, and only `OLD`/`NEW` respectively can say which.
"""


def _trigger_statements(table: str, affected: str) -> tuple[str, ...]:
    """Build the function and four triggers that mark one source table's changes dirty.

    Deliberately generic in everything but its arguments — the queue table,
    the source table and the mapping. See this module's docstring on what
    that would take to share with `trades`, and why it is not shared yet.

    Parameters
    ----------
    table
        The source table's bare name, in the `accounting` schema.
    affected
        A `SELECT` over the transition table `changed` yielding
        `(user_id, transaction_id)`.

    Returns
    -------
    tuple[str, ...]
        The `CREATE FUNCTION` followed by four `CREATE TRIGGER`s.
    """
    function = f"{TRIGGER_FUNCTION_PREFIX}{table}"
    statements = [
        f"""
        CREATE OR REPLACE FUNCTION {function}() RETURNS trigger AS $body$
        BEGIN
            INSERT INTO {SCHEMA}.{DIRTY_TABLE} (user_id, transaction_id)
            {affected.strip()}
            ON CONFLICT DO NOTHING;
            RETURN NULL;
        END;
        $body$ LANGUAGE plpgsql;
        """
    ]
    statements += [
        f"""
        CREATE TRIGGER {TRIGGER_NAME_PREFIX}{table}_{suffix}
        AFTER {sql_event} ON {SCHEMA}.{table}
        REFERENCING {transition} TABLE AS changed
        FOR EACH STATEMENT EXECUTE FUNCTION {function}();
        """
        for suffix, sql_event, transition in _TRIGGER_EVENTS
    ]
    return tuple(statements)


PROJECTION_TRIGGER_STATEMENTS: tuple[str, ...] = tuple(
    statement
    for table in sorted(AFFECTED_TRANSACTIONS)
    for statement in _trigger_statements(table, AFFECTED_TRANSACTIONS[table])
)
"""Every statement that installs the staleness triggers, in order.

Executed from two places against one definition, exactly as
`accounting.db.triggers.ZERO_SUM_STATEMENTS` is: the migration (the real
database) and the `after_create` hook below (the test suite's
`Base.metadata.create_all`, which knows nothing about triggers). Without the
hook, the projection would be kept fresh in production and nowhere else, and
every test asserting it would be asserting against a cache nothing
invalidates.
"""


def install_projection_triggers(
    target: MetaData,  # noqa: ARG001 — the parameter list is SQLAlchemy's `after_create` signature
    connection: Connection,
    **kwargs: Any,  # noqa: ANN401 — likewise
) -> None:
    """Install the staleness triggers once `create_all` has built the tables they name.

    Registered on the metadata rather than on any one table, because a
    trigger names two tables — its source and the queue it inserts into —
    and `create_all` orders by foreign-key dependency, which says nothing
    about that pairing. Waiting until every table exists is the only
    ordering that is always right.

    The guard is what keeps that from over-reaching: `Base.metadata` is
    shared with `trades`, so a `create_all` restricted to that package's
    tables would otherwise try to install `accounting` triggers on tables it
    did not create.

    The migration installs the same statements itself (see
    `migration.versions.000000000002_resolved_posting_projection`); this is
    the other half of the two-path rule `db.triggers` states — without it the
    projection would be kept fresh in production and nowhere else, and every
    test asserting on it would be asserting against a cache nothing
    invalidates.

    Parameters
    ----------
    target
        The metadata that was created; unused, the guard reads `tables`.
    connection
        The connection `create_all` is running on.
    **kwargs
        SQLAlchemy's own event payload; `tables` is the list just created.
    """
    tables = kwargs.get("tables") or []
    if not any(getattr(table, "name", None) == DIRTY_TABLE for table in tables):
        return
    for statement in PROJECTION_TRIGGER_STATEMENTS:
        connection.execute(text(statement))


event.listen(Base.metadata, "after_create", install_projection_triggers)
