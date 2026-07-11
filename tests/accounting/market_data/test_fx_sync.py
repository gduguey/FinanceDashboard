"""Tests for `accounting.market_data.fx_sync`: the cron replacement for the "Sync exchange rates" button."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import polars as pl
import pytest

from accounting.config import AccountingConfig
from accounting.market_data import exchange_rates, fx_sync


def _config(tmp_path) -> AccountingConfig:
    return AccountingConfig(data_dir=tmp_path)


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        pass


class _FakeRequestsModule:
    """Stands in for the top-level `requests` module `exchange_rates` falls back to when no session is given."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict] = []

    def get(self, url, params, timeout):
        self.calls.append({"url": url, "params": params})
        return _FakeResponse(self._text)


_PAYLOAD = json.dumps({
    "amount": 1.0,
    "base": "USD",
    "start_date": "2026-06-01",
    "end_date": "2026-06-01",
    "rates": {"2026-06-01": {"EUR": 0.86}},
})


def test_run_fx_sync_refreshes_the_cache(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    fake_requests = _FakeRequestsModule(_PAYLOAD)
    monkeypatch.setattr(exchange_rates, "requests", fake_requests)

    result = fx_sync.run_fx_sync(config)

    assert result is None
    assert len(fake_requests.calls) == 1
    history = exchange_rates.load_rate_history(config)
    assert not history.is_empty()
    assert history.filter(pl.col("currency") == "EUR")["rate_to_base"].item() == pytest.approx(1 / 0.86)


def test_run_fx_sync_makes_no_network_call_when_cache_already_current(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    today = datetime.now(tz=UTC).date()
    monkeypatch.setattr(
        exchange_rates,
        "load_rate_history",
        lambda cfg: pl.DataFrame(
            {"date": [today], "currency": ["EUR"], "rate_to_base": [1.1]}, schema=exchange_rates.RATE_HISTORY_SCHEMA
        ),
    )
    fake_requests = _FakeRequestsModule(_PAYLOAD)
    monkeypatch.setattr(exchange_rates, "requests", fake_requests)

    fx_sync.run_fx_sync(config)

    assert fake_requests.calls == []


def test_run_fx_sync_defaults_to_accounting_config(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(fx_sync, "AccountingConfig", lambda: config)
    fake_requests = _FakeRequestsModule(_PAYLOAD)
    monkeypatch.setattr(exchange_rates, "requests", fake_requests)

    fx_sync.run_fx_sync()

    assert not exchange_rates.load_rate_history(config).is_empty()
