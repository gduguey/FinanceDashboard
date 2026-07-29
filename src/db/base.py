"""The declarative base every ORM model in this repo builds on.

`accounting.db.models` and `trades.db.models` both import this one `Base`
rather than each declaring their own — that's what lets a single Alembic
environment autogenerate migrations across every table in the project in
one pass (see `migration/env.py`), even though each module's tables live in
their own Postgres schema and never foreign-key into each other directly.
"""

from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003 — SQLAlchemy resolves Mapped[...] at runtime
from decimal import Decimal
from typing import TYPE_CHECKING, override

from sqlalchemy import DateTime, MetaData, Numeric, TypeDecorator, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from typing import Any

    from sqlalchemy.engine.interfaces import Dialect
    from sqlalchemy.orm import Session

MONEY = Numeric(18, 4, asdecimal=True)
"""The column type for every money amount in this schema.

`asdecimal=True` is the whole point: psycopg hands back an exact
`decimal.Decimal`, so the exactness Postgres already maintains in its own
`NUMERIC(18, 4)` representation survives being read into Python instead of
being discarded at the driver boundary. Every ORM column using this type is
annotated `Mapped[Decimal]` to match. See `db.money` for the scale and
rounding policy that goes with it.
"""

SHARES = Numeric(20, 8, asdecimal=True)
"""Like `MONEY`, but for fractional share counts, which need more decimal places."""

RATE = Numeric(12, 6)
"""The column type for every rate or percentage.

`NUMERIC`, never `double precision`: a rate is multiplied into a money
amount, so a float rate reintroduces exactly the drift `MONEY` exists to
prevent. Six decimal places — wider than money, see `db.money.RATE_SCALE`.
"""


class RateMap(TypeDecorator[dict[str, Decimal]]):
    """A JSONB map of name to exact rate, e.g. a target allocation per symbol.

    JSON has no decimal type, and psycopg refuses to serialize a `Decimal`
    outright rather than silently narrowing it. Storing the values as
    strings is therefore the *exact* representation, not a workaround —
    `"33.333333"` round-trips to the same `Decimal` it went in as, where a
    JSON number would land on the nearest double.

    This is only for a genuine map of rates whose keys are open-ended
    (symbols the user picks). A fixed, known set of rates should be real
    `RATE` columns instead — see the DB audit's D11.
    """

    impl = JSONB
    cache_ok = True

    @override
    def process_bind_param(self, value: dict[str, Decimal] | None, dialect: Dialect) -> dict[str, str] | None:
        """Serialize each rate to its exact decimal string.

        Parameters
        ----------
        value
            The map on its way to Postgres, or `None`.
        dialect
            Unused; required by the `TypeDecorator` interface.

        Returns
        -------
        dict[str, str] or None
        """
        del dialect
        if value is None:
            return None
        return {key: str(rate) for key, rate in value.items()}

    @override
    def process_result_value(self, value: dict[str, str] | None, dialect: Dialect) -> dict[str, Decimal] | None:
        """Parse each stored string back into an exact `Decimal`.

        Parameters
        ----------
        value
            The map as read from Postgres, or `None`.
        dialect
            Unused; required by the `TypeDecorator` interface.

        Returns
        -------
        dict[str, Decimal] or None
        """
        del dialect
        if value is None:
            return None
        return {key: Decimal(str(rate)) for key, rate in value.items()}


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


class Timestamped:
    """Mixin giving a table the `created_at`/`updated_at` pair every table should have.

    The field guide's rule is "timestamp every row from day one" precisely
    because creation time cannot be backfilled — once a row exists without
    one, that fact is gone. Only six of ~39 tables had `created_at` and
    exactly one had `updated_at` (DB-audit D13), so debugging "when did
    this change?" was usually impossible.

    Both are `TIMESTAMPTZ` and both are filled by the *database*, not by
    Python: `server_default=now()` and `onupdate=now()` mean a row written
    by a migration, a bulk statement, or `psql` is timestamped the same way
    one written through the ORM is, and every timestamp comes from a single
    clock rather than from whichever machine happened to run the code.
    """

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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


