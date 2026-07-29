"""Per-user brokerage connections and the ledger events they produce.

Global, non-user-specific reference data (daily close prices, CPI, HYSA
rates) deliberately stays out of Postgres — see `DATABASE_SCHEMA.md` — it's
identical for every user and stays exactly where it is today, under
`data/trades/{prices,cpi,hysa_rates}/`.

Every table's primary key is a surrogate `id` (never a composite
`(user_id, ...)` key), minted by the database from `db.base.UUID7_DEFAULT`
— time-ordered, so inserts append to the B-tree rather than scattering
through it (DB-audit D3). `natural_key` is the human-meaningful string (a
connection name, an import's own dedup key) that identity is actually about,
and its `UNIQUE(user_id, natural_key)` is what makes re-importing the same
source idempotent: the write conflicts on that key, not on a recomputed id.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import MONEY, RATE, SHARES, UUID7_DEFAULT, Base, RateMap, Timestamped, check_in_sql
from db.models import CURRENCY_CODE_COLUMN
from trades.config import LedgerEventType, TaxRegime

SCHEMA = "trades"


class Security(Base, Timestamped):
    """The reference list of instruments a ledger event or a benchmark override can name.

    `ledger_events.symbol` and `dashboard_settings.benchmark_symbol_override`
    were both free text with nothing behind them (DB-audit move #3). The
    second one was the visible failure: a mistyped benchmark silently
    produced an empty counterfactual, because `market_data.prices` fetched a
    symbol Yahoo has never heard of, cached the empty result, and every
    comparison downstream read zero rows rather than an error.

    ## `symbol` is the primary key, and the only column

    Same reasoning as `db.models.Currency` and `accounting.db.institutions`:
    the referencing columns keep holding the ticker itself, so nothing above
    the persistence boundary changed — `models.LedgerEvent.symbol` is still a
    `str`, `LedgerEvent.polars_schema` is unchanged, and every price cache
    file is still named after it (`market_data.prices._cache_path`).

    Nothing else is stored **because nothing else is populated**.
    `market_data.symbol_search` does know a symbol's long name and its
    exchange — but only for a symbol a person typed into the benchmark
    picker, and the endpoint hands those two fields straight to the browser
    (`api.routers.settings` persists only `symbol_override`). The IBKR sync,
    which is where all but one symbol in this table comes from, knows the
    ticker and nothing else. `market_data.prices` knows only the ticker too:
    it keys a CSV per symbol and holds no metadata at all. A `name` column
    would therefore be `NULL` for every row the sync creates, which is a
    column that promises information the app cannot supply.

    ## Rows appear as a side effect of the write that names them

    Symbols arrive dynamically — a user buys something new and the next sync
    mentions it — so this is an open vocabulary like `institutions`, filled
    by `db.base.ensure_reference_rows` from the two paths that write a symbol
    (`brokers.ibkr.main._write_ledger` and `dashboard.settings.save_settings`)
    rather than seeded. There is nothing to seed: a fresh user's ledger is
    empty, and `config.returns.benchmark_symbol` — the default when no
    override is set — is never stored in this schema at all.

    No `user_id`: `AAPL` is the same instrument for everyone (see
    `db.tenant.is_reference_table`), so no policy and no `RLS_EXEMPT` entry.
    """

    __tablename__ = "securities"
    __table_args__ = {"schema": SCHEMA}

    symbol: Mapped[str] = mapped_column(primary_key=True)
    """The ticker as the broker reports it and every referencing column stores it, e.g. `VOO`."""


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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID7_DEFAULT)
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

    `currency` is a foreign key into `public.currencies` and `symbol` one
    into `Security`. `currency` in particular was **the one currency column in
    this schema with no constraint at all**: the other eight each restated
    `CHECK (currency IN ('USD', 'EUR'))` from `accounting.models`, and this one
    could not, because `trades` may not import `accounting`. The list lives in
    `db.currency` now, below both packages, so the column that was the
    exception is covered by the same mechanism as the rest.
    """

    __tablename__ = "ledger_events"
    __table_args__ = (
        CheckConstraint(check_in_sql("event_type", get_args(LedgerEventType)), name="event_type"),
        UniqueConstraint("user_id", "natural_key", name="uq_ledger_events_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID7_DEFAULT)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.broker_connections.id", ondelete="CASCADE")
    )
    event_datetime: Mapped[datetime]
    symbol: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.securities.symbol"))
    event_type: Mapped[str]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(ForeignKey(CURRENCY_CODE_COLUMN))
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
    benchmark_symbol_override: Mapped[str | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.securities.symbol"), default=None
    )
    """Which instrument to compare returns against, or `NULL` for `config.returns.benchmark_symbol`.

    A real reference now: a mistyped ticker here used to be stored happily
    and then produce an empty benchmark series rather than an error. See
    `Security`."""
    local_zone: Mapped[str | None] = mapped_column(default=None)
    tax_enabled: Mapped[bool] = mapped_column(default=False)
    tax_regime: Mapped[str | None] = mapped_column(default=None)
    residency_status_change_date: Mapped[date | None] = mapped_column(default=None)
    w8ben_claimed: Mapped[bool] = mapped_column(default=False)
    w8ben_treaty_rate_pct: Mapped[Decimal | None] = mapped_column(RATE, default=None)
    marginal_ordinary_rate_pct: Mapped[Decimal | None] = mapped_column(RATE, default=None)
    qualified_ltcg_rate_pct: Mapped[Decimal | None] = mapped_column(RATE, default=None)
