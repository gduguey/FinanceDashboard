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
    lots_table,
    make_price_lookup,
    monthly_pnl,
    monthly_pnl_by_symbol,
    overview_cards,
    reallocation_markers,
    resolved_marginal_ordinary_rate,
    resolved_nra_dividend_tax_rate,
    resolved_qualified_ltcg_rate,
    resolved_tax_regime,
    risk_stat,
    tax_summary,
)
from trades.utils.io_utils import write_csv_atomic


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        prices={"cache_dir": tmp_path},
        cpi={"cache_dir": tmp_path},
        hysa_rates={"cache_dir": tmp_path},
    )


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
    cards = overview_cards(_OVERVIEW_LEDGER, config, DashboardSettings(), as_of=date(2026, 1, 3))

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
    cards = overview_cards(_OVERVIEW_LEDGER, config, DashboardSettings(), as_of=date(2026, 1, 3))

    assert cards.xirr_is_provisional is True
    assert cards.xirr_pct == pytest.approx((1040.0 / 1000.0) ** (365 / 2) * 100 - 100)


def test_overview_cards_reports_excess_value_vs_hysa(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)],
            "close": [500.0, 510.0, 520.0],
        }),
        tmp_path / "VOO.csv",
    )
    cards = overview_cards(_OVERVIEW_LEDGER, config, DashboardSettings(), as_of=date(2026, 1, 3))

    expected_hysa_value = 1000.0 * (1 + config.returns.hysa_annual_rate / 365) ** 2
    assert cards.excess_value_vs_hysa_usd == pytest.approx(1040.0 - expected_hysa_value)


def test_overview_cards_excess_value_taxes_the_hysa_leg_when_tax_is_enabled(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)],
            "close": [500.0, 510.0, 520.0],
        }),
        tmp_path / "VOO.csv",
    )
    settings = DashboardSettings(tax_enabled=True, tax_regime="RESIDENT")
    cards = overview_cards(_OVERVIEW_LEDGER, config, settings, as_of=date(2026, 1, 3))

    after_tax_rate = config.returns.hysa_annual_rate * (1 - config.tax.marginal_ordinary_rate)
    expected_hysa_value = 1000.0 * (1 + after_tax_rate / 365) ** 2
    assert cards.excess_value_vs_hysa_usd == pytest.approx(1040.0 - expected_hysa_value)


def test_overview_cards_excess_value_leaves_hysa_untaxed_for_a_nonresident_alien(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)],
            "close": [500.0, 510.0, 520.0],
        }),
        tmp_path / "VOO.csv",
    )
    settings = DashboardSettings(tax_enabled=True, tax_regime="NRA")
    cards = overview_cards(_OVERVIEW_LEDGER, config, settings, as_of=date(2026, 1, 3))

    expected_hysa_value = 1000.0 * (1 + config.returns.hysa_annual_rate / 365) ** 2
    assert cards.excess_value_vs_hysa_usd == pytest.approx(1040.0 - expected_hysa_value)


def test_overview_cards_twr_matches_value_growth_with_no_intermediate_flows(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)],
            "close": [500.0, 510.0, 520.0],
        }),
        tmp_path / "VOO.csv",
    )
    cards = overview_cards(_OVERVIEW_LEDGER, config, DashboardSettings(), as_of=date(2026, 1, 3))

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
    cards = overview_cards(ledger, config, DashboardSettings(), as_of=date(2026, 1, 2))

    assert cards.total_deposited_usd == pytest.approx(1000.0)
    assert cards.total_withdrawn_usd == pytest.approx(200.0)
    assert cards.total_dividends_gross_usd == pytest.approx(5.0)


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

    result = dollar_chart_series(ledger, config, DashboardSettings(), date(2026, 1, 1), date(2026, 1, 2))

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
    settings = DashboardSettings(hysa_fixed_rate_pct=10.0)

    result = dollar_chart_series(ledger, config, settings, date(2026, 1, 1), date(2026, 1, 2))

    expected_hysa_day2 = 1000.0 * (1 + 0.10 / 365)
    assert result["hysa_value_usd"].to_list() == pytest.approx([1000.0, expected_hysa_day2])
    assert result["hysa_rate_pct"].to_list() == pytest.approx([10.0, 10.0])


