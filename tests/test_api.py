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
        "symbol": "CASH",
        "event_type": "DEPOSIT",
        "shares": None,
        "price": None,
        "amount": 2000.0,
        "currency": "USD",
        "meta": "{}",
    },
    {
        "event_id": "ibkr:2",
        "event_datetime": datetime(2026, 1, 2, 10, 0, 0),
        "symbol": "VOO",
        "event_type": "BUY",
        "shares": 2.0,
        "price": 500.0,
        "amount": 1000.0,
        "currency": "USD",
        "meta": "{}",
    },
    {
        "event_id": "ibkr:3",
        "event_datetime": datetime(2026, 1, 3, 10, 0, 0),
        "symbol": "VOO",
        "event_type": "SELL",
        "shares": 1.0,
        "price": 550.0,
        "amount": 550.0,
        "currency": "USD",
        "meta": "{}",
    },
]


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point the module-level config at a throwaway cache dir.

    Seeds a minimal local ledger + price + CPI history so GET endpoints
    never touch the network (per api.py's read-only GET contract).
    """
    config = AppConfig(
        ibkr={"cache_dir": tmp_path / "ibkr"},
        prices={"cache_dir": tmp_path / "prices"},
        cpi={"cache_dir": tmp_path / "cpi"},
        dashboard={"settings_path": tmp_path / "dashboard_settings.json"},
    )
    monkeypatch.setattr(trades_api, "app_config", config)

    write_csv_atomic(pl.DataFrame(LEDGER_ROWS), config.ibkr.ledger_csv_path)
    price_history = pl.DataFrame({
        "price_date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)],
        "close": [500.0, 500.0, 560.0],
    })
    config.prices.cache_dir.mkdir(parents=True)
    write_csv_atomic(price_history, config.prices.cache_dir / "VOO.csv")
    write_csv_atomic(price_history, config.prices.cache_dir / "VOO.adjusted.csv")
    config.cpi.cache_dir.mkdir(parents=True)
    write_csv_atomic(
        pl.DataFrame({"observation_date": [date(2026, 1, 1)], "value": [300.0]}), config.cpi.cache_dir / "CPIAUCSL.csv"
    )
    return config


@pytest.fixture
def client():
    return TestClient(trades_api.app)


def test_overview_reports_value_and_gain(client) -> None:
    response = client.get("/api/overview", params={"as_of": "2026-01-03"})
    assert response.status_code == 200
    body = response.json()
    assert body["value_usd"] == pytest.approx(1 * 560.0 + 1550.0)
    assert body["realized_gain_usd"] == pytest.approx(50.0)


def test_overview_no_ledger_is_a_404(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(trades_api, "app_config", AppConfig(ibkr={"cache_dir": tmp_path / "empty"}))
    assert client.get("/api/overview").status_code == 404


def test_dollar_chart_returns_series_and_reallocation_markers(client) -> None:
    body = client.get("/api/chart/dollar", params={"start": "2026-01-01", "end": "2026-01-03"}).json()
    assert len(body["series"]) == 3
    assert body["series"][0]["date"] == "2026-01-01"
    assert body["reallocation_markers"] == []


def test_growth_of_100_chart_returns_one_entry_per_day(client) -> None:
    body = client.get("/api/chart/growth-of-100", params={"start": "2026-01-01", "end": "2026-01-03"}).json()
    assert len(body) == 3
    assert body[0]["portfolio_index"] == pytest.approx(100.0)


def test_monthly_pnl_returns_one_entry_for_january(client) -> None:
    body = client.get("/api/chart/monthly-pnl", params={"start": "2026-01-01", "end": "2026-01-03"}).json()
    assert len(body) == 1
    assert body[0]["month"] == "2026-01"


def test_monthly_pnl_by_symbol_returns_one_row_per_symbol_per_month(client) -> None:
    body = client.get("/api/chart/monthly-pnl/by-symbol", params={"start": "2026-01-01", "end": "2026-01-03"}).json()
    symbols = {row["symbol"] for row in body}
    assert symbols == {"VOO", "CASH"}


def test_allocation_reports_voo_and_cash(client) -> None:
    body = client.get("/api/allocation", params={"as_of": "2026-01-03"}).json()
    symbols = {row["symbol"] for row in body}
    assert symbols == {"VOO", "CASH"}


def test_target_allocation_defaults_to_empty(client) -> None:
    assert client.get("/api/settings/target-allocation").json() == {}


def test_target_allocation_put_then_get_round_trips(client) -> None:
    put_response = client.put("/api/settings/target-allocation", json={"VOO": 80.0})
    assert put_response.status_code == 200
    assert client.get("/api/settings/target-allocation").json() == {"VOO": 80.0}


def test_lots_reports_open_and_closed_lots(client) -> None:
    body = client.get("/api/lots", params={"as_of": "2026-01-03"}).json()
    assert len(body["open_lots"]) == 1
    assert len(body["closed_lots"]) == 1
    assert len(body["symbol_rollup"]) == 1


def test_risk_reports_max_drawdown(client) -> None:
    body = client.get("/api/risk", params={"start": "2026-01-01", "end": "2026-01-03"}).json()
    assert body["max_drawdown_pct"] <= 0.0


def test_data_quality_includes_held_and_benchmark_symbols(client) -> None:
    body = client.get("/api/data-quality").json()
    symbols = {row["symbol"] for row in body}
    assert symbols == {"VOO"}
    assert body[0]["last_price_date"] == "2026-01-03"


def test_ledger_export_returns_every_row(client) -> None:
    body = client.get("/api/ledger/export").json()
    assert len(body) == len(LEDGER_ROWS)


def test_sync_calls_ibkr_and_refreshes_price_and_cpi_caches_without_hitting_network(client, monkeypatch) -> None:
    monkeypatch.setenv("IBKR_FLEX_WEB_SERVICE_TOKEN", "test-token")
    monkeypatch.setenv("IBKR_QUERY_ID", "12345")

    def fake_sync(credentials, config):
        raw_dir = config.ibkr.raw_statement_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "20260104T000000.xml").write_text("<FlexQueryResponse />", encoding="utf-8")
        sync_calls.append((credentials, config))
        return IbkrSyncResult(
            pulled_at=datetime(2026, 1, 4),
            statement_from_date=date(2026, 1, 4),
            statement_to_date=date(2026, 1, 4),
            new_event_count=0,
            total_event_count=3,
        )

    sync_calls = []
    monkeypatch.setattr(trades_api.main, "sync_ibkr_account", fake_sync)
    raw_price_calls = []
    monkeypatch.setattr(
        trades_api.prices,
        "update_price_caches",
        lambda symbols, since, as_of, config: raw_price_calls.append(symbols) or {},
    )
    adjusted_price_calls = []
    monkeypatch.setattr(
        trades_api.prices,
        "update_price_cache",
        lambda symbol, since, as_of, config, adjusted=False: adjusted_price_calls.append((symbol, adjusted)),
    )
    cpi_calls = []
    monkeypatch.setattr(trades_api.cpi_module, "update_cpi_cache", cpi_calls.append)

    response = client.post("/api/sync")

    assert response.status_code == 200
    assert len(sync_calls) == 1
    assert raw_price_calls == [["VOO"]]
    assert adjusted_price_calls == [("VOO", True)]
    assert len(cpi_calls) == 1
    body = response.json()
    assert body["symbols_refreshed"] == ["VOO"]
