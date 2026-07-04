from datetime import date, datetime

import polars as pl
import pytest
from fastapi.testclient import TestClient

from trades import api as trades_api
from trades.brokers.ibkr.main import IbkrSyncResult
from trades.config import AppConfig
from trades.utils.io_utils import write_csv_atomic

LEDGER_ROWS = [
    {
        "event_id": "ibkr:1",
        "event_datetime": datetime(2026, 1, 1, 10, 0, 0),
        "symbol": "VOO",
        "event_type": "BUY",
        "shares": 1.0,
        "price": 500.0,
        "amount": 500.0,
        "currency": "USD",
        "meta": "{}",
    },
    {
        "event_id": "ibkr:2",
        "event_datetime": datetime(2026, 1, 2, 10, 0, 0),
        "symbol": "VOO",
        "event_type": "SELL",
        "shares": 1.0,
        "price": 500.0,
        "amount": 500.0,
        "currency": "USD",
        "meta": "{}",
    },
]


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point the module-level config at a throwaway cache dir.

    Seeds a minimal local ledger + price history so GET endpoints never
    touch the network (per api.py's read-only GET contract).
    """
    config = AppConfig(ibkr={"cache_dir": tmp_path / "ibkr"}, prices={"cache_dir": tmp_path / "prices"})
    monkeypatch.setattr(trades_api, "app_config", config)

    write_csv_atomic(pl.DataFrame(LEDGER_ROWS), config.ibkr.ledger_csv_path)
    price_history = pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 550.0]})
    config.prices.cache_dir.mkdir(parents=True)
    write_csv_atomic(price_history, config.prices.cache_dir / "VOO.csv")
    return config


@pytest.fixture
def client():
    return TestClient(trades_api.app)


def test_summary_reports_current_value_and_gain(client) -> None:
    response = client.get("/api/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total_invested_usd"] == pytest.approx(500.0)
    assert body["current_value_usd"] == pytest.approx(550.0)
    assert body["total_gain_usd"] == pytest.approx(50.0)
    assert body["symbol_count"] == 1


def test_summary_reports_last_synced_from_raw_statement_archive(client, isolated_config) -> None:
    # Deliberately not seeded via the ledger's event_datetime — that's IBKR's
    # own whenGenerated, not local wall-clock time (see
    # ibkr.api.last_synced_at's docstring), so it's the wrong source for this.
    raw_dir = isolated_config.ibkr.raw_statement_dir
    raw_dir.mkdir(parents=True)
    (raw_dir / "20260102T060000.xml").write_text("<FlexQueryResponse />", encoding="utf-8")

    body = client.get("/api/summary").json()
    # Stored as naive UTC, rendered in config.timezone.local_zone (default America/New_York, EST in January).
    assert body["last_synced_at"] == "2026-01-02T01:00:00-05:00"


def test_summary_reports_no_sync_yet_when_never_synced(client) -> None:
    body = client.get("/api/summary").json()
    assert body["last_synced_at"] is None


def test_trades_only_includes_buys(client) -> None:
    trades = client.get("/api/trades").json()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "VOO"


def test_returns_missing_price_is_a_422_not_a_silent_gap(client) -> None:
    response = client.get("/api/returns", params={"as_of": "2020-01-01"})
    assert response.status_code == 422


def test_returns_curve_shape(client) -> None:
    body = client.get("/api/returns/curve").json()
    assert len(body["points"]) == 1
    assert len(body["trend"]) == 1
    assert body["hysa_annual_rate_pct"] == pytest.approx(4.0)


def test_schedule_endpoints_return_data(client) -> None:
    assert client.get("/api/schedule/monthly").json()
    assert client.get("/api/schedule/daily").json()
    pie = client.get("/api/schedule/pie").json()
    assert "Whole portfolio — by symbol" in pie


def test_no_trades_yet_is_a_404(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(trades_api, "app_config", AppConfig(ibkr={"cache_dir": tmp_path / "empty"}))
    assert client.get("/api/trades").status_code == 404


def test_sync_calls_ibkr_and_refreshes_prices_without_hitting_network(client, monkeypatch) -> None:
    monkeypatch.setenv("IBKR_FLEX_WEB_SERVICE_TOKEN", "test-token")
    monkeypatch.setenv("IBKR_QUERY_ID", "12345")

    def fake_sync(credentials, config):
        # Real `sync_ibkr_account` always archives a raw statement before
        # returning (see its docstring) — `last_synced_at` depends on that.
        raw_dir = config.ibkr.raw_statement_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "20260103T000000.xml").write_text("<FlexQueryResponse />", encoding="utf-8")
        sync_calls.append((credentials, config))
        return IbkrSyncResult(
            pulled_at=datetime(2026, 1, 3),
            statement_from_date=date(2026, 1, 3),
            statement_to_date=date(2026, 1, 3),
            new_event_count=0,
            total_event_count=1,
        )

    sync_calls = []
    monkeypatch.setattr(trades_api.main, "sync_ibkr_account", fake_sync)
    price_calls = []
    monkeypatch.setattr(
        trades_api.prices,
        "update_price_caches",
        lambda symbols, since, as_of, config: price_calls.append(symbols) or {},
    )

    response = client.post("/api/sync")

    assert response.status_code == 200
    assert len(sync_calls) == 1
    assert price_calls == [["VOO"]]
    body = response.json()
    assert body["symbols_refreshed"] == ["VOO"]
    # Stored as naive UTC, rendered in config.timezone.local_zone (default America/New_York, EST in January).
    assert body["synced_at"] == "2026-01-02T19:00:00-05:00"