def test_dollar_chart_series_taxes_the_hysa_leg_when_tax_is_enabled(tmp_path) -> None:
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
    settings = DashboardSettings(hysa_fixed_rate_pct=10.0, tax_enabled=True, tax_regime="RESIDENT")

    result = dollar_chart_series(ledger, config, settings, date(2026, 1, 1), date(2026, 1, 2))

    after_tax_rate_pct = 10.0 * (1 - config.tax.marginal_ordinary_rate)
    expected_hysa_day2 = 1000.0 * (1 + after_tax_rate_pct / 100 / 365)
    assert result["hysa_value_usd"].to_list() == pytest.approx([1000.0, expected_hysa_day2])
    assert result["hysa_rate_pct"].to_list() == pytest.approx([after_tax_rate_pct, after_tax_rate_pct])


def test_dollar_chart_series_leaves_hysa_untaxed_when_tax_is_disabled(tmp_path) -> None:
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
    settings = DashboardSettings(hysa_fixed_rate_pct=10.0, tax_enabled=False, tax_regime="RESIDENT")

    result = dollar_chart_series(ledger, config, settings, date(2026, 1, 1), date(2026, 1, 2))
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
    settings = DashboardSettings(hysa_bank_id="some-bank")

    result = dollar_chart_series(ledger, config, settings, date(2026, 1, 1), date(2026, 1, 2))

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
    settings = DashboardSettings(benchmark_symbol_override="QQQ")

    result = dollar_chart_series(ledger, config, settings, date(2026, 1, 1), date(2026, 1, 2))

    expected_shares = 1000.0 / 200.0
    assert result["benchmark_value_usd"].to_list() == pytest.approx([1000.0, expected_shares * 220.0])


def test_dollar_chart_series_with_no_activity_yet_is_all_zero(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(_event("d1", "2026-01-05", "DEPOSIT", amount=1000.0))
    result = dollar_chart_series(ledger, config, DashboardSettings(), date(2026, 1, 1), date(2026, 1, 2))
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

    result = growth_of_100_chart(ledger, config, DashboardSettings(), date(2026, 1, 1), date(2026, 1, 2))

    assert result["portfolio_index"].to_list() == pytest.approx([100.0, 110.0])
    assert result["benchmark_index"].to_list() == pytest.approx([100.0, 120.0])
    assert result["cpi_index"].to_list() == pytest.approx([100.0, 101.0])
    expected_hysa_day2 = (1 + config.returns.hysa_annual_rate / 365) * 100
    assert result["hysa_index"].to_list() == pytest.approx([100.0, expected_hysa_day2])
    assert result["hysa_rate_pct"].to_list() == pytest.approx([config.returns.hysa_annual_rate * 100] * 2)


def test_growth_of_100_chart_taxes_the_hysa_leg_when_tax_is_enabled(tmp_path) -> None:
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
    settings = DashboardSettings(tax_enabled=True, tax_regime="RESIDENT")

    result = growth_of_100_chart(ledger, config, settings, date(2026, 1, 1), date(2026, 1, 2))

    after_tax_rate = config.returns.hysa_annual_rate * (1 - config.tax.marginal_ordinary_rate)
    expected_hysa_day2 = (1 + after_tax_rate / 365) * 100
    assert result["hysa_index"].to_list() == pytest.approx([100.0, expected_hysa_day2])
    assert result["hysa_rate_pct"].to_list() == pytest.approx([after_tax_rate * 100] * 2)


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

    result = growth_of_100_chart(ledger, config, DashboardSettings(), date(2026, 1, 1), date(2026, 1, 2))

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
    settings = DashboardSettings(target_allocation_pct={"VOO": 90.0, "CASH": 10.0})

    rows = {row["symbol"]: row for row in allocation_view(ledger, config, settings, as_of=date(2026, 1, 1)).to_dicts()}

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
    table = lots_table(_LOTS_LEDGER, config, DashboardSettings(), as_of=date(2026, 12, 1))

    assert len(table.open_lots) == 1
    assert table.open_lots["raw_return_pct"][0] == pytest.approx((650.0 / 500.0 - 1) * 100)


def test_lots_table_reports_closed_lots_with_their_excess_over_a_hysa(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 6, 1), date(2026, 12, 1)],
            "close": [500.0, 600.0, 650.0],
        }),
        tmp_path / "VOO.csv",
    )
    table = lots_table(_LOTS_LEDGER, config, DashboardSettings(), as_of=date(2026, 12, 1))

    assert len(table.closed_lots) == 1
    assert table.closed_lots["realized_gain"][0] == pytest.approx(100.0)
    assert table.closed_lots["excess_return_vs_hysa_pct"][0] is not None


