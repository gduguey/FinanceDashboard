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

from sqlalchemy import (
    ARRAY,
    DDL,
    DateTime,
    MetaData,
    Numeric,
    Text,
    TypeDecorator,
    any_,
    event,
    func,
    literal,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from typing import Any

    from sqlalchemy import ColumnElement
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


UUID7_FUNCTION_NAME = "public.uuid7"
"""The time-ordered UUID generator every primary key in this schema defaults to.

Schema-qualified deliberately. A `DEFAULT` expression is resolved once, when
the table is created, and stored as a reference to that exact function — so
naming the schema here means no table's default can ever be re-pointed by a
`search_path` difference between the migration role and `app_runtime`.

Lives in `public` rather than in `accounting`/`trades` because both schemas'
tables default to it, and because `DROP SCHEMA accounting CASCADE` (which
`tests/conftest.py` runs between suites) must not take it with them.
"""

_CREATE_UUID7_SQL = f"""
CREATE OR REPLACE FUNCTION {UUID7_FUNCTION_NAME}() RETURNS uuid AS $$
DECLARE
    unix_ts_ms bytea;
    uuid_bytes bytea;
BEGIN
    unix_ts_ms = substring(int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3);
    uuid_bytes = uuid_send(gen_random_uuid());
    uuid_bytes = overlay(uuid_bytes PLACING unix_ts_ms FROM 1 FOR 6);
    uuid_bytes = set_byte(uuid_bytes, 6, (get_byte(uuid_bytes, 6) & 15) | 112);
    uuid_bytes = set_byte(uuid_bytes, 8, (get_byte(uuid_bytes, 8) & 63) | 128);
    RETURN encode(uuid_bytes, 'hex')::uuid;
END
$$ LANGUAGE plpgsql VOLATILE;
"""
"""RFC 9562 version 7, laid out byte by byte so the bit positions are checkable against the spec.

Postgres only grew a built-in `uuidv7()` in 18; this project runs 16 (both
locally and in `deploy/docker-compose.yml`'s `postgres:16-alpine`), so the
function is defined here instead. The layout, in the 16 bytes RFC 9562 §5.7
specifies:

- **bytes 0-5** — `unix_ts_ms`, the 48-bit big-endian count of milliseconds
  since the Unix epoch. `int8send` renders the `bigint` as 8 big-endian
  bytes and `substring(... FROM 3)` drops the two high ones, which are zero
  for every timestamp until the year 10889. This prefix is the whole point
  of the exercise: it makes a fresh id sort *after* every id minted before
  it, so inserts land at the right-hand edge of the B-tree instead of
  scattered through it (DB-audit D3).
- **byte 6, high nibble** — the version, `0111`. `& 15` keeps the low
  nibble's random bits, `| 112` (`0x70`) writes the version over the high
  ones.
- **byte 8, top two bits** — the variant, `10`. `& 63` keeps the low six
  random bits, `| 128` (`0x80`) writes the variant over the top two.
- **everything else** — random, taken from `gen_random_uuid()`. Every bit
  this function preserves from it (byte 6's low nibble, byte 7, byte 8's low
  six bits, bytes 9-15) is a random bit in a v4 UUID, so `rand_a` and
  `rand_b` are fully random; only the two bytes carrying version and variant
  are overwritten, and only in the bits that hold them. `gen_random_uuid()`
  is core Postgres from 13 onward, so this needs no extension.

`clock_timestamp()`, not `now()`: `now()` is fixed at transaction start, so
every row a bulk import inserts would share one timestamp and sort
arbitrarily among itself. Ordering *within* a millisecond is still
arbitrary — `rand_a` is random here rather than a counter, which RFC 9562
permits — so this guarantees ordering across milliseconds, which is the
locality property that matters.
"""

UUID7_STATEMENTS: tuple[str, ...] = (_CREATE_UUID7_SQL,)
"""Every statement that installs `uuid7()`, in order.

Executed from two places against one definition, exactly as
`accounting.db.triggers.ZERO_SUM_STATEMENTS` is: the baseline migration (the
real database) and the `before_create` hook below (the test suite's
`Base.metadata.create_all`, which knows nothing about functions). Without
the hook, every table's `DEFAULT public.uuid7()` would fail to create in a
test database, since Postgres resolves the function when the table is made.

`before_create` on the *metadata* rather than `after_create` on a table, for
that same reason — the function has to exist before the first `CREATE TABLE`
references it, not after.
"""

UUID7_DROP_STATEMENTS: tuple[str, ...] = (f"DROP FUNCTION IF EXISTS {UUID7_FUNCTION_NAME}()",)
"""The reverse of `UUID7_STATEMENTS`, for the baseline migration's `downgrade`.

Runs *after* the tables are dropped: while any table still has a
`DEFAULT public.uuid7()`, Postgres refuses to drop the function it depends on.
"""

for _uuid7_statement in UUID7_STATEMENTS:
    event.listen(Base.metadata, "before_create", DDL(_uuid7_statement))

UUID7_DEFAULT = text(f"{UUID7_FUNCTION_NAME}()")
"""The `server_default` every UUID primary-key column in this repo is declared with.

Server-side, not a Python `default=`, for the same reason `Timestamped`'s
columns are: a row written by a migration, a bulk statement, or `psql` gets
a well-formed time-ordered id the same way one written through the ORM does,
and the id comes from the database's clock rather than from whichever machine
happened to run the code. SQLAlchemy reads the generated value back via
`INSERT ... RETURNING id`, so `row.id` is populated after `session.flush()`
exactly as a client-side default would have left it.
"""


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


def any_uuid(column: Any, values: Iterable[uuid.UUID]) -> ColumnElement[bool]:  # noqa: ANN401 — any UUID column
    """Match `column` against a set of ids as **one** array parameter, not one parameter per id.

    Use this, never `column.in_(ids)`, whenever the list can grow with a
    user's data. `IN (...)` renders one bind parameter per element, and
    Postgres's wire protocol accepts at most **65,535** of them in a single
    statement — past that the driver raises `number of parameters must be
    between 0 and 65535` and the request 500s. That was a measured
    availability cliff, not a theoretical one: a user with 65,535 posting
    overrides took down `GET /postings` and every dashboard endpoint (DB-audit
    D4, speed-audit S2), because `natural_keys_by_id` below resolves one id
    per override and nothing deduplicated them down.

    `= ANY(:array)` binds the whole list as a single array value instead, so
    there is no ceiling to hit and no chunking loop to get wrong. It is also
    the shape Postgres plans identically to `IN` for index lookups, so this
    costs nothing.

    Parameters
    ----------
    column
        The UUID column to match, e.g. `Posting.id`.
    values
        The ids to match against. Deduplicated; order is irrelevant to `ANY`.

    Returns
    -------
    sqlalchemy.ColumnElement
        A boolean expression for a `WHERE`/`ON` clause.
    """
    return column == any_(literal(sorted(set(values)), ARRAY(PG_UUID(as_uuid=True))))


def any_text(column: Any, values: Iterable[str]) -> ColumnElement[bool]:  # noqa: ANN401 — any text column
    """Match `column` against a set of strings as one array parameter — `any_uuid` for text columns.

    Read `any_uuid` for why every unbounded list in this repo goes through
    one of these two rather than through `in_`.

    Parameters
    ----------
    column
        The text column to match, e.g. `Posting.natural_key`.
    values
        The values to match against. Deduplicated.

    Returns
    -------
    sqlalchemy.ColumnElement
        A boolean expression for a `WHERE`/`ON` clause.
    """
    return column == any_(literal(sorted(set(values)), ARRAY(Text)))


def natural_keys_by_id(
    session: Session,
    model: Any,  # noqa: ANN401 — generic helper shared across every model with an id/natural_key/user_id shape
    user_id: uuid.UUID,
    ids: Iterable[uuid.UUID | None],
) -> dict[uuid.UUID, str]:
    """Batch-resolve `model.id -> model.natural_key` for a set of ids — reversing a stored FK back to its natural key.

    This function and its inverse `ids_by_natural_key` are **the only place
    in this repo where an `id` and a `natural_key` meet**. Everything above
    the persistence boundary — every pydantic model, every API path, every
    domain function — addresses a row by its natural key *string*; every
    stored foreign key holds the opaque `id`. Nothing computes one from the
    other, because nothing can: an `id` is minted by the database
    (`UUID7_DEFAULT`) and is knowable only by asking. Keeping
    both directions of the translation here, batched into one query each, is
    what stops that lookup from being re-invented per-caller or degenerating
    into one round trip per row.

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
        session.query(model.id, model.natural_key).filter(model.user_id == user_id, any_uuid(model.id, id_list)).all()
    )
    return dict(rows)


class UnknownNaturalKeyError(KeyError):
    """A caller named a natural key that has no row — a reference nothing could satisfy.

    Raised by subscripting `ids_by_natural_key`'s result (see its docstring
    for why subscripting, not `.get`, is the convention for a reference).

    This is the failure a foreign key used to raise as an `IntegrityError`,
    back when a bad reference could be turned into a *made-up id* and sent to
    Postgres to be rejected there. Nothing can invent an id any more, so the
    rejection moves up to the lookup — and gains the one thing the
    `IntegrityError` had over a bare `KeyError`: a message naming both the
    table and the key that isn't in it.

    A `KeyError` subclass so that subscripting a mapping still raises what
    subscripting a mapping is expected to raise. Like `IntegrityError` before
    it, it is caught nowhere: a reference to something that does not exist is
    a bug in the caller, not a condition to recover from.
    """


class _NaturalKeyIds(dict[str, uuid.UUID]):  # noqa: FURB189 — `__missing__` is the reason to subclass `dict`
    """`ids_by_natural_key`'s result: a plain mapping whose *missing* key is a named error, not a bare `KeyError`."""

    def __init__(self, rows: Iterable[tuple[str, uuid.UUID]], table: str) -> None:
        super().__init__(rows)
        self._table = table

    def __missing__(self, key: str) -> uuid.UUID:
        """Raise `UnknownNaturalKeyError` naming the table and the key.

        Only `__getitem__` consults this, which is what keeps `.get(key)`
        and `key in ids` as the two ways to ask whether a row exists without
        asserting that it must.

        Raises
        ------
        UnknownNaturalKeyError
            Always.
        """
        message = f"{self._table} has no row with natural key {key!r} for this user"
        raise UnknownNaturalKeyError(message)


