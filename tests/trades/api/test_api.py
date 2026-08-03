import io
import uuid
import zipfile
from datetime import UTC, date, datetime

import polars as pl
import pytest
from fastapi.testclient import TestClient

import db.models as dbm
from sqlalchemy import text
from db.session import get_db
from http_api.pagination import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX
from tests.conftest import DEFAULT_USER_ID
from trades import api as trades_api
from trades.brokers.ibkr.main import _write_ledger
from trades.config import AppConfig
from trades.models import LedgerEvent
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
        "meta": {},
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
        "meta": {},
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
        "meta": {},
    },
]


@pytest.fixture(autouse=True)
def _db_for_api(db_session):
    """Route every request the `TestClient` makes through this test's own rolled-back session.

    See `tests/accounting/api/test_api.py`'s `_db_for_api` fixture of the
    same name — `tests/conftest.py`'s `_bypass_clerk_auth_by_default`
    overrides `get_current_user_id` to `DEFAULT_USER_ID` for every test by
    default, so the one `User` row FK-satisfying `ledger_events`/
    `broker_connections` has to exist under that exact id.
    """
    db_session.add(dbm.User(id=DEFAULT_USER_ID, email="default@example.com"))
    db_session.commit()

    def _override_get_db():
        yield db_session

    trades_api.app.dependency_overrides[get_db] = _override_get_db
    yield
    trades_api.app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch, db_session):
    """Point the module-level config at a throwaway cache dir.

    Seeds a minimal local ledger + price + CPI history so GET endpoints
    never touch the network (per api.py's read-only GET contract).
    """
    config = AppConfig(
        ibkr={"cache_dir": tmp_path / "ibkr"},
        prices={"cache_dir": tmp_path / "prices"},
        cpi={"cache_dir": tmp_path / "cpi"},
        hysa_rates={"cache_dir": tmp_path / "hysa_rates"},
    )
    monkeypatch.setattr(trades_api.app.state, "config", config)

    _write_ledger(pl.DataFrame(LEDGER_ROWS, schema=LedgerEvent.polars_schema), db_session, user_id=DEFAULT_USER_ID)
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
    response = client.get("/api/v1/trades/overview", params={"as_of": "2026-01-03"})
    assert response.status_code == 200
    body = response.json()
    assert body["value_usd"] == pytest.approx(1 * 560.0 + 1550.0)
    assert body["realized_gain_usd"] == pytest.approx(50.0)


def test_overview_no_ledger_is_a_404(client, db_session) -> None:
    # isolated_config's autouse fixture always seeds a ledger — undo that
    # for this one test by overwriting it with nothing.
    _write_ledger(pl.DataFrame(schema=LedgerEvent.polars_schema), db_session, user_id=DEFAULT_USER_ID)
    assert client.get("/api/v1/trades/overview").status_code == 404


def test_dollar_chart_returns_series_and_reallocation_markers(client) -> None:
    body = client.get("/api/v1/trades/chart/dollar", params={"start": "2026-01-01", "end": "2026-01-03"}).json()
    assert len(body["series"]) == 3
    assert body["series"][0]["date"] == "2026-01-01"
    assert body["reallocation_markers"] == []


def test_growth_of_100_chart_returns_one_entry_per_day(client) -> None:
    body = client.get("/api/v1/trades/chart/growth-of-100", params={"start": "2026-01-01", "end": "2026-01-03"}).json()
    assert len(body) == 3
    assert body[0]["portfolio_index"] == pytest.approx(100.0)


def test_cash_history_returns_one_entry_per_day(client) -> None:
    body = client.get("/api/v1/trades/chart/cash-history", params={"start": "2026-01-01", "end": "2026-01-03"}).json()
    assert [row["cash"] for row in body] == pytest.approx([2000.0, 1000.0, 1550.0])