def test_lots_table_symbol_rollup_reports_open_status(tmp_path) -> None:
    config = _config(tmp_path)
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 6, 1), date(2026, 12, 1)],
            "close": [500.0, 600.0, 650.0],
        }),
        tmp_path / "VOO.csv",
    )
    table = lots_table(_LOTS_LEDGER, config, DashboardSettings(), as_of=date(2026, 12, 1))
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


def test_resolved_tax_regime_defaults_to_resident() -> None:
    assert resolved_tax_regime(DashboardSettings()) == "RESIDENT"


def test_resolved_tax_regime_honors_an_explicit_nra_selection() -> None:
    settings = DashboardSettings(tax_regime="NRA")
    assert resolved_tax_regime(settings) == "NRA"


def test_resolved_marginal_ordinary_rate_defaults_to_config(tmp_path) -> None:
    config = _config(tmp_path)
    assert resolved_marginal_ordinary_rate(config, DashboardSettings()) == pytest.approx(
        config.tax.marginal_ordinary_rate
    )


def test_resolved_marginal_ordinary_rate_honors_an_override(tmp_path) -> None:
    config = _config(tmp_path)
    settings = DashboardSettings(marginal_ordinary_rate_pct=32.0)
    assert resolved_marginal_ordinary_rate(config, settings) == pytest.approx(0.32)


def test_resolved_qualified_ltcg_rate_defaults_to_config(tmp_path) -> None:
    config = _config(tmp_path)
    assert resolved_qualified_ltcg_rate(config, DashboardSettings()) == pytest.approx(config.tax.qualified_ltcg_rate)


def test_resolved_nra_dividend_tax_rate_defaults_to_the_statutory_rate(tmp_path) -> None:
    config = _config(tmp_path)
    assert resolved_nra_dividend_tax_rate(config, DashboardSettings()) == pytest.approx(
        config.tax.nra_statutory_dividend_withholding_rate
    )


def test_resolved_nra_dividend_tax_rate_honors_a_claimed_treaty_rate(tmp_path) -> None:
    config = _config(tmp_path)
    settings = DashboardSettings(w8ben_claimed=True, w8ben_treaty_rate_pct=15.0)
    assert resolved_nra_dividend_tax_rate(config, settings) == pytest.approx(0.15)


def test_resolved_nra_dividend_tax_rate_falls_back_to_statutory_when_claimed_without_a_rate(tmp_path) -> None:
    config = _config(tmp_path)
    settings = DashboardSettings(w8ben_claimed=True)
    assert resolved_nra_dividend_tax_rate(config, settings) == pytest.approx(
        config.tax.nra_statutory_dividend_withholding_rate
    )


def test_resolved_qualified_ltcg_rate_honors_an_override(tmp_path) -> None:
    config = _config(tmp_path)
    settings = DashboardSettings(qualified_ltcg_rate_pct=20.0)
    assert resolved_qualified_ltcg_rate(config, settings) == pytest.approx(0.20)


def _tax_ledger(tmp_path) -> tuple[AppConfig, pl.DataFrame]:
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2025-01-01", "DEPOSIT", amount=2000.0),
        _event("b1", "2025-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
        _event("s1", "2025-06-01", "SELL", symbol="VOO", shares=2.0, price=400.0, amount=800.0),
        _event("b2", "2025-06-10", "BUY", symbol="VOO", shares=2.0, price=400.0, amount=800.0),
        _event("div1", "2025-07-01", "DIVIDEND", symbol="BND", amount=25.0),
    )
    write_csv_atomic(
        pl.DataFrame({"price_date": [date(2025, 1, 1), date(2026, 1, 1)], "close": [500.0, 420.0]}),
        tmp_path / "VOO.csv",
    )
    return config, ledger


