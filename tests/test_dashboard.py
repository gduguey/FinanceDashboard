from datetime import date, datetime

import polars as pl
import pytest

from trades.config import AppConfig
from trades.dashboard import (
    DashboardSettings,
    allocation_view,
    daily_portfolio_values,
    data_quality,
    dollar_chart_series,
    growth_of_100_chart,
    load_settings,
    lots_table,
    make_price_lookup,
    monthly_pnl,
    monthly_pnl_by_symbol,
    overview_cards,
    reallocation_markers,
    risk_stat,
    save_settings,
)
from trades.utils.io_utils import write_csv_atomic


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        dashboard={"settings_path": tmp_path / "dashboard_settings.json"},
        prices={"cache_dir": tmp_path},
        cpi={"cache_dir": tmp_path},
        hysa_rates={"cache_dir": tmp_path},
    )


def test_load_settings_with_no_file_yet_returns_defaults(tmp_path) -> None:
    settings = load_settings(_config(tmp_path))
    assert settings.target_allocation_pct == {}


def test_save_then_load_settings_round_trips(tmp_path) -> None:
    config = _config(tmp_path)
    save_settings(DashboardSettings(target_allocation_pct={"VOO": 60.0, "BND": 40.0}), config)
    assert load_settings(config).target_allocation_pct == {"VOO": 60.0, "BND": 40.0}


def _event(
    event_id: str,
    event_datetime: str,
    event_type: str,
    symbol: str = "CASH",
    shares: float | None = None,
    price: float | None = None,
    amount: float = 0.0,
) -> dict:
    return {
        "event_id": event_id,
        "event_datetime": datetime.fromisoformat(event_datetime),
        "symbol": symbol,
        "event_type": event_type,
        "shares": shares,
        "price": price,
        "amount": amount,
        "currency": "USD",
        "meta": {},
    }


def _ledger(*events: dict) -> pl.DataFrame:
    return pl.DataFrame(list(events))


def test_make_price_lookup_reads_the_raw_cache_by_default(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(pl.DataFrame({"price_date": [date(2026, 1, 1)], "close": [500.0]}), tmp_path / "VOO.csv")
    lookup = make_price_lookup(config)
    assert lookup("VOO", date(2026, 1, 2)) == pytest.approx(500.0)


def test_make_price_lookup_reads_the_adjusted_cache_when_requested(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(pl.DataFrame({"price_date": [date(2026, 1, 1)], "close": [500.0]}), tmp_path / "VOO.csv")
    write_csv_atomic(pl.DataFrame({"price_date": [date(2026, 1, 1)], "close": [510.0]}), tmp_path / "VOO.adjusted.csv")
    lookup = make_price_lookup(config, adjusted=True)
    assert lookup("VOO", date(2026, 1, 2)) == pytest.approx(510.0)


def test_make_price_lookup_returns_none_for_an_uncached_symbol(tmp_path) -> None:
    lookup = make_price_lookup(_config(tmp_path))
    assert lookup("BND", date(2026, 1, 2)) is None


def test_daily_portfolio_values_tracks_price_moves_on_an_unchanged_position(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 510.0]}),
        tmp_path / "VOO.csv",
    )
    lookup = make_price_lookup(config)
    result = daily_portfolio_values(ledger, lookup, date(2026, 1, 1), date(2026, 1, 2), config)
    assert result["value"].to_list() == pytest.approx([1000.0, 1020.0])
    assert result["date"].to_list() == [date(2026, 1, 1), date(2026, 1, 2)]


def test_daily_portfolio_values_before_any_ledger_activity_is_zero(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(_event("d1", "2026-01-02", "DEPOSIT", amount=1000.0))
    result = daily_portfolio_values(ledger, make_price_lookup(config), date(2026, 1, 1), date(2026, 1, 2), config)
    assert result["value"].to_list() == pytest.approx([0.0, 1000.0])


_OVERVIEW_LEDGER = _ledger(
    _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
    _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
)


def test_overview_cards_reports_value_and_gain_split(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)],
            "close": [500.0, 510.0, 520.0],
        }),
        tmp_path / "VOO.csv",
    )
    cards = overview_cards(_OVERVIEW_LEDGER, config, as_of=date(2026, 1, 3))

    assert cards.value_usd == pytest.approx(1040.0)
    assert cards.gain_usd == pytest.approx(40.0)
    assert cards.gain_pct == pytest.approx(4.0)
    assert cards.realized_gain_usd == pytest.approx(0.0)
    assert cards.unrealized_gain_usd == pytest.approx(40.0)