def ids_by_natural_key(
    session: Session,
    model: Any,  # noqa: ANN401 — generic helper shared across every model with an id/natural_key/user_id shape
    user_id: uuid.UUID,
    natural_keys: Iterable[str | None],
) -> dict[str, uuid.UUID]:
    """Batch-resolve `model.natural_key -> model.id` — the forward half of the seam `natural_keys_by_id` documents.

    What a write needs: the caller holds the natural key a pydantic model
    carries and has to store the `id` a foreign key column wants. Read that
    function's docstring for why this translation exists at all and why both
    directions live in this one module.

    A natural key with no row is simply **absent** from the result rather
    than an error, because the two callers that happens for want opposite
    things. Hence the convention every caller in this repo follows:

    - **Subscript (`ids[key]`) when the caller holds a natural key**, so a
      key naming no row raises `UnknownNaturalKeyError` rather than resolving
      to `None`. This is what keeps a bad reference as loud as it was when the
      id was derived rather than looked up: back then the made-up id reached
      Postgres and the foreign key rejected it. Silently storing `NULL` into a
      nullable column instead — a subcategory quietly becoming uncategorized,
      a child account quietly becoming a root — would be strictly worse than
      either.
    - **`.get(key)` only where the *absence itself* is the answer**, i.e.
      "does this row exist yet, and so is this an insert or an update" — see
      `merge_by_natural_key`.

    A `None` in `natural_keys` is not a missing row, it is the caller having
    no reference to resolve; those are dropped, and the caller writes `None`
    into the column without consulting this map at all.

    Parameters
    ----------
    session
        An open database session.
    model
        The ORM model to look up, e.g. `accounting.db.core.Account`.
    user_id
        Whose rows to look up.
    natural_keys
        The keys to resolve; `None` entries are ignored. Deduplicated
        before the query, so a caller may pass one key per row it holds.

    Returns
    -------
    dict[str, uuid.UUID]
        Empty if `natural_keys` has no non-`None` entries. Subscripting a key
        that isn't there raises `UnknownNaturalKeyError`.
    """
    table = str(model.__table__)
    key_list = {natural_key for natural_key in natural_keys if natural_key is not None}
    if not key_list:
        return _NaturalKeyIds((), table)
    rows: list[tuple[str, uuid.UUID]] = (
        session
        .query(model.natural_key, model.id)
        .filter(model.user_id == user_id, any_text(model.natural_key, key_list))
        .all()
    )
    return _NaturalKeyIds(rows, table)