def test_tax_summary_reports_the_realized_loss_in_the_annual_report(tmp_path) -> None:
    config, ledger = _tax_ledger(tmp_path)
    summary = tax_summary(ledger, config, DashboardSettings(), as_of=date(2026, 1, 1))
    row = summary.annual.row(0, named=True)
    assert row["short_term_gain_usd"] == pytest.approx(-200.0)
    assert row["ordinary_dividends_usd"] == pytest.approx(25.0)


def test_tax_summary_flags_the_wash_sale_and_nothing_else(tmp_path) -> None:
    config, ledger = _tax_ledger(tmp_path)
    summary = tax_summary(ledger, config, DashboardSettings(), as_of=date(2026, 1, 1))
    assert len(summary.wash_sales) == 1
    assert summary.wash_sales.row(0, named=True)["realized_gain"] == pytest.approx(-200.0)


def test_tax_summary_previews_the_remaining_open_lot(tmp_path) -> None:
    config, ledger = _tax_ledger(tmp_path)
    summary = tax_summary(ledger, config, DashboardSettings(), as_of=date(2026, 1, 1))
    assert len(summary.sale_previews) == 1
    preview = summary.sale_previews.row(0, named=True)
    assert preview["symbol"] == "VOO"
    assert preview["unrealized_gain_usd"] == pytest.approx(40.0)  # 2 shares * (420 - 400)


def test_tax_summary_after_tax_excess_value_taxes_the_hysa_leg_for_residents(tmp_path) -> None:
    config, ledger = _tax_ledger(tmp_path)
    resident_settings = DashboardSettings(tax_regime="RESIDENT")
    resident_excess = tax_summary(
        ledger, config, resident_settings, as_of=date(2026, 1, 1)
    ).after_tax_excess_value_vs_hysa_usd

    nra_settings = DashboardSettings(tax_regime="NRA")
    nra_excess = tax_summary(ledger, config, nra_settings, as_of=date(2026, 1, 1)).after_tax_excess_value_vs_hysa_usd

    # A resident's HYSA leg is taxed (smaller HYSA counterfactual, larger excess);
    # an NRA's is untaxed, so its excess is smaller (the HYSA leg compounded more).
    assert resident_excess > nra_excess


def test_tax_summary_liquidation_value_subtracts_capital_gains_tax_for_a_resident(tmp_path) -> None:
    config, ledger = _tax_ledger(tmp_path)
    settings = DashboardSettings(tax_regime="RESIDENT")
    summary = tax_summary(ledger, config, settings, as_of=date(2026, 1, 1))
    value = overview_cards(ledger, config, settings, as_of=date(2026, 1, 1)).value_usd
    # The one open lot (2 shares of VOO, bought 2025-06-10) is a short-term
    # position with a $40 unrealized gain as of 2026-01-01.
    expected_tax = 40.0 * config.tax.marginal_ordinary_rate
    assert summary.liquidation_pretax_value_usd == pytest.approx(value)
    assert summary.liquidation_long_term_gain_usd == pytest.approx(0.0)
    assert summary.liquidation_short_term_gain_usd == pytest.approx(40.0)
    assert summary.liquidation_capital_gains_tax_usd == pytest.approx(expected_tax)
    assert summary.liquidation_value_usd == pytest.approx(value - expected_tax)


def test_tax_summary_liquidation_value_is_untaxed_for_a_nonresident_alien(tmp_path) -> None:
    config, ledger = _tax_ledger(tmp_path)
    settings = DashboardSettings(tax_regime="NRA")
    summary = tax_summary(ledger, config, settings, as_of=date(2026, 1, 1))
    value = overview_cards(ledger, config, settings, as_of=date(2026, 1, 1)).value_usd
    assert summary.liquidation_capital_gains_tax_usd == pytest.approx(0.0)
    assert summary.liquidation_value_usd == pytest.approx(value)