def test_overview_cards_marks_xirr_provisional_under_the_annualization_threshold(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)],
            "close": [500.0, 510.0, 520.0],
        }),
        tmp_path / "VOO.csv",
    )
    cards = overview_cards(_OVERVIEW_LEDGER, config, as_of=date(2026, 1, 3))

    assert cards.xirr_is_provisional is True
    assert cards.xirr_pct == pytest.approx((1040.0 / 1000.0) ** (365 / 2) * 100 - 100)


def test_overview_cards_reports_dollar_alpha_vs_hysa(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)],
            "close": [500.0, 510.0, 520.0],
        }),
        tmp_path / "VOO.csv",
    )
    cards = overview_cards(_OVERVIEW_LEDGER, config, as_of=date(2026, 1, 3))

    expected_hysa_value = 1000.0 * (1 + config.returns.hysa_annual_rate / 365) ** 2
    assert cards.dollar_alpha_vs_hysa_usd == pytest.approx(1040.0 - expected_hysa_value)


def test_overview_cards_twr_matches_value_growth_with_no_intermediate_flows(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)],
            "close": [500.0, 510.0, 520.0],
        }),
        tmp_path / "VOO.csv",
    )
    cards = overview_cards(_OVERVIEW_LEDGER, config, as_of=date(2026, 1, 3))

    assert cards.twr_pct == pytest.approx(4.0)
    assert cards.twr_annualized_pct is None
    assert cards.timing_gap_pct is None


def test_overview_cards_reports_gross_deposits_and_dividends(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
        _event("div1", "2026-01-02", "DIVIDEND", symbol="VOO", amount=5.0),
        _event("w1", "2026-01-02", "WITHDRAWAL", amount=200.0),
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 510.0]}),
        tmp_path / "VOO.csv",
    )
    cards = overview_cards(ledger, config, as_of=date(2026, 1, 2))

    assert cards.total_deposited_usd == pytest.approx(1000.0)
    assert cards.total_withdrawn_usd == pytest.approx(200.0)
    assert cards.total_dividends_usd == pytest.approx(5.0)


def test_reallocation_markers_flags_a_date_with_both_a_sell_and_a_buy(tmp_path) -> None:
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
        _event("s1", "2026-06-01", "SELL", symbol="VOO", shares=2.0, price=600.0, amount=1200.0),
        _event("b2", "2026-06-01", "BUY", symbol="BND", shares=2.0, price=600.0, amount=1200.0),
    )
    markers = reallocation_markers(ledger)
    assert markers["date"].to_list() == [date(2026, 6, 1)]
    assert markers["sold_symbols"].to_list() == [["VOO"]]
    assert markers["bought_symbols"].to_list() == [["BND"]]


def test_reallocation_markers_ignores_a_buy_only_date(tmp_path) -> None:
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
    )
    assert reallocation_markers(ledger).is_empty()


def test_dollar_chart_series_reports_the_four_lines(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 510.0]}),
        tmp_path / "VOO.csv",
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 520.0]}),
        tmp_path / "VOO.adjusted.csv",
    )

    result = dollar_chart_series(ledger, config, date(2026, 1, 1), date(2026, 1, 2))

    assert result["date"].to_list() == [date(2026, 1, 1), date(2026, 1, 2)]
    assert result["contributions_usd"].to_list() == pytest.approx([1000.0, 1000.0])
    assert result["portfolio_value_usd"].to_list() == pytest.approx([1000.0, 1020.0])
    assert result["benchmark_value_usd"].to_list() == pytest.approx([1000.0, 1040.0])
    expected_hysa_day2 = 1000.0 * (1 + config.returns.hysa_annual_rate / 365)
    assert result["hysa_value_usd"].to_list() == pytest.approx([1000.0, expected_hysa_day2])


def test_dollar_chart_series_uses_a_fixed_rate_override_for_hysa(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 510.0]}),
        tmp_path / "VOO.csv",
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 520.0]}),
        tmp_path / "VOO.adjusted.csv",
    )
    save_settings(DashboardSettings(hysa_fixed_rate_pct=10.0), config)

    result = dollar_chart_series(ledger, config, date(2026, 1, 1), date(2026, 1, 2))

    expected_hysa_day2 = 1000.0 * (1 + 0.10 / 365)
    assert result["hysa_value_usd"].to_list() == pytest.approx([1000.0, expected_hysa_day2])
    assert result["hysa_rate_pct"].to_list() == pytest.approx([10.0, 10.0])