NATURAL_KEY_MERGE_ATTEMPTS = 4
"""How many times `merge_by_natural_key` may flush one batch before it gives up.

A single lost race needs exactly one retry: the writer that won has
*committed* by the time the loser's `INSERT` is rejected, so the loser's
re-lookup finds the winner's id and the second attempt is an `UPDATE` that
cannot conflict with anything. The budget is bigger than one only because a
batch can lose to a *different* writer on each pass — three imports starting
at the same instant, each inserting a different new key of the same batch —
and each of those is a genuinely new conflict rather than the same one
repeating.

Four rather than "retry until it works", because `merge_by_natural_key`
only spends an attempt when the re-lookup *proves* the batch moved forward
(some natural key that had no row now has one). Anything that survives four
attempts is therefore not contention resolving slowly, it is a batch racing
a writer that keeps inventing new rows underneath it — a pathological
situation, and one a request should surface as a named failure rather than
disappear into. Hence `ConcurrentNaturalKeyInsertError` when it runs out,
never a silent partial write and never an unbounded loop holding a
connection open.
"""


class ConcurrentNaturalKeyInsertError(RuntimeError):
    """`merge_by_natural_key` kept losing the insert race, `NATURAL_KEY_MERGE_ATTEMPTS` times over.

    Not the ordinary lost race — that one is retried and succeeds, and no
    caller ever hears about it. This is the bound being exhausted: every
    attempt collided with a *newly committed* row for one of this batch's
    own natural keys, which no realistic amount of concurrency produces.

    Deliberately unmapped to an HTTP status, unlike `VersionConflictError`'s
    409. A version conflict is a normal thing for two browser tabs to do and
    the user can act on it ("reload before saving again"); this is not
    something the caller did or can fix, so it stays a 500 and shows up in
    the logs naming the table and the batch involved.
    """


