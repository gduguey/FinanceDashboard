"""Per-user brokerage connections and the ledger events they produce.

Global, non-user-specific reference data (daily close prices, CPI, HYSA
rates) deliberately stays out of Postgres — see `DATABASE_SCHEMA.md` — it's
identical for every user and stays exactly where it is today, under
`data/trades/{prices,cpi,hysa_rates}/`.

Every table's primary key is a surrogate `id` (never a composite
`(user_id, ...)` key), derived deterministically from `db.base.derive_id`
so it stays stable across a full delete-and-recreate rewrite and, for
import-derived rows, makes re-importing the same source idempotent — see
`derive_id`'s own docstring. `natural_key` is the human-meaningful string
(a connection name, an import's own dedup key) that identity used to be
keyed on directly.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import MONEY, RATE, SHARES, Base, RateMap, Timestamped, check_in_sql
from trades.config import LedgerEventType, TaxRegime

SCHEMA = "trades"


class BrokerConnection(Base, Timestamped):
    """One user's own link to a brokerage — their own credentials, not a shared process-wide `.env`.

    Credentials themselves live in `db.models.UserSecret`, keyed by
    `(user_id, "broker:{broker}")` — see `trades.broker_credentials` — so
    this table only ever needs to know a connection exists and which
    broker it's for, never a pointer to where its credentials live.
    """

    __tablename__ = "broker_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "natural_key", name="uq_broker_connections_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    broker: Mapped[str]


class LedgerEvent(Base, Timestamped):
    """One immutable row of one user's transaction ledger — mirrors `trades.models.LedgerEvent`.

    `shares`/`price` live on the side table `LedgerEventTradeDetails`
    instead of nullable columns here — a row there exists only for
    `BUY`/`SELL` events, and if it exists both fields are guaranteed
    present, enforced by the database rather than only by
    `trades.models.LedgerEvent`'s own pydantic validator.
    """

    __tablename__ = "ledger_events"
    __table_args__ = (
        CheckConstraint(check_in_sql("event_type", get_args(LedgerEventType)), name="event_type"),
        UniqueConstraint("user_id", "natural_key", name="uq_ledger_events_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.broker_connections.id", ondelete="CASCADE")
    )
    event_datetime: Mapped[datetime]
    symbol: Mapped[str]
    event_type: Mapped[str]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str]
    meta: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)


class LedgerEventTradeDetails(Base, Timestamped):
    """The share count and per-share price for a `BUY`/`SELL` `LedgerEvent` — never any other event type.

    Carries its own `user_id`, denormalized from the parent `LedgerEvent`,
    even though `ledger_event_id` alone already determines it — an RLS
    policy needs a `user_id` column on *this* table directly, or a direct
    query against it (bypassing a join through `ledger_events`) would
    never be filtered at all.
    """

    __tablename__ = "ledger_event_trade_details"
    __table_args__ = {"schema": SCHEMA}

    ledger_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.ledger_events.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    shares: Mapped[Decimal] = mapped_column(SHARES)
    price: Mapped[Decimal] = mapped_column(MONEY)


class DashboardSettings(Base, Timestamped):
    """One user's dashboard preferences — target allocation, HYSA/benchmark overrides, tax settings, display timezone.

    Exactly zero or one row per user (a singleton preferences record) —
    `user_id` is the primary key directly; there's no natural-key/import
    concept here the way there is for `accounts`/`categories`.

    No `version` column, deliberately: see
    `trades.dashboard.settings.save_settings` for why this one row is
    last-write-wins rather than optimistically version-checked.
    """

    __tablename__ = "dashboard_settings"
    __table_args__ = (
        CheckConstraint(check_in_sql("tax_regime", get_args(TaxRegime)), name="tax_regime"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    target_allocation_pct: Mapped[dict[str, Decimal]] = mapped_column(RateMap, default=dict)
    hysa_bank_id: Mapped[str | None] = mapped_column(default=None)
    hysa_fixed_rate_pct: Mapped[Decimal | None] = mapped_column(RATE, default=None)
    benchmark_symbol_override: Mapped[str | None] = mapped_column(default=None)
    local_zone: Mapped[str | None] = mapped_column(default=None)
    tax_enabled: Mapped[bool] = mapped_column(default=False)
    tax_regime: Mapped[str | None] = mapped_column(default=None)
    residency_status_change_date: Mapped[date | None] = mapped_column(default=None)
    w8ben_claimed: Mapped[bool] = mapped_column(default=False)
    w8ben_treaty_rate_pct: Mapped[Decimal | None] = mapped_column(RATE, default=None)
    marginal_ordinary_rate_pct: Mapped[Decimal | None] = mapped_column(RATE, default=None)
    qualified_ltcg_rate_pct: Mapped[Decimal | None] = mapped_column(RATE, default=None)
