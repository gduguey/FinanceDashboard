"""Cron replacement for the old sync button's price-refresh legs — safe to run several times a day.

`db.session.get_db` only works inside a FastAPI request; this script opens
its own RLS-scoped session per user via `db.session.session_scope` instead
— the same convention `db.backup`/`trades.utils.statement_archive` already
use for code that isn't running inside a per-request context, except this
job genuinely needs *every* real user's own held symbols (the shared price
cache has to cover everyone, not just one person), so it also connects
once via the migration-owning role to list who those users even are —
exactly the same "bypass RLS on purpose, because this one thing is
inherently cross-user" reasoning `db.backup` already documents for itself.
Meant to be invoked several times in a tight window around Yahoo's market
close — 20:30, 21:15, and 22:00 UTC as `crontab` entries on the deploy VM,
spanning US markets' 4pm ET close in both daylight and standard time —
rather than once a day like `daily_sync`: this module itself doesn't need
to know that schedule, since `prices.update_price_cache`'s settlement-buffer
re-fetching (see `market_data.prices`) already makes repeat same-day calls
mostly no-ops beyond the trailing buffer window. Run via
`python -m trades.market_data.price_sync`.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, text

from db.session import session_scope
from db.settings import DatabaseSettings
from trades import dashboard
from trades.api.dependencies import _first_event_date
from trades.brokers.ibkr import main
from trades.config import AppConfig
from trades.market_data import prices

if TYPE_CHECKING:
    import uuid

logger = logging.getLogger(__name__)


def _all_user_ids() -> list[uuid.UUID]:
    """Every real user's id, listed via the migration-owning role — bypasses Row-Level Security on purpose.

    `users` itself has an RLS policy scoping a normal session to its own
    row only (migration `817ace9deb09`) — appropriate for every other
    caller, wrong for this one, which genuinely needs the full list to
    know whose portfolios to check. `DatabaseSettings` (`DATABASE_URL`) is
    the same superuser/owner role `db.backup` already uses for the
    equivalent reason.

    Returns
    -------
    list[uuid.UUID]
    """
    # database_url has no default (see db.session.get_engine's own note) — pydantic-settings
    # fills it from DATABASE_URL at runtime, but mypy has no pydantic plugin configured here.
    engine = create_engine(DatabaseSettings().database_url)  # type: ignore[call-arg]
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT id FROM users")).fetchall()
    return [row.id for row in rows]


def run_price_sync(config: AppConfig | None = None) -> list[str]:
    """Refresh the raw and adjusted price caches for every symbol any real user holds, plus every chosen benchmark.

    Replicates the price-refresh legs `POST /api/sync` used to run on
    every click — the raw cache for every symbol ever held by *any* user
    (excluding `config.ledger.cash_symbol`) plus every user's own chosen
    benchmark symbol, and the dividend/split-adjusted cache for that same
    combined set (every held symbol needs its own adjusted-close series
    for its own counterfactual comparisons, not just the benchmark). Raw
    prices are refreshed incrementally (`prices.update_price_caches`),
    which now always re-checks the last `prices._SETTLEMENT_BUFFER_DAYS`
    days regardless of what's already cached; adjusted prices are always
    fully re-fetched (`prices.refresh_adjusted_price_histories`), since
    Yahoo retroactively rewrites `adjclose` for a symbol's whole history on
    every new dividend/split.

    Parameters
    ----------
    config
        Application configuration. Defaults to `AppConfig()`.

    Returns
    -------
    list[str]
        Every symbol whose raw price cache was refreshed (every user's
        held symbols plus every user's benchmark, sorted).
    """
    config = config or AppConfig()
    today = datetime.now(tz=UTC).date()

    held_symbols: set[str] = set()
    benchmark_symbols: set[str] = set()
    first_event = today

    def _inputs_for(user_id: uuid.UUID) -> tuple[str, set[str], date | None]:
        """Resolve one user's benchmark symbol, held symbols, and earliest event date.

        Returns
        -------
        tuple[str, set[str], date | None]
            The benchmark symbol, the held symbols (empty when the ledger is
            empty), and the earliest event date (`None` when the ledger is empty).
        """
        with session_scope(user_id) as session:
            raw_ledger = main.load_ledger(session, user_id)
            settings = dashboard.load_settings(session, user_id)
        benchmark = dashboard.resolved_benchmark_symbol(config, settings)
        if raw_ledger.is_empty():
            return benchmark, set(), None
        held = set(raw_ledger["symbol"].unique().to_list()) - {config.ledger.cash_symbol}
        return benchmark, held, _first_event_date(raw_ledger)

    for user_id in _all_user_ids():
        # Isolate per-user failures: this is a cross-user batch, so one user's
        # error anywhere in resolving their symbols must not abort the price
        # refresh for everyone else — log it and move on.
        try:
            benchmark, held, event_date = _inputs_for(user_id)
        except Exception:
            logger.exception("price sync: skipping user %s after an error", user_id)
            continue
        benchmark_symbols.add(benchmark)
        held_symbols |= held
        if event_date is not None:
            first_event = min(first_event, event_date)

    raw_symbols = sorted(held_symbols | benchmark_symbols)

    prices.update_price_caches(raw_symbols, since=first_event, as_of=today, config=config)
    prices.refresh_adjusted_price_histories(raw_symbols, since=first_event, as_of=today, config=config)

    return raw_symbols


if __name__ == "__main__":
    symbols = run_price_sync()
    print(f"Refreshed prices for {len(symbols)} symbols: {', '.join(symbols)}")  # noqa: T201 — CLI entrypoint, not library code
