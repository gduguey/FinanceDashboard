from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from trades import api
from trades.brokers import ibkr
from trades.config import IbkrFlexApiConfig, PriceApiConfig

TRADE_ROWS = [
    {
        "account_id": "U1",
        "transaction_id": "1",
        "trade_id": "1",
        "symbol": "VOO",
        "asset_category": "STK",
        "currency": "USD",
        "buy_sell": "BUY",
        "trade_date": pd.Timestamp("2026-01-01"),
        "quantity": 1.0,
        "trade_price": 500.0,
        "trade_money": 500.0,
        "ib_commission": -1.0,
        "net_cash": -501.0,
    },
    {
        "account_id": "U1",
        "transaction_id": "2",
        "trade_id": "2",
        "symbol": "VOO",
        "asset_category": "STK",
        "currency": "USD",
        "buy_sell": "SELL",
        "trade_date": pd.Timestamp("2026-01-02"),
        "quantity": -1.0,
        "trade_price": 500.0,
        "trade_money": -500.0,
        "ib_commission": -1.0,
        "net_cash": 499.0,
    },
]


@pytest.fixture(autouse=True)
def _isolated_caches(tmp_path, monkeypatch):
    """Point every module-level config at a throwaway cache dir, and seed a
    minimal local trade + price history so GET endpoints never touch the
    network (per api.py's read-only GET contract)."""
    ibkr_config = IbkrFlexApiConfig(cache_dir=tmp_path / "ibkr")
    price_config = PriceApiConfig(cache_dir=tmp_path / "prices")
    monkeypatch.setattr(api, "ibkr_config", ibkr_config)
    monkeypatch.setattr(api, "price_api_config", price_config)

    ibkr._atomic_write_csv(ibkr_config.cache_dir / "trades.csv", pd.DataFrame(TRADE_ROWS))
    price_history = pd.DataFrame(
        {"price_date": pd.to_datetime(["2026-01-01", "2026-01-02"]), "close": [500.0, 550.0]}
    )
    price_config.cache_dir.mkdir(parents=True)
    price_history.to_csv(price_config.cache_dir / "VOO.csv", index=False)
    return ibkr_config, price_config


@pytest.fixture
def client():
    return TestClient(api.app)


def test_summary_reports_current_value_and_gain(client) -> None:
    response = client.get("/api/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total_invested_usd"] == pytest.approx(501.0)
    assert body["current_value_usd"] == pytest.approx(550.0)
    assert body["total_gain_usd"] == pytest.approx(49.0)
    assert body["symbol_count"] == 1


def test_summary_reports_last_synced_from_position_snapshots(client, _isolated_caches) -> None:
    ibkr_config, _ = _isolated_caches
    snapshot = pd.DataFrame(
        [
            {
                "pulled_at": pd.Timestamp("2026-01-02T06:00:00"),
                "account_id": "U1",
                "symbol": "VOO",
                "report_date": pd.Timestamp("2026-01-02"),
            }
        ]
    )
    ibkr._atomic_write_csv(ibkr_config.cache_dir / "position_snapshots.csv", snapshot)

    body = client.get("/api/summary").json()
    assert body["last_synced_at"] == "2026-01-02T06:00:00"


def test_trades_only_includes_buys(client) -> None:
    trades = client.get("/api/trades").json()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "VOO"


def test_returns_missing_price_is_a_422_not_a_silent_gap(client, _isolated_caches) -> None:
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
    empty_config = IbkrFlexApiConfig(cache_dir=tmp_path / "empty")
    monkeypatch.setattr(api, "ibkr_config", empty_config)
    assert client.get("/api/trades").status_code == 404


def test_sync_calls_ibkr_and_refreshes_prices_without_hitting_network(
    client, monkeypatch
) -> None:
    monkeypatch.setenv("IBKR_FLEX_WEB_SERVICE_TOKEN", "test-token")
    monkeypatch.setenv("IBKR_QUERY_ID", "12345")

    sync_calls = []
    monkeypatch.setattr(
        api.ibkr,
        "sync_ibkr_account",
        lambda credentials, config: sync_calls.append((credentials, config))
        or ibkr.IbkrSyncResult(
            pulled_at=pd.Timestamp("2026-01-03").to_pydatetime(),
            statement_from_date=date(2026, 1, 3),
            statement_to_date=date(2026, 1, 3),
            new_trade_count=0,
            total_trade_count=1,
        ),
    )
    price_calls = []
    monkeypatch.setattr(
        api.prices,
        "update_price_caches",
        lambda symbols, since, as_of, config: price_calls.append(symbols) or {},
    )

    response = client.post("/api/sync")

    assert response.status_code == 200
    assert len(sync_calls) == 1
    assert price_calls == [["VOO"]]
    assert response.json()["symbols_refreshed"] == ["VOO"]
