"""The declarative base every ORM model in this repo builds on.

`accounting.db.models` and `trades.db.models` both import this one `Base`
rather than each declaring their own — that's what lets a single Alembic
environment autogenerate migrations across every table in the project in
one pass (see `migration/env.py`), even though each module's tables live in
their own Postgres schema and never foreign-key into each other directly.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import MetaData, Numeric, text
from sqlalchemy.orm import DeclarativeBase

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from typing import Any

    from sqlalchemy.orm import Session

MONEY = Numeric(18, 4, asdecimal=False)
"""The column type for every money amount in this schema.

`asdecimal=False` makes psycopg hand back a Python `float`, matching every
pydantic model's own `amount: float` field — without it, SQLAlchemy's
default is to return `decimal.Decimal`, silently mismatching the
`Mapped[float]` annotation every ORM model here declares. Postgres itself
still stores and computes on the exact `NUMERIC(18, 4)` representation
either way; this only changes what Python type the driver hands back.
"""

SHARES = Numeric(20, 8, asdecimal=False)
"""Like `MONEY`, but for fractional share counts, which need more decimal places."""

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
"""Deterministic names for every constraint Alembic generates.

Without this, Postgres assigns its own auto-generated constraint names,
which differ across environments and make `alembic revision --autogenerate`
produce spurious drop/recreate diffs for constraints that didn't actually
change. See https://alembic.sqlalchemy.org/en/latest/naming.html.
"""


class Base(DeclarativeBase):
    """Shared declarative base; every table's `metadata` lives on this one instance."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def check_in_sql(column: str, values: Sequence[str]) -> str:
    """Build a `CheckConstraint`'s SQL text restricting `column` to `values`.

    Every one of this repo's pydantic `Literal` types (`AccountKind`,
    `CurrencyCode`, ...) already declares its own allowed values once, in
    `models.py`; this lets a table's `CheckConstraint` restate the *same*
    values as a database-level guarantee, via `get_args(SomeLiteral)`,
    rather than retyping the list a second time somewhere in `db/models.py`.

    Parameters
    ----------
    column
        The column name the constraint applies to.
    values
        The allowed values, e.g. from `typing.get_args(SomeLiteralType)`.

    Returns
    -------
    str
        SQL text suitable for `sqlalchemy.CheckConstraint`.
    """
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


_ID_NAMESPACE = uuid.UUID("f47b6a1e-6e21-4f5f-8a2f-9f1a2b3c4d5e")
"""Fixed, arbitrary namespace UUID every `derive_id` call hashes against — never changes."""


def derive_id(user_id: uuid.UUID, table: str, natural_key: str) -> uuid.UUID:
    """Compute a table row's opaque surrogate `id`, deterministically, from its human-meaningful natural key.

    Every table that used to use a human-chosen string (`account_id`,
    `category_id`, a content-addressed import key like
    `f"sofi:{kind}:{last4}"`, ...) directly as its primary key now stores
    that same string as `natural_key` instead, and derives its real `id`
    (a UUID, opaque, never exposed to guess row counts from) from it via
    this function. Two properties this buys, that a random
    `gen_random_uuid()` default couldn't:

    - **Stable across a full delete-and-recreate rewrite.** Several
      tables (see `accounting.store`) are persisted by deleting every row
      for a user and reinserting the current in-memory state from
      scratch on every save — the same natural key always re-derives the
      same `id`, so anything that FK-referenced this row (a posting's
      `category_id`, a category's own `parent_category_id`) keeps
      pointing at a row that still exists after the rewrite, without a
      lookup pass to remap ids.
    - **Idempotent re-import.** A content-addressed natural key (an
      import's own dedup convention) re-derives the same `id` every time
      the same source is re-imported, so "insert, conflict on
      `(user_id, natural_key)`, skip" still works exactly as it did when
      the natural key was the primary key directly.

    Parameters
    ----------
    user_id
        Whose row this is — the same `(user_id, natural_key)` pair a
        table's `UNIQUE` constraint enforces, so two different users'
        otherwise-identical natural keys never collide on the same id.
    table
        The table this id is for, e.g. `"accounts"` — included so the
        same natural key never accidentally derives the same id on two
        different tables.
    natural_key
        The human-meaningful string this row's identity used to be keyed
        on directly, e.g. an `account_id`, a `category_id`, or an
        import's own dedup key.

    Returns
    -------
    uuid.UUID
    """
    return uuid.uuid5(_ID_NAMESPACE, f"{user_id}:{table}:{natural_key}")