def upsert_and_prune(
    session: Session,
    model: Any,  # noqa: ANN401 — generic helper shared across every model with an id/natural_key/user_id shape
    user_id: uuid.UUID,
    rows: Iterable[Base],
    keep_natural_keys: set[str],
) -> None:
    """Insert-or-update every one of `rows`, then delete this user's rows of `model` not in `keep_natural_keys`.

    The write technique for any table something else foreign-keys into —
    `accounts`, `categories`, `tags` (see
    `accounting.repositories.accounts`/`taxonomy`), all three referenced by
    the ledger's own `postings`/`posting_tags`. Blindly deleting and
    reinserting one of these would mean, for one instant mid-transaction, a
    category a real posting still points at doesn't exist; Postgres rejects
    that outright. This instead leaves every still-wanted row in place and
    deletes only the ones genuinely gone, so a delete that *would* orphan
    real history fails loudly on the foreign key instead of silently
    dropping it.

    `session.merge()` (not `add()`) is what makes this an upsert rather
    than a duplicate-key error on a row that already exists — matching on
    `id`, which `derive_id` makes stable across calls for the same natural
    key.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    model
        The ORM model being written, e.g. `accounting.db.core.Account`.
    user_id
        Whose rows these are.
    rows
        The ORM rows to insert-or-update.
    keep_natural_keys
        Every natural key that should survive — anything else this user
        owns in `model` is deleted. Not necessarily the same set as
        `rows`' own keys: a multi-pass caller (see
        `accounting.repositories.accounts.replace_accounts`) passes the
        complete desired set on every pass while upserting only part of it.
    """
    for row in rows:
        session.merge(row)
    session.flush()
    existing_natural_keys = {existing.natural_key for existing in session.query(model).filter_by(user_id=user_id)}
    removed_natural_keys = existing_natural_keys - keep_natural_keys
    if removed_natural_keys:
        session.query(model).filter_by(user_id=user_id).filter(model.natural_key.in_(removed_natural_keys)).delete(
            synchronize_session=False
        )


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


def check_and_bump_row_version(
    session: Session, table: str, row_id: uuid.UUID, user_id: uuid.UUID, expected_version: int | None
) -> int | None:
    """Atomically verify one row's version still matches `expected_version`, then bump it by one.

    The per-row counterpart to `check_and_bump_version`: that one guards a
    single per-user singleton counter row, this guards one row of an
    ordinary table that already carries its own `id` and `version` columns
    (e.g. `accounting.db.automation.TransferRule`). Same atomic
    check-and-bump-in-one-statement reasoning applies — a read-then-write
    here would leave the same race window.

    Parameters
    ----------
    session
        An open database session.
    table
        The fully-qualified `schema.table_name` the row lives in.
    row_id
        Which row to update.
    user_id
        Whose row this is — scopes the update so one user can never bump
        another's row by guessing its id.
    expected_version
        The version the caller last saw, or `None` to skip the check and
        bump unconditionally — the same `None`-skips-the-check contract
        `check_and_bump_version` has, used for an idempotent
        last-write-wins field (a boolean toggle) where losing the race
        against a newer write of the same field is exactly the wanted
        outcome, not a conflict (see
        `docs/app-stack/optimistic-concurrency-versioning.md`).

    Returns
    -------
    int | None
        The newly-bumped version, or `None` if no row with `row_id` (and
        `user_id`) exists at all — the caller distinguishes that from a
        version mismatch (`VersionConflictError`) since they map to
        different HTTP statuses (404 vs 409).

    Raises
    ------
    VersionConflictError
        If `expected_version` was given and the row exists but its stored
        version no longer matches it.
    """
    result = session.execute(
        text(
            f"""
            UPDATE {table}
            SET version = version + 1
            WHERE id = :row_id AND user_id = :user_id
               AND (CAST(:expected_version AS INTEGER) IS NULL OR version = CAST(:expected_version AS INTEGER))
            RETURNING version
            """  # noqa: S608 (table is a fixed internal constant, never user input)
        ),
        {"row_id": str(row_id), "user_id": str(user_id), "expected_version": expected_version},
    )
    row = result.first()
    if row is not None:
        return row.version
    current = session.execute(
        text(f"SELECT version FROM {table} WHERE id = :row_id AND user_id = :user_id"),  # noqa: S608 (see above)
        {"row_id": str(row_id), "user_id": str(user_id)},
    ).scalar_one_or_none()
    if current is None:
        return None
    message = (
        f"This record changed elsewhere since version {expected_version} was loaded (now at version {current}) "
        "— reload before saving again."
    )
    raise VersionConflictError(message)