def merge_by_natural_key(
    session: Session,
    model: Any,  # noqa: ANN401 — generic helper shared across every model with an id/natural_key/user_id shape
    user_id: uuid.UUID,
    rows: Iterable[Any],
) -> dict[str, uuid.UUID]:
    """Insert-or-update every one of `rows`, matching an existing row on `(user_id, natural_key)`.

    The upsert primitive for every natural-keyed table written through the
    ORM. `session.merge()` matches on the **primary key**, and a primary key
    is now minted by the database rather than derived from the natural key
    (`UUID7_DEFAULT`) — so a caller holding only a natural key
    has nothing to hand it. This resolves the real key to its id first, in
    one query for the whole batch, and hands `merge()` what it needs:

    - a natural key that already has a row gets that row's `id`, so
      `merge()` updates it in place;
    - one that doesn't gets `id = None`, so `merge()` inserts and the
      column's server default mints a fresh time-ordered id.

    Deliberately the ORM's `merge()` rather than a raw
    `INSERT ... ON CONFLICT`: `accounts` and `categories` carry
    `GENERATED ALWAYS AS ... STORED` columns (`depth`/`parent_depth`) that no
    statement may write, and both tables' `updated_at` is maintained by the
    mapper's `onupdate`. Going through the mapper keeps both facts in one
    place instead of restating them in SQL here. It is also what lets this
    hand back live ORM instances' ids, which `ON CONFLICT ... RETURNING`
    would not.

    **The lookup and the flush are two statements, so they race.** Two
    requests upserting the same *new* `(user_id, natural_key)` both resolve
    it to "no row yet", both `merge()` a transient row, and the one that
    flushes second is rejected by `UNIQUE (user_id, natural_key)` — a 500 on
    a write that should simply have become an update. Never corruption (the
    constraint is doing its job), but never acceptable either: it is exactly
    the concurrency an import running while the person edits the same
    account produces.

    So the flush — and **only** the flush — runs inside a SAVEPOINT
    (`session.begin_nested()`). When the `IntegrityError` lands, rolling back
    to that savepoint undoes this function's own merges and nothing else; the
    natural keys are resolved again (the winner has committed, so its id is
    visible now — every statement reads a fresh snapshot under READ
    COMMITTED, Postgres's default and this application's); and the batch is
    merged a second time against the id the winner just created. What was an
    insert becomes the update it always meant to be.

    A savepoint rather than letting the caller retry the whole transaction,
    because five of this function's six call sites are already inside a
    larger unit of work — `replace_accounts` has flushed its parent accounts,
    `trades.brokers.ibkr.main._write_ledger` has deleted the old events — and
    a plain retry loop would have to throw that away and change what a
    failure here means for every one of them. A savepoint changes nothing
    outside these few statements: the caller's earlier writes are still
    there, still uncommitted, still theirs to commit.

    Not every `IntegrityError` is this race, and the ones that aren't must
    stay as loud as they were: a real foreign-key or check violation is a bug
    that retrying would only bury under `NATURAL_KEY_MERGE_ATTEMPTS`
    identical failures. So the re-lookup doubles as the test of *which*
    failure this was — if no natural key that had no row now has one, nothing
    changed underneath us, a retry would fail identically, and the original
    error is re-raised untouched. That is a sturdier check than matching on
    the constraint's name or on a SQLSTATE, and it needs no per-table
    knowledge here.

    Parameters
    ----------
    session
        An open database session; flushed here, committed by the caller.
    model
        The ORM model being written, e.g. `accounting.db.core.Account`.
    user_id
        Whose rows these are.
    rows
        The ORM rows to insert-or-update. Each must carry a `natural_key`;
        its `id` is overwritten from the lookup, so a caller never sets one.

    Returns
    -------
    dict[str, uuid.UUID]
        Every written row's natural key mapped to its `id` — including the
        freshly minted ones, read back by the flush. This is what lets a
        two-pass caller (see
        `accounting.repositories.accounts.replace_accounts`) point a child
        row's `parent_*_id` at a parent this call just inserted.

    Raises
    ------
    sqlalchemy.exc.IntegrityError
        Re-raised untouched whenever the flush's failure was *not* a lost
        natural-key race — a foreign key, a check constraint, a different
        unique index. Retrying those would only repeat them, so they stay as
        loud here as they were before this function retried anything.
    ConcurrentNaturalKeyInsertError
        If every one of `NATURAL_KEY_MERGE_ATTEMPTS` flushes collided with a
        newly committed row for one of this batch's own natural keys. See
        that constant for why exhausting it means something pathological
        rather than something merely busy.
    """
    rows = list(rows)
    if not rows:
        return {}
    natural_keys = [row.natural_key for row in rows]
    # Outside the savepoint on purpose. This query's autoflush is what writes
    # out whatever the *caller* still had pending, and that work must land in
    # the enclosing transaction rather than inside a savepoint this function
    # may later roll back out from under it.
    existing_ids = ids_by_natural_key(session, model, user_id, natural_keys)
    for _attempt in range(NATURAL_KEY_MERGE_ATTEMPTS):
        try:
            with session.begin_nested():
                merged: list[Any] = []
                for row in rows:
                    row.id = existing_ids.get(row.natural_key)
                    merged.append(session.merge(row))
                session.flush()
        except IntegrityError:
            # The savepoint is already rolled back — SQLAlchemy unwinds it as
            # the failed flush propagates — so the session is usable again and
            # this SELECT sees whatever the winner committed.
            resolved_ids = ids_by_natural_key(session, model, user_id, natural_keys)
            if not set(resolved_ids) - set(existing_ids):
                raise
            existing_ids = resolved_ids
        else:
            return {row.natural_key: row.id for row in merged}
    message = (
        f"{model.__table__} lost the insert race on (user_id, natural_key) {NATURAL_KEY_MERGE_ATTEMPTS} times running "
        f"for a batch of {len(rows)} rows (first key {natural_keys[0]!r}) — something is committing these keys "
        "faster than this batch can merge against them."
    )
    raise ConcurrentNaturalKeyInsertError(message)