def test_dollar_chart_series_uses_the_selected_bank_for_hysa(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 510.0]}),
        tmp_path / "VOO.csv",
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 520.0]}),
        tmp_path / "VOO.adjusted.csv",
    )
    write_csv_atomic(
        pl.DataFrame({
            "bank_id": ["some-bank"],
            "bank_name": ["Some Bank"],
            "rate_date": [date(2025, 1, 1)],
            "apy_pct": [8.0],
        }),
        tmp_path / "rates.csv",
    )
    save_settings(DashboardSettings(hysa_bank_id="some-bank"), config)

    result = dollar_chart_series(ledger, config, date(2026, 1, 1), date(2026, 1, 2))

    expected_hysa_day2 = 1000.0 * (1 + 0.08 / 365)
    assert result["hysa_value_usd"].to_list() == pytest.approx([1000.0, expected_hysa_day2])


def test_dollar_chart_series_uses_a_benchmark_symbol_override(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 510.0]}),
        tmp_path / "VOO.csv",
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [200.0, 220.0]}),
        tmp_path / "QQQ.adjusted.csv",
    )
    save_settings(DashboardSettings(benchmark_symbol_override="QQQ"), config)

    result = dollar_chart_series(ledger, config, date(2026, 1, 1), date(2026, 1, 2))

    expected_shares = 1000.0 / 200.0
    assert result["benchmark_value_usd"].to_list() == pytest.approx([1000.0, expected_shares * 220.0])


def test_dollar_chart_series_with_no_activity_yet_is_all_zero(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(_event("d1", "2026-01-05", "DEPOSIT", amount=1000.0))
    result = dollar_chart_series(ledger, config, date(2026, 1, 1), date(2026, 1, 2))
    assert result["contributions_usd"].to_list() == pytest.approx([0.0, 0.0])


def test_growth_of_100_chart_indexes_every_series_to_100_at_the_start(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 550.0]}),
        tmp_path / "VOO.csv",
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 600.0]}),
        tmp_path / "VOO.adjusted.csv",
    )
    write_csv_atomic(
        pl.DataFrame({"observation_date": [date(2026, 1, 1), date(2026, 1, 2)], "value": [300.0, 303.0]}),
        tmp_path / "CPIAUCSL.csv",
    )

    result = growth_of_100_chart(ledger, config, date(2026, 1, 1), date(2026, 1, 2))

    assert result["portfolio_index"].to_list() == pytest.approx([100.0, 110.0])
    assert result["benchmark_index"].to_list() == pytest.approx([100.0, 120.0])
    assert result["cpi_index"].to_list() == pytest.approx([100.0, 101.0])
    expected_hysa_day2 = (1 + config.returns.hysa_annual_rate / 365) * 100
    assert result["hysa_index"].to_list() == pytest.approx([100.0, expected_hysa_day2])
    assert result["hysa_rate_pct"].to_list() == pytest.approx([config.returns.hysa_annual_rate * 100] * 2)


def test_growth_of_100_chart_benchmark_and_hysa_are_unaffected_by_a_later_deposit(tmp_path) -> None:
    # A pure index of "how did $100 grow" must not balloon just because a
    # much bigger deposit happened later — that would be measuring "total
    # dollars contributed," not benchmark/HYSA performance (this is what
    # the contribution-replaying counterfactual functions would wrongly do).
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=100.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=0.2, price=500.0, amount=100.0),
        _event("d2", "2026-01-02", "DEPOSIT", amount=100_000.0),
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 500.0]}),
        tmp_path / "VOO.csv",
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [500.0, 510.0]}),
        tmp_path / "VOO.adjusted.csv",
    )

    result = growth_of_100_chart(ledger, config, date(2026, 1, 1), date(2026, 1, 2))

    assert result["benchmark_index"].to_list() == pytest.approx([100.0, 102.0])
    expected_hysa_day2 = 100.0 * (1 + config.returns.hysa_annual_rate / 365)
    assert result["hysa_index"].to_list() == pytest.approx([100.0, expected_hysa_day2])


def test_monthly_pnl_splits_value_change_into_contributions_and_market_gain(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
        _event("d2", "2026-02-15", "DEPOSIT", amount=200.0),
    )
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 1, 31), date(2026, 2, 28)],
            "close": [500.0, 520.0, 530.0],
        }),
        tmp_path / "VOO.csv",
    )
    result = monthly_pnl(ledger, config, date(2026, 1, 1), date(2026, 2, 28))

    assert result["month"].to_list() == ["2026-01", "2026-02"]
    assert result["contributions_usd"].to_list() == pytest.approx([1000.0, 200.0])
    assert result["market_gain_usd"].to_list() == pytest.approx([40.0, 20.0])


