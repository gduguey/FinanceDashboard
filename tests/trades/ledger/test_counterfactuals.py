from datetime import date, datetime

import polars as pl
import pytest

from trades.config import AppConfig
from trades.ledger.counterfactuals import (
    benchmark_counterfactual_series,
    hysa_counterfactual_series,
    hysa_counterfactual_value,
)

CONFIG = AppConfig()


def _cashflows(*rows: tuple[str, float]) -> pl.DataFrame:
    return pl.DataFrame([{"event_datetime": datetime.fromisoformat(when), "amount": amount} for when, amount in rows])


def _carry_forward(prices: dict[date, float]):
    """Mimic `market_data.prices.price_as_of`'s weekend/holiday roll-back for a plain price dict.

    `benchmark_counterfactual_series` needs a price for every day it
    walks, not just cashflow dates — exactly what the real
    `dashboard.make_price_lookup` provides, so tests use this instead of
    a bare `dict.get` (which would return None on every unpriced day).
    """

    def lookup(target_date: date) -> float | None:
        eligible = [priced_date for priced_date in prices if priced_date <= target_date]
        return prices[max(eligible)] if eligible else None

    return lookup


def test_hysa_counterfactual_compounds_a_single_deposit_daily() -> None:
    value = hysa_counterfactual_value(
        _cashflows(("2025-01-01", -1000.0)),
        as_of=date(2026, 1, 1),
        rate_lookup=lambda d: 0.04,
        days_per_year=CONFIG.returns.days_per_year,
    )
    expected = 1000.0 * (1 + 0.04 / 365) ** 365
    assert value == pytest.approx(expected)


def test_hysa_counterfactual_handles_multiple_deposits() -> None:
    value = hysa_counterfactual_value(
        _cashflows(("2025-01-01", -1000.0), ("2025-07-01", -500.0)),
        as_of=date(2026, 1, 1),
        rate_lookup=lambda d: 0.04,
        days_per_year=CONFIG.returns.days_per_year,
    )
    expected = 1000.0 * (1 + 0.04 / 365) ** 365 + 500.0 * (1 + 0.04 / 365) ** 184
    assert value == pytest.approx(expected)


def test_hysa_counterfactual_a_withdrawal_reduces_the_balance() -> None:
    value = hysa_counterfactual_value(
        _cashflows(("2025-01-01", -1000.0), ("2025-07-01", 200.0)),
        as_of=date(2025, 7, 1),
        rate_lookup=lambda d: 0.04,
        days_per_year=CONFIG.returns.days_per_year,
    )
    expected = 1000.0 * (1 + 0.04 / 365) ** 181 - 200.0
    assert value == pytest.approx(expected)


def test_hysa_counterfactual_with_no_cashflows_is_zero() -> None:
    empty = pl.DataFrame(schema={"event_datetime": pl.Datetime, "amount": pl.Float64})
    assert hysa_counterfactual_value(
        empty, as_of=date(2026, 1, 1), rate_lookup=lambda d: 0.04, days_per_year=CONFIG.returns.days_per_year
    ) == pytest.approx(0.0)


def test_hysa_counterfactual_uses_a_varying_rate_series() -> None:
    rates = {date(2025, 1, 1): 0.04, date(2025, 1, 2): 0.08}

    def rate_lookup(day: date) -> float:
        return rates.get(day, 0.04)

    value = hysa_counterfactual_value(
        _cashflows(("2025-01-01", -1000.0)),
        as_of=date(2025, 1, 3),
        rate_lookup=rate_lookup,
        days_per_year=CONFIG.returns.days_per_year,
    )
    expected = 1000.0 * (1 + 0.04 / 365) * (1 + 0.08 / 365)
    assert value == pytest.approx(expected)


def test_benchmark_counterfactual_series_a_withdrawal_sells_shares() -> None:
    prices = {date(2025, 1, 1): 100.0, date(2025, 2, 1): 200.0, date(2025, 6, 1): 120.0}
    series = benchmark_counterfactual_series(
        _cashflows(("2025-01-01", -1000.0), ("2025-02-01", 400.0)),
        end=date(2025, 6, 1),
        price_lookup=_carry_forward(prices),
    )
    expected_shares = 1000.0 / 100.0 - 400.0 / 200.0
    assert series["value"][-1] == pytest.approx(expected_shares * 120.0)