def ensure_reference_rows(
    session: Session,
    model: Any,  # noqa: ANN401 — generic helper shared across every single-column reference table
    keys: Iterable[str | None],
) -> None:
    """Create whichever of `keys` has no row yet in a shared reference table — create-or-reference, never fail.

    The write-path counterpart of `db.tenant.is_reference_table`, and the
    reason two of the three dimension tables need no seed data at all.
    `accounting.institutions` and `trades.securities` hold *open*
    vocabularies: the user types the name of their credit union, and the
    IBKR sync brings back whatever symbols they actually traded. Neither
    list can be enumerated ahead of time, so the reference has to come into
    existence as a side effect of the row that names it — otherwise adding a
    real foreign key would turn "import a statement mentioning a new ticker"
    into an `IntegrityError`.

    Deliberately not `merge_by_natural_key`: that resolves a natural key to a
    database-minted `id` because a tenant row has both. A reference row has
    only its key, which *is* its primary key (see `db.models.Currency`'s
    docstring on why), so this is one `INSERT ... ON CONFLICT DO NOTHING`
    over the whole batch — no lookup, no round trip per key, and safe under
    two concurrent imports naming the same new symbol.

    The rows come from `unnest` over one array parameter rather than from an
    explicit multi-row `VALUES`. An explicit `.values([...])` list binds one
    parameter per key, which is the 65,535-parameter ceiling `any_uuid`
    documents — and unlike an ORM `add_all`, SQLAlchemy's `insertmanyvalues`
    batching does not rescue a statement that was written out by hand.

    `ON CONFLICT DO NOTHING` rather than `DO UPDATE`: the key is the entire
    row's content, so there is nothing an existing row could be missing.

    Parameters
    ----------
    session
        An open database session; flushed here, committed by the caller.
    model
        The reference model to fill, e.g. `trades.db.models.Security`. Must
        have a single-column primary key.
    keys
        The keys that must exist. `None` and empty strings are dropped — a
        caller with a nullable reference (`dashboard_settings.benchmark_symbol_override`)
        passes it straight through rather than testing it first.
    """
    wanted = sorted({key for key in keys if key})
    if not wanted:
        return
    (key_column,) = model.__table__.primary_key.columns
    session.execute(
        pg_insert(model.__table__)
        .from_select(
            [key_column.name],
            select(func.unnest(literal(wanted, ARRAY(Text))).label(key_column.name)),
        )
        .on_conflict_do_nothing(index_elements=[key_column.name])
    )
    session.flush()