def test_monthly_pnl_by_symbol_reconciles_with_the_whole_portfolio_view(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
        _event("d2", "2026-02-15", "DEPOSIT", amount=200.0),
    )
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 1, 31), date(2026, 2, 28)],
            "close": [500.0, 520.0, 530.0],
        }),
        tmp_path / "VOO.csv",
    )
    result = monthly_pnl_by_symbol(ledger, config, date(2026, 1, 1), date(2026, 2, 28))

    january = result.filter(pl.col("month") == "2026-01")
    voo_january = january.filter(pl.col("symbol") == "VOO")
    assert voo_january["contribution_usd"][0] == pytest.approx(1000.0)
    assert voo_january["market_gain_usd"][0] == pytest.approx(40.0)
    assert january["contribution_usd"].sum() == pytest.approx(1000.0)
    assert january["market_gain_usd"].sum() == pytest.approx(40.0)

    february = result.filter(pl.col("month") == "2026-02")
    assert february["contribution_usd"].sum() == pytest.approx(200.0)
    assert february["market_gain_usd"].sum() == pytest.approx(20.0)
    cash_february = february.filter(pl.col("symbol") == config.ledger.cash_symbol)
    assert cash_february["contribution_usd"][0] == pytest.approx(200.0)
    assert cash_february["market_gain_usd"][0] == pytest.approx(0.0)


def test_allocation_view_reports_current_and_target_pct(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=400.0, amount=800.0),
    )
    write_csv_atomic(pl.DataFrame({"price_date": [date(2026, 1, 1)], "close": [400.0]}), tmp_path / "VOO.csv")
    save_settings(DashboardSettings(target_allocation_pct={"VOO": 90.0, "CASH": 10.0}), config)

    rows = {row["symbol"]: row for row in allocation_view(ledger, config, as_of=date(2026, 1, 1)).to_dicts()}

    assert rows["VOO"]["value_usd"] == pytest.approx(800.0)
    assert rows["VOO"]["current_pct"] == pytest.approx(80.0)
    assert rows["VOO"]["target_pct"] == pytest.approx(90.0)
    assert rows["VOO"]["drift_pct"] == pytest.approx(-10.0)
    assert rows["CASH"]["value_usd"] == pytest.approx(200.0)
    assert rows["CASH"]["current_pct"] == pytest.approx(20.0)


_LOTS_LEDGER = _ledger(
    _event("d1", "2026-01-01", "DEPOSIT", amount=2000.0),
    _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
    _event("s1", "2026-06-01", "SELL", symbol="VOO", shares=1.0, price=600.0, amount=600.0),
)


def test_lots_table_reports_open_lots_with_returns(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 6, 1), date(2026, 12, 1)],
            "close": [500.0, 600.0, 650.0],
        }),
        tmp_path / "VOO.csv",
    )
    table = lots_table(_LOTS_LEDGER, config, as_of=date(2026, 12, 1))

    assert len(table.open_lots) == 1
    assert table.open_lots["raw_return_pct"][0] == pytest.approx((650.0 / 500.0 - 1) * 100)


def test_lots_table_reports_closed_lots_with_hysa_alpha(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 6, 1), date(2026, 12, 1)],
            "close": [500.0, 600.0, 650.0],
        }),
        tmp_path / "VOO.csv",
    )
    table = lots_table(_LOTS_LEDGER, config, as_of=date(2026, 12, 1))

    assert len(table.closed_lots) == 1
    assert table.closed_lots["realized_gain"][0] == pytest.approx(100.0)
    assert table.closed_lots["alpha_vs_hysa_pct"][0] is not None


def test_lots_table_symbol_rollup_reports_open_status() -> None:
    config = AppConfig()
    table = lots_table(_LOTS_LEDGER, config, as_of=date(2026, 12, 1))
    rollup = {row["symbol"]: row for row in table.symbol_rollup.to_dicts()}
    assert rollup["VOO"]["status"] == "open"


def test_risk_stat_reports_max_drawdown_pct(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
    )
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)],
            "close": [500.0, 400.0, 450.0],
        }),
        tmp_path / "VOO.csv",
    )
    result = risk_stat(ledger, config, date(2026, 1, 1), date(2026, 1, 3))
    assert result == pytest.approx(-20.0)


def test_data_quality_reports_last_price_date_per_symbol(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 5)], "close": [500.0, 510.0]}),
        tmp_path / "VOO.csv",
    )
    write_csv_atomic(pl.DataFrame({"price_date": [date(2026, 1, 3)], "close": [100.0]}), tmp_path / "BND.csv")

    rows = {row["symbol"]: row for row in data_quality(["VOO", "BND", "QQQM"], config).to_dicts()}

    assert rows["VOO"]["last_price_date"] == date(2026, 1, 5)
    assert rows["BND"]["last_price_date"] == date(2026, 1, 3)
    assert rows["QQQM"]["last_price_date"] is None
