from datetime import date, datetime

import polars as pl
import pytest

from trades.config import AppConfig
from trades.ledger.counterfactuals import (
    benchmark_counterfactual_value,
    decision_counterfactual_value,
    hysa_counterfactual_value,
)

CONFIG = AppConfig()


def _cashflows(*rows: tuple[str, float]) -> pl.DataFrame:
    return pl.DataFrame([{"event_datetime": datetime.fromisoformat(when), "amount": amount} for when, amount in rows])


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


def test_hysa_counterfactual_compounds_a_single_deposit_daily() -> None:
    value = hysa_counterfactual_value(
        _cashflows(("2025-01-01", -1000.0)), as_of=date(2026, 1, 1), rate_lookup=lambda d: 0.04
    )
    expected = 1000.0 * (1 + 0.04 / 365) ** 365
    assert value == pytest.approx(expected)


def test_hysa_counterfactual_handles_multiple_deposits() -> None:
    value = hysa_counterfactual_value(
        _cashflows(("2025-01-01", -1000.0), ("2025-07-01", -500.0)),
        as_of=date(2026, 1, 1),
        rate_lookup=lambda d: 0.04,
    )
    expected = 1000.0 * (1 + 0.04 / 365) ** 365 + 500.0 * (1 + 0.04 / 365) ** 184
    assert value == pytest.approx(expected)


def test_hysa_counterfactual_a_withdrawal_reduces_the_balance() -> None:
    value = hysa_counterfactual_value(
        _cashflows(("2025-01-01", -1000.0), ("2025-07-01", 200.0)),
        as_of=date(2025, 7, 1),
        rate_lookup=lambda d: 0.04,
    )
    expected = 1000.0 * (1 + 0.04 / 365) ** 181 - 200.0
    assert value == pytest.approx(expected)


def test_hysa_counterfactual_with_no_cashflows_is_zero() -> None:
    empty = pl.DataFrame(schema={"event_datetime": pl.Datetime, "amount": pl.Float64})
    assert hysa_counterfactual_value(empty, as_of=date(2026, 1, 1), rate_lookup=lambda d: 0.04) == pytest.approx(0.0)


def test_hysa_counterfactual_uses_a_varying_rate_series() -> None:
    rates = {date(2025, 1, 1): 0.04, date(2025, 1, 2): 0.08}

    def rate_lookup(day: date) -> float:
        return rates.get(day, 0.04)

    value = hysa_counterfactual_value(
        _cashflows(("2025-01-01", -1000.0)), as_of=date(2025, 1, 3), rate_lookup=rate_lookup
    )
    expected = 1000.0 * (1 + 0.04 / 365) * (1 + 0.08 / 365)
    assert value == pytest.approx(expected)


def test_benchmark_counterfactual_buys_shares_at_the_deposit_date_price() -> None:
    prices = {date(2025, 1, 1): 100.0, date(2025, 6, 1): 120.0}
    value = benchmark_counterfactual_value(
        _cashflows(("2025-01-01", -1000.0)), as_of=date(2025, 6, 1), price_lookup=prices.get
    )
    assert value == pytest.approx(1000.0 / 100.0 * 120.0)


def test_benchmark_counterfactual_sums_shares_bought_across_multiple_deposits() -> None:
    prices = {date(2025, 1, 1): 100.0, date(2025, 2, 1): 200.0, date(2025, 6, 1): 120.0}
    value = benchmark_counterfactual_value(
        _cashflows(("2025-01-01", -1000.0), ("2025-02-01", -1000.0)),
        as_of=date(2025, 6, 1),
        price_lookup=prices.get,
    )
    expected_shares = 1000.0 / 100.0 + 1000.0 / 200.0
    assert value == pytest.approx(expected_shares * 120.0)


def test_benchmark_counterfactual_a_withdrawal_sells_shares() -> None:
    prices = {date(2025, 1, 1): 100.0, date(2025, 2, 1): 200.0, date(2025, 6, 1): 120.0}
    value = benchmark_counterfactual_value(
        _cashflows(("2025-01-01", -1000.0), ("2025-02-01", 400.0)),
        as_of=date(2025, 6, 1),
        price_lookup=prices.get,
    )
    expected_shares = 1000.0 / 100.0 - 400.0 / 200.0
    assert value == pytest.approx(expected_shares * 120.0)


def test_benchmark_counterfactual_with_no_cashflows_is_zero() -> None:
    empty = pl.DataFrame(schema={"event_datetime": pl.Datetime, "amount": pl.Float64})
    assert benchmark_counterfactual_value(empty, as_of=date(2025, 6, 1), price_lookup=lambda d: 100.0) == pytest.approx(
        0.0
    )


def test_benchmark_counterfactual_raises_on_missing_price() -> None:
    with pytest.raises(ValueError, match="No price available"):
        benchmark_counterfactual_value(
            _cashflows(("2025-01-01", -1000.0)), as_of=date(2025, 6, 1), price_lookup=lambda d: None
        )


def test_decision_counterfactual_replays_the_ledger_without_a_reallocations_sell_and_buy() -> None:
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-02", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
        _event("s1", "2026-06-01", "SELL", symbol="VOO", shares=2.0, price=600.0, amount=1200.0),
        _event("b2", "2026-06-01", "BUY", symbol="BND", shares=2.0, price=600.0, amount=1200.0),
    )
    prices = {"VOO": 700.0}
    value = decision_counterfactual_value(
        ledger,
        skip_event_ids=["s1", "b2"],
        price_lookup=lambda symbol, as_of: prices.get(symbol),
        as_of=date(2026, 12, 1),
        config=CONFIG,
    )
    assert value == pytest.approx(1400.0)


def test_decision_counterfactual_with_no_skipped_events_matches_actual_replay() -> None:
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-02", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
    )
    value = decision_counterfactual_value(
        ledger,
        skip_event_ids=[],
        price_lookup=lambda symbol, as_of: 700.0,
        as_of=date(2026, 12, 1),
        config=CONFIG,
    )
    assert value == pytest.approx(1400.0)


def test_decision_counterfactual_ignores_ledger_events_after_as_of() -> None:
    ledger = _ledger(
        _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
        _event("b1", "2026-01-02", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
        _event("d2", "2026-08-01", "DEPOSIT", amount=500.0),
        _event("b2", "2026-08-02", "BUY", symbol="BND", shares=5.0, price=100.0, amount=500.0),
    )
    prices = {"VOO": 700.0}
    value = decision_counterfactual_value(
        ledger,
        skip_event_ids=[],
        price_lookup=lambda symbol, as_of: prices.get(symbol),
        as_of=date(2026, 6, 1),
        config=CONFIG,
    )
    assert value == pytest.approx(1400.0)
