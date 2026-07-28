"""Cron replacement for the old "Sync exchange rates" button — polled a few times around the ECB's ~16:00 CET publish.

The ECB publishes one fixed daily reference rate rather than a
continuously updating feed, so unlike `trades.market_data.price_sync`'s
price refresh, there's no "which intraday tick is the real one yet"
problem here to work around — just uncertainty about the exact minute
the day's rate lands. Rather than run continuously (hourly), this is
meant to be invoked a handful of times in a tight window around 16:00 CET
— 14:00, 14:45, and 15:30 UTC as `crontab` entries on the deploy VM,
spanning the fixing across both CEST/summer and CET/winter clocks —
safe to do because `exchange_rates.update_rate_history_cache` is already
incremental, so a run that finds the cache already current makes no
network call at all. Exchange rates aren't user-scoped, unlike
`trades.market_data.price_sync`'s price refresh — no Postgres session is
needed here at all. Run via `python -m accounting.market_data.fx_sync`.
"""

from __future__ import annotations

from accounting.config import AccountingConfig
from accounting.market_data import exchange_rates


def run_fx_sync(config: AccountingConfig | None = None) -> None:
    """Refresh the cached exchange-rate history.

    `exchange_rates.update_rate_history_cache` is already incremental —
    safe to call repeatedly in the same day, each run only fetches the
    gap since the cache's last cached date (and makes no network call at
    all once already current).

    Parameters
    ----------
    config
        Application configuration. Defaults to `AccountingConfig()`.
    """
    config = config or AccountingConfig()
    exchange_rates.update_rate_history_cache(config)


if __name__ == "__main__":
    run_fx_sync()
    print("Refreshed exchange-rate history")  # noqa: T201 — this is a CLI entrypoint, not library code
