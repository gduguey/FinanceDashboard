"""The declarative base every ORM model in this repo builds on.

`accounting.db.models` and `trades.db.models` both import this one `Base`
rather than each declaring their own — that's what lets a single Alembic
environment autogenerate migrations across every table in the project in
one pass (see `migration/env.py`), even though each module's tables live in
their own Postgres schema and never foreign-key into each other directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import MetaData, Numeric
from sqlalchemy.orm import DeclarativeBase

if TYPE_CHECKING:
    from collections.abc import Sequence

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