def upsert_and_prune(
    session: Session,
    model: Any,  # noqa: ANN401 — generic helper shared across every model with an id/natural_key/user_id shape
    user_id: uuid.UUID,
    rows: Iterable[Any],
    keep_natural_keys: set[str],
) -> dict[str, uuid.UUID]:
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

    See `merge_by_natural_key` for the upsert half, and why matching on
    `(user_id, natural_key)` — the table's real key — is what makes this an
    upsert rather than a duplicate-key error.

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

    Returns
    -------
    dict[str, uuid.UUID]
        `merge_by_natural_key`'s own result — every written row's natural
        key mapped to its `id`.
    """
    written_ids = merge_by_natural_key(session, model, user_id, rows)
    existing_natural_keys = {existing.natural_key for existing in session.query(model).filter_by(user_id=user_id)}
    removed_natural_keys = existing_natural_keys - keep_natural_keys
    if removed_natural_keys:
        session.query(model).filter_by(user_id=user_id).filter(
            any_text(model.natural_key, removed_natural_keys)
        ).delete(synchronize_session=False)
    return written_ids


class VersionConflictError(Exception):
    """Raised by `check_and_bump_row_version` when a caller's remembered version no longer matches what's persisted.

    Means someone else's save — another browser tab, another device, or
    just an earlier request from the same tab — landed since the caller
    last loaded that row. Mapped to an HTTP 409 by one global exception
    handler (`trades.api.api`), shared across every table that uses this
    module, not caught anywhere it is actually raised from.
    """


def check_and_bump_row_version(
    session: Session, table: str, row_id: uuid.UUID, user_id: uuid.UUID, expected_version: int | None
) -> int | None:
    """Atomically verify one row's version still matches `expected_version`, then bump it by one.

    The one optimistic-concurrency mechanism in this repo. It guards a
    single row of an ordinary table that carries its own `id` and
    `version` columns (`accounting.goals`,
    `accounting.transfer_rules`, `accounting.category_patterns` — see
    `accounting.db.automation.TransferRule`). A whole-store/whole-settings
    twin used to sit alongside it, one counter per user covering every
    table at once; that granularity is exactly what made two genuinely
    unrelated edits conflict, and it is gone.

    The check-and-bump happens as one atomic SQL statement, not a
    separate read then write — a read-then-write would leave a race
    window where two concurrent saves both read the same "current"
    version and both proceed.

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
        bump unconditionally — used for an idempotent
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
