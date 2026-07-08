"""SQLAlchemy ORM models for the `trades` Postgres schema.

Importing this package registers every trades table on `db.base.Base`'s
shared metadata — see `accounting/db/__init__.py`'s docstring for why that
matters to Alembic and to test fixtures.
"""

from __future__ import annotations

from trades.db.models import BrokerConnection, LedgerEvent

__all__ = ["BrokerConnection", "LedgerEvent"]
