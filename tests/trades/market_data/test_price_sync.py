"""Tests for `trades.market_data.price_sync`: the cron replacement for the old sync button's price legs."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime

import polars as pl
import pytest

from trades import dashboard
from trades.brokers.ibkr import main
from trades.config import AppConfig
from trades.market_data import price_sync, prices

_USER_A = uuid.uuid4()
_USER_B = uuid.uuid4()


def _config(tmp_path) -> AppConfig:
    return AppConfig(prices={"cache_dir": tmp_path})


@pytest.fixture(autouse=True)
def _fake_session_scope(monkeypatch):
    """Every DB-touching function below is mocked, so `session_scope` never needs a real Postgres session."""

    @contextmanager
    def _fake_scope(user_id):
        yield object()

    monkeypatch.setattr(price_sync, "session_scope", _fake_scope)


@pytest.fixture(autouse=True)
def _one_fake_user(monkeypatch):
    """Most tests below only care about the single-user aggregation logic — one fixed id is enough.

    `test_...aggregates_across_every_real_user...` overrides this with two.
    """
    monkeypatch.setattr(price_sync, "_all_user_ids", lambda: [_USER_A])


def test_held_symbols_excludes_the_cash_symbol_and_includes_the_benchmark(tmp_path, monkeypatch) -> None:
    ledger = pl.DataFrame({
        "event_datetime": [datetime(2026, 1, 1), datetime(2026, 1, 2), datetime(2026, 1, 3)],
        "symbol": ["CASH", "AAPL", "MSFT"],
    })
    monkeypatch.setattr(main, "load_ledger", lambda session, user_id: ledger)
    monkeypatch.setattr(dashboard, "load_settings", lambda session, user_id: dashboard.DashboardSettings())
    monkeypatch.setattr(dashboard, "resolved_benchmark_symbol", lambda config, settings: "VOO")

    raw_calls = []
    monkeypatch.setattr(
        prices, "update_price_caches", lambda symbols, since, as_of, config: raw_calls.append((symbols, since, as_of))
    )
    adjusted_calls = []
    monkeypatch.setattr(
        prices,
        "refresh_adjusted_price_histories",
        lambda symbols, since, as_of, config: adjusted_calls.append((symbols, since, as_of)),
    )

    symbols = price_sync.run_price_sync(_config(tmp_path))

    assert symbols == ["AAPL", "MSFT", "VOO"]
    assert raw_calls == [(["AAPL", "MSFT", "VOO"], date(2026, 1, 1), raw_calls[0][2])]
    assert adjusted_calls == [(["AAPL", "MSFT", "VOO"], date(2026, 1, 1), adjusted_calls[0][2])]


def test_empty_ledger_uses_today_as_since_and_no_held_symbols(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "load_ledger", lambda session, user_id: pl.DataFrame(schema={"symbol": pl.Utf8}))
    monkeypatch.setattr(dashboard, "load_settings", lambda session, user_id: dashboard.DashboardSettings())
    monkeypatch.setattr(dashboard, "resolved_benchmark_symbol", lambda config, settings: "VOO")

    raw_calls = []
    monkeypatch.setattr(
        prices, "update_price_caches", lambda symbols, since, as_of, config: raw_calls.append((symbols, since, as_of))
    )
    monkeypatch.setattr(prices, "refresh_adjusted_price_histories", lambda symbols, since, as_of, config: None)

    symbols = price_sync.run_price_sync(_config(tmp_path))

    assert symbols == ["VOO"]
    [(refreshed_symbols, since, as_of)] = raw_calls
    assert refreshed_symbols == ["VOO"]
    assert since == as_of  # both `today`


def test_returns_the_refreshed_symbol_list(tmp_path, monkeypatch) -> None:
    ledger = pl.DataFrame({"event_datetime": [datetime(2026, 1, 1)], "symbol": ["AAPL"]})
    monkeypatch.setattr(main, "load_ledger", lambda session, user_id: ledger)
    monkeypatch.setattr(dashboard, "load_settings", lambda session, user_id: dashboard.DashboardSettings())
    monkeypatch.setattr(dashboard, "resolved_benchmark_symbol", lambda config, settings: "VOO")
    monkeypatch.setattr(prices, "update_price_caches", lambda symbols, since, as_of, config: {})
    monkeypatch.setattr(prices, "refresh_adjusted_price_histories", lambda symbols, since, as_of, config: {})

    symbols = price_sync.run_price_sync(_config(tmp_path))

    assert symbols == ["AAPL", "VOO"]


def test_refreshes_adjusted_history_for_every_held_and_benchmark_symbol_not_just_the_benchmark(
    tmp_path, monkeypatch
) -> None:
    # Unlike the old hourly sync (which only kept the benchmark's adjusted
    # cache fresh), every held symbol also needs its adjusted-close cache
    # refreshed for its own counterfactual comparisons.
    ledger = pl.DataFrame({"event_datetime": [datetime(2026, 1, 1)], "symbol": ["AAPL"]})
    monkeypatch.setattr(main, "load_ledger", lambda session, user_id: ledger)
    monkeypatch.setattr(dashboard, "load_settings", lambda session, user_id: dashboard.DashboardSettings())
    monkeypatch.setattr(dashboard, "resolved_benchmark_symbol", lambda config, settings: "VOO")
    monkeypatch.setattr(prices, "update_price_caches", lambda symbols, since, as_of, config: {})

    adjusted_calls = []
    monkeypatch.setattr(
        prices,
        "refresh_adjusted_price_histories",
        lambda symbols, since, as_of, config: adjusted_calls.append(symbols),
    )

    price_sync.run_price_sync(_config(tmp_path))

    assert adjusted_calls == [["AAPL", "VOO"]]


def test_aggregates_held_and_benchmark_symbols_across_every_real_user(tmp_path, monkeypatch) -> None:
    """The actual multi-user fix: two different users' portfolios both contribute to what gets refreshed."""
    monkeypatch.setattr(price_sync, "_all_user_ids", lambda: [_USER_A, _USER_B])

    ledgers = {
        _USER_A: pl.DataFrame({"event_datetime": [datetime(2026, 1, 2)], "symbol": ["AAPL"]}),
        _USER_B: pl.DataFrame({"event_datetime": [datetime(2026, 1, 1)], "symbol": ["TSLA"]}),
    }
    benchmarks = {_USER_A: "VOO", _USER_B: "SPY"}

    monkeypatch.setattr(main, "load_ledger", lambda session, user_id: ledgers[user_id])
    monkeypatch.setattr(dashboard, "load_settings", lambda session, user_id: user_id)
    monkeypatch.setattr(dashboard, "resolved_benchmark_symbol", lambda config, settings: benchmarks[settings])

    raw_calls = []
    monkeypatch.setattr(
        prices, "update_price_caches", lambda symbols, since, as_of, config: raw_calls.append((symbols, since))
    )
    monkeypatch.setattr(prices, "refresh_adjusted_price_histories", lambda symbols, since, as_of, config: None)

    symbols = price_sync.run_price_sync(_config(tmp_path))

    assert symbols == ["AAPL", "SPY", "TSLA", "VOO"]
    # The earlier of the two users' first-event dates wins, so neither user's history is truncated.
    assert raw_calls == [(["AAPL", "SPY", "TSLA", "VOO"], date(2026, 1, 1))]
