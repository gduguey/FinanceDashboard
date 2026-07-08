"""Per-user brokerage connections and the ledger events they produce.

Global, non-user-specific reference data (daily close prices, CPI, HYSA
rates) deliberately stays out of Postgres — see `DATABASE_SCHEMA.md` — it's
identical for every user and stays exactly where it is today, under
`data/trades/{prices,cpi,hysa_rates}/`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import MONEY, SHARES, Base, check_in_sql
from trades.config import LedgerEventType

SCHEMA = "trades"


class BrokerConnection(Base):
    """One user's own link to a brokerage — their own credentials, not a shared process-wide `.env`.

    `credentials_ref` is a pointer (e.g. a key into `db.models.UserSecret`),
    never the raw token itself — this table only records that a connection
    exists and which broker it's for.
    """

    __tablename__ = "broker_connections"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    connection_id: Mapped[str] = mapped_column(primary_key=True)
    broker: Mapped[str]
    external_account_id: Mapped[str | None] = mapped_column(default=None)
    credentials_ref: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class LedgerEvent(Base):
    """One immutable row of one user's transaction ledger — mirrors `trades.models.LedgerEvent`."""

    __tablename__ = "ledger_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "connection_id"],
            [f"{SCHEMA}.broker_connections.user_id", f"{SCHEMA}.broker_connections.connection_id"],
        ),
        CheckConstraint(check_in_sql("event_type", get_args(LedgerEventType)), name="event_type"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[str] = mapped_column(primary_key=True)
    connection_id: Mapped[str]
    event_datetime: Mapped[datetime]
    symbol: Mapped[str]
    event_type: Mapped[str]
    shares: Mapped[float | None] = mapped_column(SHARES, default=None)
    price: Mapped[float | None] = mapped_column(MONEY, default=None)
    amount: Mapped[float] = mapped_column(MONEY)
    currency: Mapped[str]
    meta: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
