import io
import zipfile
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
        hysa_rates={"cache_dir": tmp_path / "hysa_rates"},
        dashboard={"settings_path": tmp_path / "dashboard_settings.json"},
        credentials={"ibkr_credentials_path": tmp_path / "credentials.json"},
    )
    monkeypatch.setattr(trades_api.app.state, "config", config)
    monkeypatch.setattr(
        trades_api.app.state, "sync_progress", trades_api.SyncProgress(step="Idle", percent=0.0, done=True)
    )

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
    config.hysa_rates.cache_dir.mkdir(parents=True)
    write_csv_atomic(
        pl.DataFrame({
            "bank_id": ["ally-bank", "marcus"],
            "bank_name": ["Ally Bank", "Marcus"],
            "rate_date": [date(2026, 1, 1), date(2026, 1, 1)],
            "apy_pct": [4.0, 4.1],
        }),
        config.hysa_rates.cache_dir / "rates.csv",
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
    monkeypatch.setattr(trades_api.app.state, "config", AppConfig(ibkr={"cache_dir": tmp_path / "empty"}))
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


def test_cash_history_returns_one_entry_per_day(client) -> None:
    body = client.get("/api/chart/cash-history", params={"start": "2026-01-01", "end": "2026-01-03"}).json()
    assert [row["cash"] for row in body] == pytest.approx([2000.0, 1000.0, 1550.0])


def test_cash_history_includes_a_benchmark_counterfactual_for_cash_received(client) -> None:
    # Jan 1: $2000 arrives as one lot. Jan 2: a $1000 real BUY consumes
    # $1000 of that lot at an unchanged price -> $0 banked, $1000 stays
    # open. Jan 3: the price rises to 560 and a $550 SELL creates a second,
    # separate lot -> live value is both lots' current worth: 1000*(560/500)
    # + 550*(560/560) = 1670; nothing further gets consumed, so realized stays 0.
    body = client.get("/api/chart/cash-history", params={"start": "2026-01-01", "end": "2026-01-03"}).json()
    assert [row["benchmark_live_usd"] for row in body] == pytest.approx([2000.0, 1000.0, 1670.0])
    assert [row["benchmark_realized_usd"] for row in body] == pytest.approx([0.0, 0.0, 0.0])
    assert all(row["hysa_live_usd"] > 0 for row in body)


def test_statements_export_returns_a_zip_of_every_archived_flex_statement(client, isolated_config) -> None:
    raw_dir = isolated_config.ibkr.raw_statement_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "2026-01-01T00-00-00.xml").write_text("<FlexQueryResponse />")

    response = client.get("/api/statements/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    zip_file = zipfile.ZipFile(io.BytesIO(response.content))
    assert zip_file.namelist() == ["2026-01-01T00-00-00.xml"]


def test_cash_sitting_reports_current_balance_and_when_it_last_dropped(client) -> None:
    body = client.get("/api/cash-sitting").json()
    assert body["cash_usd"] == pytest.approx(1550.0)
    # $1000 of the original Jan 1 lot is still open (Jan 2's BUY only
    # consumed $1000 of it); Jan 3's SELL proceeds are a separate, newer
    # lot -- the oldest *open* dollar has been sitting since Jan 1.
    assert body["sitting_since"] == "2026-01-01"
    assert body["warning_level"] == "heavy"


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


def test_target_allocation_put_preserves_other_settings(client) -> None:
    client.put("/api/settings/hysa", json={"bank_id": "marcus"})
    client.put("/api/settings/target-allocation", json={"VOO": 80.0})
    assert client.get("/api/settings/hysa").json()["bank_id"] == "marcus"


def test_hysa_settings_default_to_no_override(client) -> None:
    assert client.get("/api/settings/hysa").json() == {"bank_id": None, "fixed_rate_pct": None}


def test_hysa_settings_put_then_get_round_trips(client) -> None:
    put_response = client.put("/api/settings/hysa", json={"bank_id": "marcus", "fixed_rate_pct": None})
    assert put_response.status_code == 200
    assert client.get("/api/settings/hysa").json() == {"bank_id": "marcus", "fixed_rate_pct": None}


def test_hysa_settings_put_preserves_target_allocation(client) -> None:
    client.put("/api/settings/target-allocation", json={"VOO": 80.0})
    client.put("/api/settings/hysa", json={"fixed_rate_pct": 5.0})
    assert client.get("/api/settings/target-allocation").json() == {"VOO": 80.0}


def test_benchmark_setting_defaults_to_no_override(client) -> None:
    body = client.get("/api/settings/benchmark").json()
    assert body["symbol_override"] is None
    assert body["default_symbol"]


def test_benchmark_setting_put_then_get_round_trips(client) -> None:
    put_response = client.put("/api/settings/benchmark", json={"symbol_override": "QQQ"})
    assert put_response.status_code == 200
    assert client.get("/api/settings/benchmark").json()["symbol_override"] == "QQQ"


def test_tax_settings_default_to_disabled_and_resident(client) -> None:
    body = client.get("/api/settings/tax").json()
    assert body == {
        "tax_enabled": False,
        "tax_regime": None,
        "resolved_tax_regime": "RESIDENT",
        "residency_status_change_date": None,
        "w8ben_claimed": False,
        "w8ben_treaty_rate_pct": None,
        "marginal_ordinary_rate_pct": None,
        "resolved_marginal_ordinary_rate_pct": pytest.approx(24.0),
        "qualified_ltcg_rate_pct": None,
        "resolved_qualified_ltcg_rate_pct": pytest.approx(15.0),
    }


def test_tax_settings_put_then_get_round_trips(client) -> None:
    put_response = client.put(
        "/api/settings/tax",
        json={
            "tax_enabled": True,
            "tax_regime": "NRA",
            "residency_status_change_date": "2025-10-01",
            "w8ben_claimed": True,
            "w8ben_treaty_rate_pct": 15.0,
            "marginal_ordinary_rate_pct": 32.0,
            "qualified_ltcg_rate_pct": 20.0,
        },
    )
    assert put_response.status_code == 200
    body = client.get("/api/settings/tax").json()
    assert body == {
        "tax_enabled": True,
        "tax_regime": "NRA",
        "resolved_tax_regime": "NRA",
        "residency_status_change_date": "2025-10-01",
        "w8ben_claimed": True,
        "w8ben_treaty_rate_pct": pytest.approx(15.0),
        "marginal_ordinary_rate_pct": pytest.approx(32.0),
        "resolved_marginal_ordinary_rate_pct": pytest.approx(32.0),
        "qualified_ltcg_rate_pct": pytest.approx(20.0),
        "resolved_qualified_ltcg_rate_pct": pytest.approx(20.0),
    }


def test_tax_settings_put_preserves_target_allocation(client) -> None:
    client.put("/api/settings/target-allocation", json={"VOO": 80.0})
    client.put(
        "/api/settings/tax",
        json={
            "tax_enabled": True,
            "tax_regime": None,
            "residency_status_change_date": None,
            "w8ben_claimed": False,
            "w8ben_treaty_rate_pct": None,
            "marginal_ordinary_rate_pct": None,
            "qualified_ltcg_rate_pct": None,
        },
    )
    assert client.get("/api/settings/target-allocation").json() == {"VOO": 80.0}


def test_ibkr_settings_default_to_no_override(client) -> None:
    body = client.get("/api/settings/ibkr").json()
    assert body["token_set"] is False
    assert body["query_id_set"] is False


def test_ibkr_settings_put_then_get_round_trips(client) -> None:
    put_response = client.put("/api/settings/ibkr", json={"token": "my-token", "query_id": "99999"})
    assert put_response.status_code == 200
    assert put_response.json() == {"configured": True, "token_set": True, "query_id_set": True}
    assert client.get("/api/settings/ibkr").json() == {"configured": True, "token_set": True, "query_id_set": True}


def test_ibkr_settings_put_merges_a_partial_update(client) -> None:
    client.put("/api/settings/ibkr", json={"token": "my-token"})
    client.put("/api/settings/ibkr", json={"query_id": "99999"})
    body = client.get("/api/settings/ibkr").json()
    assert body["token_set"] is True
    assert body["query_id_set"] is True


def test_ibkr_settings_delete_clears_the_override(client) -> None:
    client.put("/api/settings/ibkr", json={"token": "my-token", "query_id": "99999"})
    delete_response = client.delete("/api/settings/ibkr")
    assert delete_response.status_code == 200
    body = client.get("/api/settings/ibkr").json()
    assert body["token_set"] is False
    assert body["query_id_set"] is False


def test_tax_report_returns_the_realized_gain_and_an_open_lot_preview(client) -> None:
    body = client.get("/api/tax/report", params={"as_of": "2026-01-03"}).json()
    annual_row = next(row for row in body["annual"] if row["year"] == 2026)
    assert annual_row["short_term_gain_usd"] == pytest.approx(50.0)
    assert body["wash_sales"] == []
    assert len(body["sale_previews"]) == 1
    assert body["sale_previews"][0]["symbol"] == "VOO"


def test_tax_report_includes_tax_owed_and_liquidation_value(client) -> None:
    body = client.get("/api/tax/report", params={"as_of": "2026-01-03"}).json()
    owed_row = next(row for row in body["tax_owed"] if row["year"] == 2026)
    assert owed_row["capital_gains_tax_usd"] == pytest.approx(50.0 * 0.24)
    assert "balance_due_usd" in owed_row
    assert body["liquidation_pretax_value_usd"] - body["liquidation_capital_gains_tax_usd"] == pytest.approx(
        body["liquidation_value_usd"]
    )
    assert body["liquidation_long_term_gain_usd"] >= 0
    assert body["liquidation_short_term_gain_usd"] >= 0


def test_hysa_rates_lists_banks_and_history(client) -> None:
    body = client.get("/api/hysa-rates").json()
    bank_ids = {bank["bank_id"] for bank in body["banks"]}
    assert bank_ids == {"ally-bank", "marcus"}
    assert len(body["history"]) == 2
    assert body["default_bank_id"]


def test_symbol_search_passes_the_query_through(client, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        trades_api.symbol_search_module,
        "search_symbols",
        lambda query, config: (
            calls.append(query) or [{"symbol": "VOO", "name": "Vanguard S&P 500", "exchange": "NYSE"}]
        ),
    )
    body = client.get("/api/symbols/search", params={"q": "voo"}).json()
    assert calls == ["voo"]
    assert body == [{"symbol": "VOO", "name": "Vanguard S&P 500", "exchange": "NYSE"}]


def test_ensure_symbol_priced_refreshes_raw_and_adjusted_caches(client, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(trades_api.prices, "load_price_cache", lambda symbol, config: pl.DataFrame())
    monkeypatch.setattr(
        trades_api.prices,
        "update_price_cache",
        lambda symbol, since, as_of, config, adjusted=False: (
            calls.append(adjusted) or pl.DataFrame({"price_date": [date(2026, 1, 5)], "close": [123.0]})
        ),
    )

    body = client.post("/api/symbols/AAPL/ensure-priced").json()

    assert calls == [False, True]
    assert body == {"symbol": "AAPL", "was_stale": True, "last_price_date": "2026-01-05"}


def test_ensure_symbol_priced_reports_not_stale_when_cache_already_covers_today(client, monkeypatch) -> None:
    today = datetime.now().date()
    cached = pl.DataFrame({"price_date": [today], "close": [500.0]})
    monkeypatch.setattr(trades_api.prices, "load_price_cache", lambda symbol, config: cached)
    monkeypatch.setattr(
        trades_api.prices, "update_price_cache", lambda symbol, since, as_of, config, adjusted=False: cached
    )

    body = client.post("/api/symbols/VOO/ensure-priced").json()

    assert body["was_stale"] is False


def test_ensure_symbol_priced_maps_unknown_symbol_to_422(client, monkeypatch) -> None:
    monkeypatch.setattr(trades_api.prices, "load_price_cache", lambda symbol, config: pl.DataFrame())

    def raise_unknown_symbol(symbol, since, as_of, config, adjusted=False):
        message = f"Yahoo chart API returned no data for {symbol}: not found"
        raise ValueError(message)

    monkeypatch.setattr(trades_api.prices, "update_price_cache", raise_unknown_symbol)

    response = client.post("/api/symbols/NOTASYMBOL/ensure-priced")
    assert response.status_code == 422


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

    def fake_sync(credentials, config, on_progress=None):
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
    hysa_rates_calls = []
    monkeypatch.setattr(trades_api.hysa_rates_module, "update_hysa_rates_cache", hysa_rates_calls.append)

    response = client.post("/api/sync")

    assert response.status_code == 200
    assert len(sync_calls) == 1
    assert raw_price_calls == [["VOO"]]
    assert adjusted_price_calls == [("VOO", True)]
    assert len(cpi_calls) == 1
    assert len(hysa_rates_calls) == 1
    body = response.json()
    assert body["symbols_refreshed"] == ["VOO"]


def test_sync_progress_defaults_to_idle_and_done(client) -> None:
    body = client.get("/api/sync/progress").json()
    assert body == {"step": "Idle", "percent": 0.0, "done": True, "error": None}


def test_sync_progress_reflects_done_after_a_successful_sync(client, monkeypatch) -> None:
    monkeypatch.setenv("IBKR_FLEX_WEB_SERVICE_TOKEN", "test-token")
    monkeypatch.setenv("IBKR_QUERY_ID", "12345")

    def fake_sync(credentials, config, on_progress=None):
        raw_dir = config.ibkr.raw_statement_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "20260104T000000.xml").write_text("<FlexQueryResponse />", encoding="utf-8")
        return IbkrSyncResult(
            pulled_at=datetime(2026, 1, 4),
            statement_from_date=date(2026, 1, 4),
            statement_to_date=date(2026, 1, 4),
            new_event_count=0,
            total_event_count=3,
        )

    monkeypatch.setattr(trades_api.main, "sync_ibkr_account", fake_sync)
    monkeypatch.setattr(trades_api.prices, "update_price_caches", lambda symbols, since, as_of, config: {})
    monkeypatch.setattr(
        trades_api.prices,
        "update_price_cache",
        lambda symbol, since, as_of, config, adjusted=False: None,
    )
    monkeypatch.setattr(trades_api.cpi_module, "update_cpi_cache", lambda config: None)
    monkeypatch.setattr(trades_api.hysa_rates_module, "update_hysa_rates_cache", lambda config: None)

    client.post("/api/sync")

    body = client.get("/api/sync/progress").json()
    assert body == {"step": "Done", "percent": 100.0, "done": True, "error": None}


def test_sync_progress_reflects_failure_and_still_raises(client, monkeypatch) -> None:
    monkeypatch.setenv("IBKR_FLEX_WEB_SERVICE_TOKEN", "test-token")
    monkeypatch.setenv("IBKR_QUERY_ID", "12345")

    def failing_sync(credentials, config, on_progress=None):
        message = "IBKR Flex API error 1018: too many requests"
        raise ValueError(message)

    monkeypatch.setattr(trades_api.main, "sync_ibkr_account", failing_sync)

    with pytest.raises(ValueError, match="too many requests"):
        client.post("/api/sync")

    body = client.get("/api/sync/progress").json()
    assert body["done"] is True
    assert body["error"] is not None
