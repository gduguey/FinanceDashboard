"""Cron replacement for the old sync button's CPI/HYSA-refresh legs — run daily, not on a click.

Unlike `trades.market_data.price_sync`'s price refresh, neither CPI nor
HYSA rates are fetched incrementally (see `cpi.py`/`hysa_rates.py` for
why) — both are small, full re-fetches, cheap enough once a day. No
Postgres session is needed either: neither cache depends on the ledger.
Run via `python -m trades.market_data.daily_sync`, once daily at 4:00 UTC
as a `crontab` entry on the deploy VM (after the nightly backup).
"""

from __future__ import annotations

from trades.config import AppConfig
from trades.market_data import cpi, hysa_rates


def run_daily_market_data_sync(config: AppConfig | None = None) -> None:
    """Refresh the CPI index cache and every bank's HYSA rate history, both full re-fetches.

    Parameters
    ----------
    config
        Application configuration. Defaults to `AppConfig()`.
    """
    config = config or AppConfig()
    cpi.update_cpi_cache(config)
    hysa_rates.update_hysa_rates_cache(config)


if __name__ == "__main__":
    run_daily_market_data_sync()
    print("Refreshed CPI and HYSA rate caches")  # noqa: T201 — this is a CLI entrypoint, not library code
