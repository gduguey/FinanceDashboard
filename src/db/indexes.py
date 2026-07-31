"""Foreign-key indexing, derived from the schema rather than remembered per table.

Postgres does not index a foreign key for you. Indexing them is the single
most emphasised rule in *Use The Index, Luke!*, and this schema met it in
almost no place: across ~39 tables there were exactly three declared
indexes — one non-unique and two unique expression indexes — so
`postings.transaction_id`, `account_id`, `category_id`,
`budget_id`, every `corrections.*` `posting_id`, and every junction
table's own columns were unindexed. That is DB-audit D1, and it showed up
as a measurably slow cascade delete and as joins that seq-scan the whole
tenant slice.

D2 is the other half. Row-Level Security injects
`user_id = current_setting('app.current_user_id')` into *every* statement,
so an application query's real predicate is always
`user_id AND <whatever else>`, never the foreign key alone.

Those two requirements pull in opposite directions, and the column order
resolves them. There are two distinct access paths:

- **Cascade and referential checks** — `DELETE FROM categories WHERE id = ?`
  makes Postgres scan every referencing table for `category_id = ?`, with
  no `user_id` in sight. This path needs the foreign key to *lead*. It is
  the path the speed audit measured as slow enough to have to background.
- **Application reads** — `WHERE user_id = ? AND category_id = ?`.

Both predicates are equality, and a composite B-tree serves an
equality/equality pair equally well in either order. So `(fk, user_id)`
satisfies both paths with one index, while `(user_id, fk)` satisfies only
the second — the foreign key is not a leftmost prefix there, so the
cascade still seq-scans. `(fk, user_id)` is therefore what this builds.
Queries filtering on `user_id` alone are already served by each table's
`uq_*(user_id, natural_key)`.

The walk is computed here instead of being written out by hand 60 times.
Adding a foreign key adds its index, with nothing for anyone to forget —
the same argument `db.tenant` makes for policies.

Indexes that already exist are never duplicated: if a column is already the
second column of a `(user_id, x)` unique constraint, or already leads an
explicit index, it is skipped.

One class of foreign key gets no index at all, and the same two access paths
are the reason. A reference into a shared dimension table
(`db.tenant.is_reference_table` — `currencies`, `institutions`, `securities`)
satisfies neither:

- **Cascade and referential checks** never happen. A currency is not
  deleted; the reference list is seeded once and the rows outlive every row
  that points at them. Nothing has to scan `postings` for
  `currency = 'EUR'`, because nothing ever removes `'EUR'`.
- **Application reads** never filter on it either. `currency` is *rendered*,
  not searched — every read in this codebase selects a tenant's postings and
  displays whatever currency each one carries.

What an index there would cost is real, though: `(currency, user_id)` over
every posting is a second B-tree to maintain on every insert, keyed on a
column with two distinct values — which no planner would choose anyway.
That is the textbook useless low-cardinality index, and twelve of them —
one per column referencing one of the three dimension tables — is what a
blind walk would have added here. So the walk asks where the foreign key
*points* before indexing it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Index, PrimaryKeyConstraint, UniqueConstraint

from db.tenant import OWNER_COLUMN, is_reference_table

if TYPE_CHECKING:
    from sqlalchemy import Column, Table
    from sqlalchemy.sql.schema import MetaData

_MAX_IDENTIFIER_LENGTH = 63
"""Postgres truncates identifiers past this, which would silently collide two index names."""


def _existing_leading_columns(table: Table) -> set[tuple[str, ...]]:
    """Every column prefix already served by an index or unique constraint.

    A B-tree serves any leftmost prefix of its own column list, so an index
    on `(user_id, posted_at)` already covers lookups on `user_id`. Only
    prefixes are recorded, because only prefixes are usable, and only
    index-backed constraints count — a foreign key creates no index.

    Parameters
    ----------
    table
        The table to inspect.

    Returns
    -------
    set[tuple[str, ...]]
        Each usable leading column combination, as a tuple of column names.
    """
    prefixes: set[tuple[str, ...]] = set()
    column_lists = [tuple(column.name for column in index.columns) for index in table.indexes]
    # Only constraints Postgres actually backs with an index count. A
    # ForeignKeyConstraint emphatically does not — treating one as coverage
    # is precisely the mistake that leaves foreign keys unindexed.
    column_lists += [
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, PrimaryKeyConstraint | UniqueConstraint)
    ]
    for columns in column_lists:
        prefixes.update(columns[:size] for size in range(1, len(columns) + 1))
    return prefixes


def _points_only_at_reference_data(column: Column[object], metadata: MetaData) -> bool:
    """Whether every foreign key on `column` targets a shared dimension table.

    Read per column rather than per constraint, because a composite foreign
    key's columns are indexed individually here. A column is only skipped when
    *none* of its references is to a tenant table, so one reaching reference
    data and real data both still gets its index.

    The target is looked up by name in `metadata` rather than by resolving
    `ForeignKey.column`, and that is not an optimisation. Resolving raises
    `NoReferencedTableError` when the target's module has not been imported
    yet, and one target legitimately has not: `accounts.broker_connection_id`
    names `trades.broker_connections`, and this walk runs from
    `accounting.db.__init__` in a process that may hold only the `accounting`
    package (see `migration/env.py` on why each is optional). An unresolvable
    target is therefore treated as *not* reference data — it gets its index,
    exactly as before this predicate existed — while the three real dimension
    tables are always loaded by then, since a module declaring the foreign key
    is what imports them (`db.models.CURRENCY_CODE_COLUMN`).

    Parameters
    ----------
    column
        The column to classify; assumed to have at least one foreign key.
    metadata
        The metadata the walk is over, used to resolve each target by name.

    Returns
    -------
    bool
    """
    targets = [metadata.tables.get(key.target_fullname.rsplit(".", 1)[0]) for key in column.foreign_keys]
    return all(target is not None and is_reference_table(target) for target in targets)


def _index_name(table: Table, columns: tuple[str, ...]) -> str:
    """Build a deterministic, length-safe index name.

    Parameters
    ----------
    table
        The table being indexed.
    columns
        The index's columns, in order.

    Returns
    -------
    str
        `ix_<table>_<col>_<col>`, shortened from the left if it would
        exceed Postgres' identifier limit.
    """
    name = f"ix_{table.name}_{'_'.join(columns)}"
    if len(name) <= _MAX_IDENTIFIER_LENGTH:
        return name
    # Keep the (distinguishing) tail rather than the (repetitive) head.
    return name[-_MAX_IDENTIFIER_LENGTH:].lstrip("_")


def ensure_foreign_key_indexes(metadata: MetaData, *, schema: str | None = None) -> list[Index]:
    """Add the missing index behind every foreign key in `metadata`.

    Idempotent, and safe to call more than once: anything already covered
    by an existing index, unique constraint, or primary key is skipped.

    Parameters
    ----------
    metadata
        The declarative metadata to walk, normally `db.base.Base.metadata`.
    schema
        Restrict to one schema. `None` (the default) covers all of them.

    Returns
    -------
    list[Index]
        The indexes that were created, for logging or assertion.
    """
    created: list[Index] = []
    for table in metadata.tables.values():
        if schema is not None and (table.schema or "public") != schema:
            continue
        covered = _existing_leading_columns(table)
        is_tenant_table = OWNER_COLUMN in table.columns
        for column in table.columns:
            if not column.foreign_keys:
                continue
            if column.computed is not None:
                # A stored generated column only ever exists here to pin the
                # constant side of a composite foreign key (the `parent_depth`
                # columns behind the two-level tree guard, see
                # `accounting.db.core.Category`). Every row holds the same
                # value, so an index led by it is pure write cost — the
                # selectivity lives in the composite's other column, which
                # gets its own index on the pass below.
                continue
            if column.name != OWNER_COLUMN and _points_only_at_reference_data(column, metadata):
                # A reference into a shared dimension table — no cascade to
                # serve and no read that filters on it. See this module's
                # docstring, and `db.tenant.is_reference_table`.
                continue
            wanted: tuple[str, ...]
            if column.name == OWNER_COLUMN:
                # The tenant key itself. RLS filters on it, so it needs a
                # leading index; most tables already have one via
                # `uq_*(user_id, natural_key)`.
                wanted = (OWNER_COLUMN,)
            elif is_tenant_table:
                # Foreign key first: see this module's docstring on why this
                # order serves both the cascade and the RLS-scoped read.
                wanted = (column.name, OWNER_COLUMN)
            else:
                wanted = (column.name,)
            if wanted in covered:
                continue
            index = Index(_index_name(table, wanted), *(table.columns[name] for name in wanted))
            created.append(index)
            covered.add(wanted)
    return created
