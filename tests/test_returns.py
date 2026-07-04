import math
from datetime import date

import numpy as np
import polars as pl
import pytest

from trades import returns
from trades.config import AppConfig

CONFIG = AppConfig()


def test_total_return_pct() -> None:
    assert returns.total_return_pct(price_paid=100.0, current_price=110.0) == pytest.approx(10.0)


def test_annualized_return_pct_one_year_matches_total_return() -> None:
    assert returns.annualized_return_pct(10.0, days_held=365, config=CONFIG) == pytest.approx(10.0)


def test_annualized_return_pct_zero_days_is_nan() -> None:
    assert math.isnan(returns.annualized_return_pct(10.0, days_held=0, config=CONFIG))


def test_annualized_return_pct_negative_days_raises() -> None:
    with pytest.raises(ValueError, match="days_held"):
        returns.annualized_return_pct(10.0, days_held=-1, config=CONFIG)


def test_annualized_return_pct_short_hold_amplifies() -> None:
    # A 1% gain in a single day annualizes to a huge number by design.
    result = returns.annualized_return_pct(1.0, days_held=1, config=CONFIG)
    assert result > 100


def test_hysa_period_return_pct_one_year() -> None:
    config = AppConfig(returns={"hysa_annual_rate": 0.04})
    assert returns.hysa_period_return_pct(365, config=config) == pytest.approx(4.0)


def test_hysa_period_return_pct_compounds_for_partial_year() -> None:
    config = AppConfig(returns={"hysa_annual_rate": 0.04})
    half_year = returns.hysa_period_return_pct(182, config=config)
    assert 0 < half_year < 4.0


def test_hysa_period_return_pct_negative_days_raises() -> None:
    with pytest.raises(ValueError, match="days_held"):
        returns.hysa_period_return_pct(-1, config=CONFIG)


def test_build_returns_table_and_alpha() -> None:
    trades = pl.DataFrame([
        {"trade_date": date(2026, 1, 1), "symbol": "VOO", "usd_spent": 100.0, "usd_per_share": 100.0},
        {"trade_date": date(2026, 6, 1), "symbol": "BND", "usd_spent": 200.0, "usd_per_share": 50.0},
    ])
    prices = {"VOO": 110.0, "BND": 51.0}
    table = returns.build_returns_table(
        trades, lambda symbol, as_of: prices[symbol], as_of=date(2026, 7, 1), config=CONFIG
    )
    assert len(table) == 2
    assert table["total_return_pct"][0] == pytest.approx(10.0)
    assert "alpha_period_pct" in table.columns

    alpha = returns.portfolio_alpha_pct(table)
    expected = (table["alpha_period_pct"] * table["usd_spent"]).sum() / table["usd_spent"].sum()
    assert alpha == pytest.approx(expected)


def test_build_returns_table_accepts_a_lazyframe() -> None:
    trades = pl.DataFrame([
        {"trade_date": date(2026, 1, 1), "symbol": "VOO", "usd_spent": 100.0, "usd_per_share": 100.0}
    ])
    table = returns.build_returns_table(
        trades.lazy(), lambda symbol, as_of: 110.0, as_of=date(2026, 7, 1), config=CONFIG
    )
    assert isinstance(table, pl.LazyFrame)
    assert len(table.collect()) == 1


def test_build_returns_table_raises_on_missing_price() -> None:
    trades = pl.DataFrame([
        {"trade_date": date(2026, 1, 1), "symbol": "VOO", "usd_spent": 100.0, "usd_per_share": 100.0}
    ])
    with pytest.raises(ValueError, match="No price available"):
        returns.build_returns_table(trades, lambda symbol, as_of: None, as_of=date(2026, 7, 1), config=CONFIG)


def test_fit_trend_linear_recovers_known_line() -> None:
    x = np.array([1, 2, 3, 4, 5])
    y = 2 * x + 1
    config = AppConfig(returns={"trend_fit_kind": "linear"})
    x_sorted, y_fit = returns.fit_trend(x, y, config)
    assert np.allclose(y_fit, 2 * x_sorted + 1, atol=1e-8)


def test_fit_trend_mean_is_flat() -> None:
    x = np.array([1, 2, 3])
    y = np.array([10.0, 20.0, 30.0])
    config = AppConfig(returns={"trend_fit_kind": "mean"})
    _, y_fit = returns.fit_trend(x, y, config)
    assert np.allclose(y_fit, 20.0)
