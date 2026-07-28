from datetime import date, datetime

import polars as pl
import pytest

from trades.config import AppConfig
from trades.dashboard.cash_sitting import (
    CashLot,
    CashSittingSummary,
    cash_received_counterfactual,
    cash_sitting_summary,
    daily_cash_balances,
    open_cash_lots,
)


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


def test_open_cash_lots_single_deposit_stays_open_as_its_own_lot() -> None:
    series = _cash_series(("2026-01-01", 1000.0), ("2026-01-05", 1000.0))
    lots = open_cash_lots(series)
    assert lots == [CashLot(date(2026, 1, 1), 1000.0)]


def test_open_cash_lots_a_deposit_on_top_of_existing_cash_gets_its_own_fresh_lot() -> None:
    # The bug this whole model exists to fix: new money arriving on top of
    # already-sitting cash must never be silently absorbed into the old
    # lot's arrival date — it needs its own, separately-aged lot.
    series = _cash_series(("2026-01-01", 1000.0), ("2026-01-10", 6000.0))
    lots = open_cash_lots(series)
    assert lots == [CashLot(date(2026, 1, 1), 1000.0), CashLot(date(2026, 1, 10), 5000.0)]


def test_open_cash_lots_a_withdrawal_consumes_the_oldest_lot_first() -> None:
    series = _cash_series(("2026-01-01", 1000.0), ("2026-01-05", 1500.0), ("2026-01-06", 1200.0))
    lots = open_cash_lots(series)
    assert lots == [CashLot(date(2026, 1, 1), 700.0), CashLot(date(2026, 1, 5), 500.0)]


def test_open_cash_lots_fully_consumed_by_a_later_withdrawal_leaves_nothing_open() -> None:
    series = _cash_series(("2026-01-01", 1000.0), ("2026-01-03", 0.0))
    assert open_cash_lots(series) == []


def test_cash_sitting_summary_flags_no_warning_for_a_freshly_arrived_deposit() -> None:
    # The $1000 sitting since Jan 1 is fully swept away by Jan 4 (invested);
    # the $500 that arrives Jan 5 is a brand new lot with nothing to do
    # with the old one, so it reports as sitting since Jan 5, not Jan 1.
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-04", 0.0), ("2026-01-05", 500.0))
    growth_index = pl.DataFrame({
        "date": [date(2026, 1, 5)],
        "portfolio_index": [101.0],
        "benchmark_index": [100.5],
    })
    summary = cash_sitting_summary(daily_cash, growth_index, date(2026, 1, 5), AppConfig())
    assert isinstance(summary, CashSittingSummary)
    assert summary.cash_usd == pytest.approx(500.0)
    assert summary.sitting_since == date(2026, 1, 5)
    assert summary.days_sitting == 0
    assert summary.warning_level == "none"


def test_cash_sitting_summary_a_partial_withdrawal_does_not_make_the_leftover_look_fresher() -> None:
    # The old blob heuristic reset "sitting since" to *today* on any big
    # enough decrease, treating whatever's left as brand new -- backwards,
    # since a withdrawal only removes money, it can't make the remainder
    # any younger. The leftover $500 is still the same Jan 1 lot.
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-05", 500.0))
    growth_index = pl.DataFrame({
        "date": [date(2026, 1, 1), date(2026, 1, 5)],
        "portfolio_index": [100.0, 101.0],
        "benchmark_index": [100.0, 100.5],
    })
    summary = cash_sitting_summary(daily_cash, growth_index, date(2026, 1, 5), AppConfig())
    assert summary.cash_usd == pytest.approx(500.0)
    assert summary.sitting_since == date(2026, 1, 1)
    assert summary.days_sitting == 4


def test_cash_sitting_summary_raises_a_value_error_when_benchmark_history_is_missing() -> None:
    # A benchmark price cache that doesn't cover the lot's arrival date
    # leaves `benchmark_index` null there -- this must surface as a clean
    # ValueError (mapped to a 422 by the API), not an unhandled TypeError
    # from dividing by None.
    daily_cash = _cash_series(("2026-01-01", 1000.0))
    growth_index = pl.DataFrame({
        "date": [date(2026, 1, 1)],
        "portfolio_index": [100.0],
        "benchmark_index": [None],
    })
    with pytest.raises(ValueError, match="benchmark"):
        cash_sitting_summary(daily_cash, growth_index, date(2026, 1, 1), AppConfig())


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


def test_cash_sitting_summary_does_not_inflate_missed_earnings_for_freshly_arrived_cash() -> None:
    # $1000 has been sitting since Jan 1; $5000 more arrives on Jan 10,
    # the same day this is measured. The old blob heuristic would have
    # applied the full Jan1->Jan10 growth to the whole $6000 blended
    # balance -- the fresh $5000 didn't exist for any of that window, so
    # it must contribute zero missed earnings, not a share of the $1000's.
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-10", 6000.0))
    growth_index = pl.DataFrame({
        "date": [date(2026, 1, 1), date(2026, 1, 10)],
        "portfolio_index": [100.0, 110.0],
        "benchmark_index": [100.0, 105.0],
    })
    summary = cash_sitting_summary(daily_cash, growth_index, date(2026, 1, 10), AppConfig())
    assert summary.sitting_since == date(2026, 1, 1)
    assert summary.days_sitting == 9
    # Old lot: 1000 * 1.10 = 1100. New lot: 5000 * (110/110) = 5000. Total 6100.
    assert summary.hypothetical_value_portfolio_usd == pytest.approx(6100.0)
    assert summary.missed_earnings_portfolio_usd == pytest.approx(100.0)