def test_tax_summary_tax_owed_estimates_tax_on_the_years_realized_income(tmp_path) -> None:
    config, ledger = _tax_ledger(tmp_path)
    settings = DashboardSettings(tax_regime="RESIDENT")
    summary = tax_summary(ledger, config, settings, as_of=date(2026, 1, 1))
    row = summary.tax_owed.row(0, named=True)
    # The year's only realized gain is a $200 short-term loss, floored at
    # zero; the $25 ordinary dividend is taxed at the marginal rate.
    assert row["capital_gains_tax_usd"] == pytest.approx(0.0)
    assert row["dividend_tax_usd"] == pytest.approx(25.0 * config.tax.marginal_ordinary_rate)


def _lots_prices(tmp_path) -> None:
    write_csv_atomic(
        pl.DataFrame({
            "price_date": [date(2026, 1, 1), date(2026, 6, 1), date(2026, 12, 1)],
            "close": [500.0, 600.0, 650.0],
        }),
        tmp_path / "VOO.csv",
    )


def test_closed_lot_excess_return_uses_the_users_own_hysa_rate_not_the_config_constant(tmp_path) -> None:
    """A3e: this column used to be benchmarked against a flat `config.returns.hysa_annual_rate`.

    The overview's own HYSA figure has always used the real published-rate
    lookup, so the two "Alpha vs. HYSA" numbers on screen were measured
    against two different rates. This one now goes through the same lookup,
    which is what makes a fixed-rate override change it at all.
    """
    config = _config(tmp_path)
    _lots_prices(tmp_path)
    # The lot ran 2026-01-01 to 2026-06-01: 151 days compounded daily.
    days_held = (date(2026, 6, 1) - date(2026, 1, 1)).days
    table = lots_table(_LOTS_LEDGER, config, DashboardSettings(hysa_fixed_rate_pct=10.0), as_of=date(2026, 12, 1))

    total_return_pct = table.closed_lots["total_return_pct"][0]
    hysa_return_pct = ((1 + 0.10 / config.returns.days_per_year) ** days_held - 1) * 100
    assert table.closed_lots["excess_return_vs_hysa_pct"][0] == pytest.approx(total_return_pct - hysa_return_pct)


def test_closed_lot_excess_return_reads_the_selected_banks_published_rate(tmp_path) -> None:
    config = _config(tmp_path)
    _lots_prices(tmp_path)
    write_csv_atomic(
        pl.DataFrame({
            "bank_id": ["some-bank"],
            "bank_name": ["Some Bank"],
            "rate_date": [date(2025, 1, 1)],
            "apy_pct": [8.0],
        }),
        tmp_path / "rates.csv",
    )
    days_held = (date(2026, 6, 1) - date(2026, 1, 1)).days
    table = lots_table(_LOTS_LEDGER, config, DashboardSettings(hysa_bank_id="some-bank"), as_of=date(2026, 12, 1))

    total_return_pct = table.closed_lots["total_return_pct"][0]
    hysa_return_pct = ((1 + 0.08 / config.returns.days_per_year) ** days_held - 1) * 100
    assert table.closed_lots["excess_return_vs_hysa_pct"][0] == pytest.approx(total_return_pct - hysa_return_pct)


def test_closed_lot_excess_return_shrinks_when_the_hysa_leg_is_left_untaxed(tmp_path) -> None:
    """Tax-enabled taxes the HYSA leg, exactly as the overview card's does, so the excess grows."""
    config = _config(tmp_path)
    _lots_prices(tmp_path)
    taxed = lots_table(
        _LOTS_LEDGER,
        config,
        DashboardSettings(hysa_fixed_rate_pct=10.0, tax_enabled=True, tax_regime="RESIDENT"),
        as_of=date(2026, 12, 1),
    )
    untaxed = lots_table(_LOTS_LEDGER, config, DashboardSettings(hysa_fixed_rate_pct=10.0), as_of=date(2026, 12, 1))

    assert taxed.closed_lots["excess_return_vs_hysa_pct"][0] > untaxed.closed_lots["excess_return_vs_hysa_pct"][0]