def test_hysa_counterfactual_series_has_one_row_per_day_since_the_first_flow() -> None:
    series = hysa_counterfactual_series(
        _cashflows(("2025-01-01", -1000.0)),
        end=date(2025, 1, 3),
        rate_lookup=lambda d: 0.04,
        days_per_year=CONFIG.returns.days_per_year,
    )
    assert series["date"].to_list() == [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]
    day1 = 1000.0
    day2 = day1 * (1 + 0.04 / 365)
    day3 = day2 * (1 + 0.04 / 365)
    assert series["value"].to_list() == pytest.approx([day1, day2, day3])


def test_hysa_counterfactual_series_last_value_matches_the_scalar_function() -> None:
    series = hysa_counterfactual_series(
        _cashflows(("2025-01-01", -1000.0), ("2025-07-01", 200.0)),
        end=date(2025, 7, 1),
        rate_lookup=lambda d: 0.04,
        days_per_year=CONFIG.returns.days_per_year,
    )
    value = hysa_counterfactual_value(
        _cashflows(("2025-01-01", -1000.0), ("2025-07-01", 200.0)),
        as_of=date(2025, 7, 1),
        rate_lookup=lambda d: 0.04,
        days_per_year=CONFIG.returns.days_per_year,
    )
    assert series["value"][-1] == pytest.approx(value)


def test_hysa_counterfactual_series_with_no_cashflows_is_empty() -> None:
    empty = pl.DataFrame(schema={"event_datetime": pl.Datetime, "amount": pl.Float64})
    series = hysa_counterfactual_series(
        empty, end=date(2025, 1, 3), rate_lookup=lambda d: 0.04, days_per_year=CONFIG.returns.days_per_year
    )
    assert series.is_empty()


def test_hysa_counterfactual_series_with_cashflows_entirely_after_end_is_empty_with_a_typed_schema() -> None:
    series = hysa_counterfactual_series(
        _cashflows(("2025-01-05", -1000.0)),
        end=date(2025, 1, 1),
        rate_lookup=lambda d: 0.04,
        days_per_year=CONFIG.returns.days_per_year,
    )
    assert series.is_empty()
    assert series.schema == {"date": pl.Date, "value": pl.Float64}


def test_benchmark_counterfactual_series_has_one_row_per_day_since_the_first_flow() -> None:
    prices = {date(2025, 1, 1): 100.0, date(2025, 1, 2): 110.0, date(2025, 1, 3): 120.0}
    series = benchmark_counterfactual_series(
        _cashflows(("2025-01-01", -1000.0)), end=date(2025, 1, 3), price_lookup=prices.get
    )
    assert series["date"].to_list() == [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]
    shares = 1000.0 / 100.0
    assert series["value"].to_list() == pytest.approx([shares * 100.0, shares * 110.0, shares * 120.0])


def test_benchmark_counterfactual_series_with_no_cashflows_is_empty() -> None:
    empty = pl.DataFrame(schema={"event_datetime": pl.Datetime, "amount": pl.Float64})
    series = benchmark_counterfactual_series(empty, end=date(2025, 1, 3), price_lookup=lambda d: 100.0)
    assert series.is_empty()


def test_benchmark_counterfactual_series_with_cashflows_entirely_after_end_is_empty_with_a_typed_schema() -> None:
    series = benchmark_counterfactual_series(
        _cashflows(("2025-01-05", -1000.0)), end=date(2025, 1, 1), price_lookup=lambda d: 100.0
    )
    assert series.is_empty()
    assert series.schema == {"date": pl.Date, "value": pl.Float64}


def test_benchmark_counterfactual_series_raises_on_missing_price() -> None:
    with pytest.raises(ValueError, match="No price available"):
        benchmark_counterfactual_series(
            _cashflows(("2025-01-01", -1000.0)), end=date(2025, 6, 1), price_lookup=lambda d: None
        )