def test_cash_history_includes_a_benchmark_counterfactual_for_cash_received(client) -> None:
    # Jan 1: $2000 arrives as one lot. Jan 2: a $1000 real BUY consumes
    # $1000 of that lot at an unchanged price -> $0 banked, $1000 stays
    # open. Jan 3: the price rises to 560 and a $550 SELL creates a second,
    # separate lot -> live value is both lots' current worth: 1000*(560/500)
    # + 550*(560/560) = 1670; nothing further gets consumed, so realized stays 0.
    body = client.get("/api/v1/trades/chart/cash-history", params={"start": "2026-01-01", "end": "2026-01-03"}).json()
    assert [row["benchmark_live_usd"] for row in body] == pytest.approx([2000.0, 1000.0, 1670.0])
    assert [row["benchmark_realized_usd"] for row in body] == pytest.approx([0.0, 0.0, 0.0])
    assert all(row["hysa_live_usd"] > 0 for row in body)


def test_statements_export_returns_a_zip_of_every_archived_flex_statement(client, isolated_config) -> None:
    raw_dir = isolated_config.ibkr.raw_statement_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "2026-01-01T00-00-00.xml").write_text("<FlexQueryResponse />")

    response = client.get("/api/v1/trades/statements/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    zip_file = zipfile.ZipFile(io.BytesIO(response.content))
    assert zip_file.namelist() == ["2026-01-01T00-00-00.xml"]


def test_cash_sitting_reports_current_balance_and_when_it_last_dropped(client) -> None:
    body = client.get("/api/v1/trades/cash-sitting").json()
    assert body["cash_usd"] == pytest.approx(1550.0)
    # $1000 of the original Jan 1 lot is still open (Jan 2's BUY only
    # consumed $1000 of it); Jan 3's SELL proceeds are a separate, newer
    # lot -- the oldest *open* dollar has been sitting since Jan 1.
    assert body["sitting_since"] == "2026-01-01"
    assert body["warning_level"] == "heavy"


def test_monthly_pnl_returns_one_entry_for_january(client) -> None:
    body = client.get("/api/v1/trades/chart/monthly-pnl", params={"start": "2026-01-01", "end": "2026-01-03"}).json()
    assert len(body) == 1
    assert body[0]["month"] == "2026-01"


def test_monthly_pnl_by_symbol_returns_one_row_per_symbol_per_month(client) -> None:
    body = client.get(
        "/api/v1/trades/chart/monthly-pnl/by-symbol", params={"start": "2026-01-01", "end": "2026-01-03"}
    ).json()
    symbols = {row["symbol"] for row in body}
    assert symbols == {"VOO", "CASH"}


def test_allocation_reports_voo_and_cash(client) -> None:
    body = client.get("/api/v1/trades/allocation", params={"as_of": "2026-01-03"}).json()
    symbols = {row["symbol"] for row in body}
    assert symbols == {"VOO", "CASH"}


def test_target_allocation_defaults_to_empty(client) -> None:
    assert client.get("/api/v1/trades/settings/target-allocation").json() == {}


def _patch_target_allocation(client, patch: dict[str, float | None]):
    """Send one RFC 7386 merge patch to the target allocation, with the media type the route declares.

    Returns
    -------
    httpx.Response
    """
    return client.patch(
        "/api/v1/trades/settings/target-allocation",
        json=patch,
        headers={"Content-Type": "application/merge-patch+json"},
    )


def test_target_allocation_patch_then_get_round_trips(client) -> None:
    """The endpoint returns the bare `symbol -> pct` map — no envelope, since there is no version to carry."""
    response = _patch_target_allocation(client, {"VOO": 80.0})
    assert response.status_code == 200
    assert response.json() == {"VOO": 80.0}
    assert client.get("/api/v1/trades/settings/target-allocation").json() == {"VOO": 80.0}


def test_target_allocation_patch_sets_one_symbol_and_leaves_the_rest_alone(client) -> None:
    """The point of the merge patch: editing one symbol is not a chance to clobber the others.

    A whole-map `PUT` made every save a full replacement, so a client
    holding a stale map silently reverted anything saved since it read.
    """
    _patch_target_allocation(client, {"VOO": 60.0, "BND": 30.0, "CASH": 10.0})

    response = _patch_target_allocation(client, {"BND": 25.0})

    assert response.json() == {"VOO": 60.0, "BND": 25.0, "CASH": 10.0}


def test_target_allocation_patch_removes_a_symbol_set_to_null(client) -> None:
    _patch_target_allocation(client, {"VOO": 60.0, "BND": 40.0})

    response = _patch_target_allocation(client, {"BND": None})

    assert response.json() == {"VOO": 60.0}
    assert client.get("/api/v1/trades/settings/target-allocation").json() == {"VOO": 60.0}


def test_target_allocation_patch_ignores_a_null_for_a_symbol_that_was_never_set(client) -> None:
    """Removing what isn't there is a no-op, not a 404 — merge-patch says nothing about absent keys."""
    _patch_target_allocation(client, {"VOO": 60.0})

    response = _patch_target_allocation(client, {"NVDA": None})

    assert response.status_code == 200
    assert response.json() == {"VOO": 60.0}


def test_target_allocation_patch_with_an_empty_body_changes_nothing(client) -> None:
    _patch_target_allocation(client, {"VOO": 60.0})

    response = _patch_target_allocation(client, {})

    assert response.json() == {"VOO": 60.0}


def test_target_allocation_patch_preserves_other_settings(client) -> None:
    client.put("/api/v1/trades/settings/hysa", json={"bank_id": "marcus"})
    _patch_target_allocation(client, {"VOO": 80.0})
    assert client.get("/api/v1/trades/settings/hysa").json()["bank_id"] == "marcus"


def test_a_settings_save_never_conflicts_with_a_sibling_settings_save(client) -> None:
    """Two different settings panels writing the same one row is last-write-wins, never a 409.

    The shared settings-row counter is gone (see `trades.dashboard.settings.save_settings`), so a
    target-allocation save followed by an unrelated hysa save just both land.
    """
    assert _patch_target_allocation(client, {"VOO": 80.0}).status_code == 200

    followup = client.put("/api/v1/trades/settings/hysa", json={"bank_id": "marcus", "fixed_rate_pct": None})
    assert followup.status_code == 200
    assert client.get("/api/v1/trades/settings/target-allocation").json() == {"VOO": 80.0}


def test_hysa_settings_default_to_no_override(client) -> None:
    assert client.get("/api/v1/trades/settings/hysa").json() == {"bank_id": None, "fixed_rate_pct": None}


def test_hysa_settings_put_then_get_round_trips(client) -> None:
    put_response = client.put("/api/v1/trades/settings/hysa", json={"bank_id": "marcus", "fixed_rate_pct": None})
    assert put_response.status_code == 200
    assert client.get("/api/v1/trades/settings/hysa").json() == {"bank_id": "marcus", "fixed_rate_pct": None}


def test_hysa_settings_put_preserves_target_allocation(client) -> None:
    _patch_target_allocation(client, {"VOO": 80.0})
    client.put("/api/v1/trades/settings/hysa", json={"fixed_rate_pct": 5.0})
    assert client.get("/api/v1/trades/settings/target-allocation").json() == {"VOO": 80.0}


def test_a_settings_save_after_an_unrelated_one_still_succeeds(client) -> None:
    """The case the shared counter used to 409: an unrelated save landing between a read and a write."""
    client.get("/api/v1/trades/settings/hysa")
    # Someone else's save lands first.
    client.put("/api/v1/trades/settings/benchmark", json={"symbol_override": "QQQ"})

    response = client.put("/api/v1/trades/settings/hysa", json={"bank_id": "marcus", "fixed_rate_pct": None})
    assert response.status_code == 200
    assert client.get("/api/v1/trades/settings/benchmark").json()["symbol_override"] == "QQQ"


def test_benchmark_setting_defaults_to_no_override(client) -> None:
    body = client.get("/api/v1/trades/settings/benchmark").json()
    assert body["symbol_override"] is None
    assert body["default_symbol"]


def test_benchmark_setting_put_then_get_round_trips(client) -> None:
    put_response = client.put("/api/v1/trades/settings/benchmark", json={"symbol_override": "QQQ"})
    assert put_response.status_code == 200
    assert client.get("/api/v1/trades/settings/benchmark").json()["symbol_override"] == "QQQ"


def test_tax_settings_default_to_disabled_and_resident(client) -> None:
    body = client.get("/api/v1/trades/settings/tax").json()
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
        "/api/v1/trades/settings/tax",
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
    body = client.get("/api/v1/trades/settings/tax").json()
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
    _patch_target_allocation(client, {"VOO": 80.0})
    client.put(
        "/api/v1/trades/settings/tax",
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
    assert client.get("/api/v1/trades/settings/target-allocation").json() == {"VOO": 80.0}


def test_ibkr_settings_default_to_no_override(client) -> None:
    body = client.get("/api/v1/trades/settings/ibkr").json()
    assert body["token_set"] is False
    assert body["query_id_set"] is False


def test_ibkr_settings_put_then_get_round_trips(client) -> None:
    put_response = client.put("/api/v1/trades/settings/ibkr", json={"token": "my-token", "query_id": "99999"})
    assert put_response.status_code == 200
    assert put_response.json() == {"configured": True, "token_set": True, "query_id_set": True}
    assert client.get("/api/v1/trades/settings/ibkr").json() == {
        "configured": True,
        "token_set": True,
        "query_id_set": True,
    }


def test_ibkr_settings_put_merges_a_partial_update(client) -> None:
    client.put("/api/v1/trades/settings/ibkr", json={"token": "my-token"})
    client.put("/api/v1/trades/settings/ibkr", json={"query_id": "99999"})
    body = client.get("/api/v1/trades/settings/ibkr").json()
    assert body["token_set"] is True
    assert body["query_id_set"] is True


def test_ibkr_settings_delete_clears_the_override(client) -> None:
    client.put("/api/v1/trades/settings/ibkr", json={"token": "my-token", "query_id": "99999"})
    delete_response = client.delete("/api/v1/trades/settings/ibkr")
    assert delete_response.status_code == 200
    body = client.get("/api/v1/trades/settings/ibkr").json()
    assert body["token_set"] is False
    assert body["query_id_set"] is False


def test_tax_report_returns_the_realized_gain_and_an_open_lot_preview(client) -> None:
    body = client.get("/api/v1/trades/tax/report", params={"as_of": "2026-01-03"}).json()
    annual_row = next(row for row in body["annual"] if row["year"] == 2026)
    assert annual_row["short_term_gain_usd"] == pytest.approx(50.0)
    assert body["wash_sales"] == []
    assert len(body["sale_previews"]) == 1
    assert body["sale_previews"][0]["symbol"] == "VOO"


def test_tax_report_includes_tax_owed_and_liquidation_value(client) -> None:
    body = client.get("/api/v1/trades/tax/report", params={"as_of": "2026-01-03"}).json()
    owed_row = next(row for row in body["tax_owed"] if row["year"] == 2026)
    assert owed_row["capital_gains_tax_usd"] == pytest.approx(50.0 * 0.24)
    assert "balance_due_usd" in owed_row
    assert body["liquidation_pretax_value_usd"] - body["liquidation_capital_gains_tax_usd"] == pytest.approx(
        body["liquidation_value_usd"]
    )
    assert body["liquidation_long_term_gain_usd"] >= 0
    assert body["liquidation_short_term_gain_usd"] >= 0


def test_hysa_rates_lists_banks_and_history(client) -> None:
    body = client.get("/api/v1/trades/hysa-rates").json()
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
    body = client.get("/api/v1/trades/symbols/search", params={"q": "voo"}).json()
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

    body = client.post("/api/v1/trades/symbols/AAPL/ensure-priced").json()

    assert calls == [False, True]
    assert body == {"symbol": "AAPL", "was_stale": True, "last_price_date": "2026-01-05"}


def test_ensure_symbol_priced_reports_not_stale_when_cache_already_covers_today(client, monkeypatch) -> None:
    # Must match the endpoint's own UTC "today" (src/trades/api.py's `ensure_symbol_priced`,
    # and every other `as_of`-default in this codebase) — a naive local `datetime.now()`
    # can land on the previous UTC day depending on machine timezone/time of day.
    today = datetime.now(UTC).date()
    cached = pl.DataFrame({"price_date": [today], "close": [500.0]})
    monkeypatch.setattr(trades_api.prices, "load_price_cache", lambda symbol, config: cached)
    monkeypatch.setattr(
        trades_api.prices, "update_price_cache", lambda symbol, since, as_of, config, adjusted=False: cached
    )

    body = client.post("/api/v1/trades/symbols/VOO/ensure-priced").json()

    assert body["was_stale"] is False


def test_ensure_symbol_priced_maps_unknown_symbol_to_422(client, monkeypatch) -> None:
    monkeypatch.setattr(trades_api.prices, "load_price_cache", lambda symbol, config: pl.DataFrame())

    def raise_unknown_symbol(symbol, since, as_of, config, adjusted=False):
        message = f"Yahoo chart API returned no data for {symbol}: not found"
        raise ValueError(message)

    monkeypatch.setattr(trades_api.prices, "update_price_cache", raise_unknown_symbol)

    response = client.post("/api/v1/trades/symbols/NOTASYMBOL/ensure-priced")
    assert response.status_code == 422


@pytest.mark.parametrize(("path", "window_unit"), [("open", "lot"), ("closed", "lot"), ("symbols", "symbol")])
def test_each_lot_collection_answers_with_the_page_envelope(client, path, window_unit) -> None:
    body = client.get(f"/api/v1/trades/lots/{path}", params={"as_of": "2026-01-03"}).json()
    assert len(body["items"]) == 1
    assert body["total"] == 1
    assert body["window_unit"] == window_unit
    assert body["offset"] == 0
    assert body["limit"] == PAGE_LIMIT_DEFAULT


@pytest.mark.parametrize("path", ["open", "closed", "symbols"])
def test_a_lot_page_clamps_an_oversized_limit_rather_than_rejecting_it(client, path) -> None:
    body = client.get(f"/api/v1/trades/lots/{path}", params={"limit": PAGE_LIMIT_MAX * 2}).json()
    assert body["limit"] == PAGE_LIMIT_MAX


@pytest.mark.parametrize("path", ["open", "closed", "symbols"])
def test_a_lot_page_past_the_end_of_the_collection_is_empty_rather_than_an_error(client, path) -> None:
    """What ends a client's page walk: the total keeps counting the whole collection."""
    body = client.get(f"/api/v1/trades/lots/{path}", params={"offset": 500}).json()
    assert body["items"] == []
    assert body["total"] == 1


def test_the_lot_pages_compose_into_the_whole_collection(client) -> None:
    """Walking one lot at a time must reconstruct the collection exactly, in the same order.

    A page's window is a slice of a sort the server chose, so this is the
    property that says the two agree — a per-page order that differs from the
    whole-collection order both repeats and drops lots, silently, and a lots
    table is a tax figure.
    """
    for path in ("open", "closed", "symbols"):
        whole = client.get(f"/api/v1/trades/lots/{path}", params={"limit": PAGE_LIMIT_MAX}).json()["items"]
        walked = []
        offset = 0
        while True:
            page = client.get(f"/api/v1/trades/lots/{path}", params={"limit": 1, "offset": offset}).json()
            walked.extend(page["items"])
            offset += page["limit"]
            if offset >= page["total"]:
                break
        assert walked == whole, f"/lots/{path} pages do not compose into the whole collection"


def test_risk_reports_max_drawdown(client) -> None:
    body = client.get("/api/v1/trades/risk", params={"start": "2026-01-01", "end": "2026-01-03"}).json()
    assert body["max_drawdown_pct"] <= 0.0


def test_data_quality_includes_held_and_benchmark_symbols(client) -> None:
    body = client.get("/api/v1/trades/data-quality").json()
    symbols = {row["symbol"] for row in body}
    assert symbols == {"VOO"}
    assert body[0]["last_price_date"] == "2026-01-03"


def test_ledger_export_returns_every_row(client) -> None:
    body = client.get("/api/v1/trades/ledger/export").json()

    assert len(body["items"]) == len(LEDGER_ROWS)
    assert body["total"] == len(LEDGER_ROWS)
    assert body["window_unit"] == "event"
    assert body["offset"] == 0


def test_ledger_export_pages_compose_into_the_whole_ledger(client) -> None:
    """Walking the pages must reconstruct the export exactly — the property a backup depends on.

    Asserted as an ordered list rather than a set: an export whose pages
    each hold the right rows but in a shuffled order is still a corrupt
    backup, and a page boundary is exactly where a disagreement between the
    windowed read's `ORDER BY` and the full read's would show up.
    """
    whole = client.get("/api/v1/trades/ledger/export", params={"limit": PAGE_LIMIT_MAX}).json()["items"]

    walked = []
    offset = 0
    while True:
        page = client.get("/api/v1/trades/ledger/export", params={"limit": 1, "offset": offset}).json()
        walked.extend(page["items"])
        offset += page["limit"]
        if offset >= page["total"]:
            break

    assert walked == whole
    assert [event["event_id"] for event in walked] == [row["event_id"] for row in LEDGER_ROWS]


def test_ledger_export_clamps_an_oversized_limit_rather_than_rejecting_it(client) -> None:
    body = client.get("/api/v1/trades/ledger/export", params={"limit": PAGE_LIMIT_MAX + 1}).json()

    assert body["limit"] == PAGE_LIMIT_MAX


def test_ledger_export_past_the_end_is_an_empty_page_not_an_error(client) -> None:
    """`total` still has to be the real total, or a client cannot tell it has finished from an error."""
    body = client.get("/api/v1/trades/ledger/export", params={"offset": len(LEDGER_ROWS)}).json()

    assert body["items"] == []
    assert body["total"] == len(LEDGER_ROWS)


@pytest.fixture
def captured_jobs(monkeypatch):
    """Hold whatever `POST /sync-runs` hands the runner, instead of running it on a thread.

    These are route tests: what they are about is the `202`, the `Location`
    and the run row, not the pull. The runner's own behaviour — partial
    success, a crash closing the run, the restart sweep — needs sessions that
    really commit and is covered in `tests/trades/api/test_sync_runs.py`.

    Returns
    -------
    list
        One entry per submitted job.
    """
    jobs = []
    monkeypatch.setattr(trades_api.sync_runs.runner, "submit", jobs.append)
    return jobs


def test_starting_a_sync_answers_202_with_the_run_it_created(client, captured_jobs) -> None:
    """`202`, not `200`: nothing has been pulled when this returns.

    The `Location` is the whole contract — a `202` with no address to poll
    is a dead end — so it is asserted to be a real, followable URL rather
    than merely present.
    """
    response = client.post("/api/v1/trades/sync-runs")

    assert response.status_code == 202
    body = response.json()
    assert body["state"] == "queued"
    assert body["percent"] == pytest.approx(0.0)
    assert body["steps"] == []
    assert response.headers["Location"].endswith(f"/api/v1/trades/sync-runs/{body['id']}")
    assert len(captured_jobs) == 1

    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json()["id"] == body["id"]


def test_a_second_sync_while_one_is_running_is_a_409_naming_the_run_in_flight(client, captured_jobs) -> None:
    """The refusal comes from `uq_sync_runs_active_user`, not from a check in the handler.

    A per-process lock could only ever serialize the worker it lived in. The
    `Location` on the 409 is what makes this useful rather than merely
    correct: a client that lost its run id (a browser reload mid-sync) gets
    handed it back.
    """
    first = client.post("/api/v1/trades/sync-runs")

    second = client.post("/api/v1/trades/sync-runs")

    assert second.status_code == 409
    assert second.headers["Location"].endswith(f"/api/v1/trades/sync-runs/{first.json()['id']}")
    assert len(captured_jobs) == 1, "the refused request must not also have started a sync"


def test_a_finished_run_frees_the_slot_for_the_next_one(client, captured_jobs, db_session) -> None:
    """`uq_sync_runs_active_user` is partial, so only `queued`/`running` occupy the slot."""
    first = client.post("/api/v1/trades/sync-runs").json()
    db_session.execute(text("UPDATE trades.sync_runs SET state = 'succeeded' WHERE id = :id"), {"id": first["id"]})
    db_session.commit()

    second = client.post("/api/v1/trades/sync-runs")

    assert second.status_code == 202
    assert second.json()["id"] != first["id"]
    assert len(captured_jobs) == 2


def test_a_run_that_does_not_exist_is_a_404(client) -> None:
    assert client.get(f"/api/v1/trades/sync-runs/{uuid.uuid4()}").status_code == 404


def test_listing_runs_returns_them_newest_first_on_the_shared_envelope(client, captured_jobs, db_session) -> None:
    """The list is what lets a reload mid-sync find the run whose id it lost."""
    ids = []
    for _ in range(3):
        ids.append(client.post("/api/v1/trades/sync-runs").json()["id"])
        db_session.execute(text("UPDATE trades.sync_runs SET state = 'succeeded'"))
        db_session.commit()

    body = client.get("/api/v1/trades/sync-runs").json()

    assert body["window_unit"] == "run"
    assert body["total"] == 3
    # Descending id, not reversed insertion order. `recent_runs` sorts on the
    # primary key because it is a UUIDv7 and therefore time-ordered — but only
    # across milliseconds. Three rows minted inside one tick differ in their
    # random tail, so asserting insertion order would flake on a fast machine
    # while claiming to test the sort.
    assert [item["id"] for item in body["items"]] == sorted(ids, reverse=True)


def test_listing_runs_pages(client, captured_jobs, db_session) -> None:
    for _ in range(3):
        client.post("/api/v1/trades/sync-runs")
        db_session.execute(text("UPDATE trades.sync_runs SET state = 'succeeded'"))
        db_session.commit()

    body = client.get("/api/v1/trades/sync-runs", params={"limit": 2, "offset": 1}).json()

    assert len(body["items"]) == 2
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 1


def test_a_runner_that_will_not_take_the_job_closes_the_run_it_just_created(client, monkeypatch) -> None:
    """`start_run` commits before `submit`, so a failed handoff must not strand a `queued` row.

    `uq_sync_runs_active_user` counts `queued` as a sync in flight, so a
    stranded row blocks every future sync for that user until a restart
    sweeps it — a permanent consequence from a transient cause, and the
    likeliest cause is transient: `submit` raises once the pool is shut
    down, which a graceful shutdown really passes through while the server
    still accepts requests.

    Asserted as "the handler closes the run", not "the next POST succeeds":
    `fail_run` opens its own committed session, which cannot see a row this
    suite created inside the `db_session` fixture's rolled-back
    transaction. That the close genuinely frees the slot is
    `test_sync_runs.py`'s, against sessions that really commit.
    """
    closed: list[tuple[uuid.UUID, str]] = []

    def refuse(_job) -> None:
        message = "the sync runner is not started"
        raise RuntimeError(message)

    monkeypatch.setattr(trades_api.sync_runs.runner, "submit", refuse)
    monkeypatch.setattr(
        trades_api.sync_runs, "fail_run", lambda _user_id, run_id, error: closed.append((run_id, error))
    )

    response = client.post("/api/v1/trades/sync-runs")

    assert response.status_code == 503
    assert len(closed) == 1, "the queued row was left claiming the user's one sync slot"