def natural_keys_by_id(
    session: Session,
    model: Any,  # noqa: ANN401 — generic helper shared across every model with an id/natural_key/user_id shape
    user_id: uuid.UUID,
    ids: Iterable[uuid.UUID | None],
) -> dict[uuid.UUID, str]:
    """Batch-resolve `model.id -> model.natural_key` for a set of ids — reversing a stored FK back to its natural key.

    Shared by `accounting.store` and `accounting.importers.ingest`
    (and any future module in the same position): a row read back from
    Postgres only ever has a FK column's opaque `id`, never the
    human-meaningful string a pydantic model's own field expects — this
    is the one place that reversal happens, batched into a single query
    rather than one lookup per row.

    Parameters
    ----------
    session
        An open database session.
    model
        The ORM model to look up, e.g. `accounting.db.core.Posting`.
    user_id
        Whose rows to look up.
    ids
        The ids to resolve; `None` entries are ignored.

    Returns
    -------
    dict[uuid.UUID, str]
        Empty if `ids` has no non-`None` entries.
    """
    id_list = {row_id for row_id in ids if row_id is not None}
    if not id_list:
        return {}
    rows: list[tuple[uuid.UUID, str]] = (
        session.query(model.id, model.natural_key).filter(model.user_id == user_id, model.id.in_(id_list)).all()
    )
    return dict(rows)


class VersionConflictError(Exception):
    """Raised by `check_and_bump_version` when a caller's remembered version no longer matches what's persisted.

    Means someone else's save — another browser tab, another device, or
    just an earlier request from the same tab — landed since the caller
    last loaded this data. Mapped to an HTTP 409 by one global exception
    handler (`trades.api.api`), shared across every table that uses this
    module, not caught anywhere any of them are actually raised from.
    """


def get_version(session: Session, table: str, user_id: uuid.UUID) -> int:
    """Read this user's current save-version counter for `table`.

    Parameters
    ----------
    session
        An open database session.
    table
        The fully-qualified `schema.table_name` this counter is for, e.g.
        `"accounting.store_versions"` — a plain two-column `(user_id,
        version)` table with `user_id` as its sole primary key (see
        `accounting.db.concurrency.StoreVersion` for the shape every such
        table follows).
    user_id
        Whose counter to read.

    Returns
    -------
    int
        `0` if this user has never saved anything to `table` yet (no row exists).
    """
    row = session.execute(
        text(f"SELECT version FROM {table} WHERE user_id = :user_id"),  # noqa: S608 (table is a fixed internal constant, never user input)
        {"user_id": str(user_id)},
    ).first()
    return row.version if row is not None else 0


def check_and_bump_version(session: Session, table: str, user_id: uuid.UUID, expected_version: int | None) -> int:
    """Atomically verify no other save has landed since `expected_version`, then bump `table`'s counter by one.

    The check-and-bump happens as one atomic SQL statement (an upsert
    with a conditional `WHERE` on the update branch), not a separate read
    then write — a read-then-write here would leave a race window where
    two concurrent saves could both read the same "current" version and
    both proceed. `expected_version=None` (no version the caller wants
    checked, e.g. an older client) skips the check but still bumps: a
    real change always has to be visible to a version-aware caller later,
    even if this particular caller didn't opt into checking itself.

    Returns the newly-bumped version so a caller that saves more than once
    per request (e.g. `accounting.store._check_and_bump_store_version`) can
    re-stash it as the expected version for its own next call, rather than
    that next call re-checking against the same now-stale value the first
    call already consumed.

    Parameters
    ----------
    session
        An open database session.
    table
        The fully-qualified `schema.table_name` this counter is for (see
        `get_version`).
    user_id
        Whose store this is.
    expected_version
        The version the caller last saw, or `None` to skip the check.

    Returns
    -------
    int
        The version `table` was just bumped to.

    Raises
    ------
    VersionConflictError
        If `expected_version` was given and no longer matches what's
        actually stored.
    """
    result = session.execute(
        text(
            f"""
            INSERT INTO {table} (user_id, version)
            VALUES (:user_id, 1)
            ON CONFLICT (user_id) DO UPDATE
            SET version = {table}.version + 1
            WHERE CAST(:expected_version AS INTEGER) IS NULL
               OR {table}.version = CAST(:expected_version AS INTEGER)
            RETURNING version
            """  # noqa: S608 (table is a fixed internal constant, never user input)
        ),
        {"user_id": str(user_id), "expected_version": expected_version},
    )
    row = result.first()
    if row is None:
        current = get_version(session, table, user_id)
        message = (
            f"This data changed elsewhere since version {expected_version} was loaded (now at version {current}) "
            "— reload before saving again."
        )
        raise VersionConflictError(message)
    return row.version
