"""Tests for `trades.market_data.daily_sync`: the cron replacement for the old sync button's CPI/HYSA legs."""

from __future__ import annotations

import polars as pl

from trades.config import AppConfig
from trades.market_data import cpi, daily_sync, hysa_rates


def _config(tmp_path) -> AppConfig:
    return AppConfig(prices={"cache_dir": tmp_path})


def test_run_daily_market_data_sync_refreshes_cpi_and_hysa_caches(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cpi, "update_cpi_cache", lambda config: calls.append("cpi") or pl.DataFrame())
    monkeypatch.setattr(hysa_rates, "update_hysa_rates_cache", lambda config: calls.append("hysa") or pl.DataFrame())

    result = daily_sync.run_daily_market_data_sync(_config(tmp_path))

    assert result is None
    assert calls == ["cpi", "hysa"]


def test_run_daily_market_data_sync_defaults_to_appconfig(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(daily_sync, "AppConfig", lambda: config)
    calls: list[AppConfig] = []
    monkeypatch.setattr(cpi, "update_cpi_cache", lambda cfg: calls.append(cfg) or pl.DataFrame())
    monkeypatch.setattr(hysa_rates, "update_hysa_rates_cache", lambda cfg: calls.append(cfg) or pl.DataFrame())

    daily_sync.run_daily_market_data_sync()

    assert calls == [config, config]
