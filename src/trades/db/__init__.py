"""SQLAlchemy ORM models for the `trades` Postgres schema.

Importing this package registers every trades table on `db.base.Base`'s
shared metadata — see `accounting/db/__init__.py`'s docstring for why that
matters to Alembic and to test fixtures.
"""

from __future__ import annotations

from db.base import Base
from db.indexes import ensure_foreign_key_indexes
from trades.db.models import (
    BrokerConnection,
    DashboardSettings,
    DashboardSettingsVersion,
    LedgerEvent,
    LedgerEventTradeDetails,
)

__all__ = [
    "BrokerConnection",
    "DashboardSettings",
    "DashboardSettingsVersion",
    "LedgerEvent",
    "LedgerEventTradeDetails",
]


# Every foreign key in this schema gets its `(user_id, <fk>)` index here rather
# than in each model, so adding a foreign key cannot ship without one.
# See `db.indexes` for the reasoning. This is the only module where every
# model in the schema is guaranteed to be loaded, so it is the only place the
# walk can run.
ensure_foreign_key_indexes(Base.metadata, schema="trades")  # noqa: RUF067 — see the comment above
