from datetime import date, datetime

import polars as pl
import pytest

from trades.config import AppConfig
from trades.dashboard.cash_sitting import (
    CashSittingSummary,
    cash_inflows,
    cash_received_counterfactual,
    cash_sitting_summary,
    daily_cash_balances,
    sitting_since_date,
)


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        dashboard={"settings_path": tmp_path / "dashboard_settings.json"},
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


def test_daily_cash_balances_tracks_deposits_and_purchases(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-03", "BUY", symbol="VOO", shares=1.0, price=600.0, amount=600.0),
    )
    result = daily_cash_balances(ledger, config, date(2026, 1, 1), date(2026, 1, 4))
    assert result["date"].to_list() == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4)]
    assert result["cash"].to_list() == pytest.approx([1000.0, 1000.0, 400.0, 400.0])


def test_daily_cash_balances_before_any_activity_is_zero(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _ledger(_event("d1", "2026-01-02", "DEPOSIT", amount=1000.0))
    result = daily_cash_balances(ledger, config, date(2026, 1, 1), date(2026, 1, 2))
    assert result["cash"].to_list() == pytest.approx([0.0, 1000.0])


def _cash_series(*pairs: tuple[str, float]) -> pl.DataFrame:
    return pl.DataFrame({
        "date": [date.fromisoformat(d) for d, _ in pairs],
        "cash": [c for _, c in pairs],
    })


def test_sitting_since_date_stays_at_the_start_with_no_qualifying_decrease() -> None:
    series = _cash_series(("2026-01-01", 1000.0), ("2026-01-02", 1000.0), ("2026-01-03", 1200.0))
    assert sitting_since_date(series, threshold_pct=0.20) == date(2026, 1, 1)


def test_sitting_since_date_ignores_a_small_decrease_below_threshold() -> None:
    # A 10% drop doesn't clear a 20% threshold.
    series = _cash_series(("2026-01-01", 1000.0), ("2026-01-02", 900.0))
    assert sitting_since_date(series, threshold_pct=0.20) == date(2026, 1, 1)


def test_sitting_since_date_resets_on_a_qualifying_decrease() -> None:
    series = _cash_series(("2026-01-01", 1000.0), ("2026-01-02", 1000.0), ("2026-01-03", 700.0), ("2026-01-04", 700.0))
    assert sitting_since_date(series, threshold_pct=0.20) == date(2026, 1, 3)


def test_sitting_since_date_does_not_reset_on_an_increase_after_a_decrease() -> None:
    series = _cash_series(
        ("2026-01-01", 1000.0),
        ("2026-01-03", 700.0),  # qualifying decrease -> resets to Jan 3
        ("2026-01-05", 5000.0),  # increase -> must not reset
        ("2026-01-06", 4500.0),  # 10% drop off the new 5000 balance -> below 20% threshold, no reset
    )
    assert sitting_since_date(series, threshold_pct=0.20) == date(2026, 1, 3)


def test_cash_sitting_summary_flags_no_warning_when_recently_deployed() -> None:
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-05", 500.0))
    growth_index = pl.DataFrame({
        "date": [date(2026, 1, 1), date(2026, 1, 5)],
        "portfolio_index": [100.0, 101.0],
        "benchmark_index": [100.0, 100.5],
    })
    summary = cash_sitting_summary(daily_cash, growth_index, date(2026, 1, 5), AppConfig())
    assert isinstance(summary, CashSittingSummary)
    assert summary.cash_usd == pytest.approx(500.0)
    assert summary.sitting_since == date(2026, 1, 5)
    assert summary.days_sitting == 0
    assert summary.warning_level == "none"


def test_cash_sitting_summary_flags_light_warning_past_one_week() -> None:
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-09", 1000.0))
    growth_index = pl.DataFrame({
        "date": [date(2026, 1, 1), date(2026, 1, 9)],
        "portfolio_index": [100.0, 110.0],
        "benchmark_index": [100.0, 105.0],
    })
    summary = cash_sitting_summary(daily_cash, growth_index, date(2026, 1, 9), AppConfig())
    assert summary.days_sitting == 8
    assert summary.warning_level == "light"


def test_cash_sitting_summary_flags_heavy_warning_past_two_weeks() -> None:
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-16", 1000.0))
    growth_index = pl.DataFrame({
        "date": [date(2026, 1, 1), date(2026, 1, 16)],
        "portfolio_index": [100.0, 110.0],
        "benchmark_index": [100.0, 105.0],
    })
    summary = cash_sitting_summary(daily_cash, growth_index, date(2026, 1, 16), AppConfig())
    assert summary.days_sitting == 15
    assert summary.warning_level == "heavy"


def test_cash_sitting_summary_estimates_missed_earnings_vs_portfolio_and_benchmark() -> None:
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-09", 1000.0))
    growth_index = pl.DataFrame({
        "date": [date(2026, 1, 1), date(2026, 1, 9)],
        "portfolio_index": [100.0, 110.0],  # +10% over the window
        "benchmark_index": [100.0, 105.0],  # +5% over the window
    })
    summary = cash_sitting_summary(daily_cash, growth_index, date(2026, 1, 9), AppConfig())
    assert summary.hypothetical_value_portfolio_usd == pytest.approx(1100.0)
    assert summary.missed_earnings_portfolio_usd == pytest.approx(100.0)
    assert summary.hypothetical_value_benchmark_usd == pytest.approx(1050.0)
    assert summary.missed_earnings_benchmark_usd == pytest.approx(50.0)


def test_cash_inflows_treats_a_daily_increase_as_a_negative_deposit_flow() -> None:
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-02", 1500.0))
    flows = cash_inflows(daily_cash)
    assert flows["event_datetime"].to_list() == [datetime(2026, 1, 1), datetime(2026, 1, 2)]
    assert flows["amount"].to_list() == pytest.approx([-1000.0, -500.0])


def test_cash_inflows_ignores_a_decrease() -> None:
    # A decrease means the cash was deployed into a real purchase, not
    # something that should undo the counterfactual's own virtual deposit.
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-02", 400.0), ("2026-01-03", 400.0))
    flows = cash_inflows(daily_cash)
    assert flows["event_datetime"].to_list() == [datetime(2026, 1, 1)]
    assert flows["amount"].to_list() == pytest.approx([-1000.0])


def test_cash_received_counterfactual_covers_every_day_and_zero_fills_before_the_first_flow(tmp_path) -> None:
    config = _config(tmp_path)
    daily_cash = _cash_series(("2026-01-01", 0.0), ("2026-01-02", 0.0), ("2026-01-03", 1000.0))
    result = cash_received_counterfactual(
        daily_cash,
        date(2026, 1, 3),
        benchmark_price_lookup=lambda _day: 100.0,
        hysa_rate_lookup=lambda _day: 0.05,
        days_per_year=config.returns.days_per_year,
    )
    assert result["date"].to_list() == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    assert result["benchmark_value_usd"].to_list() == pytest.approx([0.0, 0.0, 1000.0])
    assert result["hysa_value_usd"].to_list() == pytest.approx([0.0, 0.0, 1000.0])


def test_cash_received_counterfactual_grows_a_received_deposit_at_the_benchmark_rate(tmp_path) -> None:
    config = _config(tmp_path)
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-02", 1000.0))
    # Benchmark doubles in price from day 1 to day 2 -> the $1000 received
    # on day 1, invested immediately, would be worth $2000 by day 2.
    prices = {date(2026, 1, 1): 100.0, date(2026, 1, 2): 200.0}
    result = cash_received_counterfactual(
        daily_cash,
        date(2026, 1, 2),
        benchmark_price_lookup=lambda day: prices[day],
        hysa_rate_lookup=lambda _day: 0.0,
        days_per_year=config.returns.days_per_year,
    )
    assert result["benchmark_value_usd"].to_list() == pytest.approx([1000.0, 2000.0])
