"""Cron replacement for the old sync button's price-refresh legs — safe to run several times a day.

`db.session.get_db` only works inside a FastAPI request; this script opens
its own RLS-scoped session via `db.session.session_scope` instead, using
`db.current_user.DEFAULT_USER_ID` — the same convention `db.backup` and
`trades.utils.statement_archive` already use for code that isn't running
inside a per-request context. Meant to be invoked several times in a tight
window around Yahoo's market close (see `docs/server-setup/maintenance.md`
for the cron entries) rather than once a day like `daily_sync`: this module
itself doesn't need to know that schedule, since `prices.update_price_cache`'s
settlement-buffer re-fetching (see `market_data.prices`) already makes
repeat same-day calls mostly no-ops beyond the trailing buffer window. Run
via `python -m trades.market_data.price_sync`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from db.current_user import DEFAULT_USER_ID
from db.session import session_scope
from trades import dashboard
from trades.api.dependencies import _first_event_date
from trades.brokers.ibkr import main
from trades.config import AppConfig
from trades.market_data import prices


def run_price_sync(config: AppConfig | None = None) -> list[str]:
    """Refresh the raw and adjusted price caches for every held symbol plus the benchmark.

    Replicates the price-refresh legs `POST /api/sync` used to run on
    every click — the raw cache for every symbol ever held (excluding
    `config.ledger.cash_symbol`) plus the benchmark symbol, and the
    dividend/split-adjusted cache for that same set (every held symbol
    needs its own adjusted-close series for its own counterfactual
    comparisons, not just the benchmark). Raw prices are refreshed
    incrementally (`prices.update_price_caches`), which now always
    re-checks the last `prices._SETTLEMENT_BUFFER_DAYS` days regardless of
    what's already cached; adjusted prices are always fully re-fetched
    (`prices.refresh_adjusted_price_histories`), since Yahoo retroactively
    rewrites `adjclose` for a symbol's whole history on every new
    dividend/split.

    Parameters
    ----------
    config
        Application configuration. Defaults to `AppConfig()`.

    Returns
    -------
    list[str]
        Every symbol whose raw price cache was refreshed (held symbols
        plus the benchmark, sorted).
    """
    config = config or AppConfig()
    today = datetime.now(tz=UTC).date()

    with session_scope(DEFAULT_USER_ID) as session:
        raw_ledger = main.load_ledger(session)
        settings = dashboard.load_settings(session, DEFAULT_USER_ID)

    benchmark_symbol = dashboard.resolved_benchmark_symbol(config, settings)
    if raw_ledger.is_empty():
        held_symbols: list[str] = []
        first_event = today
    else:
        held_symbols = sorted(set(raw_ledger["symbol"].unique().to_list()) - {config.ledger.cash_symbol})
        first_event = _first_event_date(raw_ledger)
    raw_symbols = sorted({*held_symbols, benchmark_symbol})

    prices.update_price_caches(raw_symbols, since=first_event, as_of=today, config=config)
    prices.refresh_adjusted_price_histories(raw_symbols, since=first_event, as_of=today, config=config)

    return raw_symbols


if __name__ == "__main__":
    symbols = run_price_sync()
    print(f"Refreshed prices for {len(symbols)} symbols: {', '.join(symbols)}")  # noqa: T201 — CLI entrypoint, not library code