def test_cash_received_counterfactual_covers_every_day_and_zero_fills_before_the_first_flow(tmp_path) -> None:
    config = _config(tmp_path)
    daily_cash = _cash_series(("2026-01-01", 0.0), ("2026-01-02", 0.0), ("2026-01-03", 1000.0))
    result = cash_received_counterfactual(
        daily_cash,
        benchmark_price_lookup=lambda _day: 100.0,
        hysa_rate_lookup=lambda _day: 0.0,
        days_per_year=config.returns.days_per_year,
    )
    assert result["date"].to_list() == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    assert result["benchmark_live_usd"].to_list() == pytest.approx([0.0, 0.0, 1000.0])
    assert result["benchmark_realized_usd"].to_list() == pytest.approx([0.0, 0.0, 0.0])
    assert result["hysa_live_usd"].to_list() == pytest.approx([0.0, 0.0, 1000.0])
    assert result["hysa_realized_usd"].to_list() == pytest.approx([0.0, 0.0, 0.0])


def test_cash_received_counterfactual_grows_a_still_open_lot_at_the_benchmark_rate(tmp_path) -> None:
    config = _config(tmp_path)
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-02", 1000.0))
    prices = {date(2026, 1, 1): 100.0, date(2026, 1, 2): 200.0}
    result = cash_received_counterfactual(
        daily_cash,
        benchmark_price_lookup=lambda day: prices[day],
        hysa_rate_lookup=lambda _day: 0.0,
        days_per_year=config.returns.days_per_year,
    )
    assert result["benchmark_live_usd"].to_list() == pytest.approx([1000.0, 2000.0])
    assert result["benchmark_realized_usd"].to_list() == pytest.approx([0.0, 0.0])


def test_cash_received_counterfactual_grows_a_still_open_lot_at_the_hysa_rate() -> None:
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-02", 1000.0))
    result = cash_received_counterfactual(
        daily_cash,
        benchmark_price_lookup=lambda _day: 100.0,
        hysa_rate_lookup=lambda _day: 0.10,
        days_per_year=1,
    )
    assert result["hysa_live_usd"].to_list() == pytest.approx([1000.0, 1100.0])
    assert result["hysa_realized_usd"].to_list() == pytest.approx([0.0, 0.0])


def test_cash_received_counterfactual_freezes_the_gain_into_realized_on_a_full_withdrawal() -> None:
    # $1000 sits from Jan1, grows to a hypothetical $1100 by Jan2 (still
    # open, so that's "live"). On Jan3 the real $1000 gets deployed (cash
    # drops to 0) -- the counterfactual must bank the gain *as of Jan3's
    # own price* (not Jan2's) into realized, and drop live back to zero,
    # rather than leaving a residual that keeps compounding forever.
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-02", 1000.0), ("2026-01-03", 0.0))
    prices = {date(2026, 1, 1): 100.0, date(2026, 1, 2): 110.0, date(2026, 1, 3): 120.0}
    result = cash_received_counterfactual(
        daily_cash,
        benchmark_price_lookup=lambda day: prices[day],
        hysa_rate_lookup=lambda _day: 0.0,
        days_per_year=1,
    )
    assert result["benchmark_live_usd"].to_list() == pytest.approx([1000.0, 1100.0, 0.0])
    assert result["benchmark_realized_usd"].to_list() == pytest.approx([0.0, 0.0, 200.0])


def test_cash_received_counterfactual_freezes_only_the_consumed_share_on_a_partial_withdrawal() -> None:
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-02", 1000.0), ("2026-01-03", 600.0))
    prices = {date(2026, 1, 1): 100.0, date(2026, 1, 2): 110.0, date(2026, 1, 3): 120.0}
    result = cash_received_counterfactual(
        daily_cash,
        benchmark_price_lookup=lambda day: prices[day],
        hysa_rate_lookup=lambda _day: 0.0,
        days_per_year=1,
    )
    assert result["benchmark_live_usd"].to_list() == pytest.approx([1000.0, 1100.0, 720.0])
    assert result["benchmark_realized_usd"].to_list() == pytest.approx([0.0, 0.0, 80.0])


def test_cash_received_counterfactual_realized_stays_frozen_after_deployment() -> None:
    # The whole point of freezing: once banked, a realized gain must not
    # keep compounding on its own afterward (that would double-count
    # against the real portfolio's own subsequent performance).
    daily_cash = _cash_series(("2026-01-01", 1000.0), ("2026-01-02", 0.0), ("2026-01-03", 0.0), ("2026-01-04", 0.0))
    prices = {date(2026, 1, 1): 100.0, date(2026, 1, 2): 120.0, date(2026, 1, 3): 200.0, date(2026, 1, 4): 300.0}
    result = cash_received_counterfactual(
        daily_cash,
        benchmark_price_lookup=lambda day: prices[day],
        hysa_rate_lookup=lambda _day: 0.0,
        days_per_year=1,
    )
    assert result["benchmark_realized_usd"].to_list() == pytest.approx([0.0, 200.0, 200.0, 200.0])
    assert result["benchmark_live_usd"].to_list() == pytest.approx([1000.0, 0.0, 0.0, 0.0])
