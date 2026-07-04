from datetime import date, datetime

import polars as pl
import pytest

from trades.config import AppConfig
from trades.ledger.lots import ClosedLot, Lot, closed_lots_to_frame, lots_to_frame
from trades.ledger.metrics import lot_returns, max_drawdown, realized_gain_total, symbol_metrics, unrealized_gain, xirr
from trades.ledger.replay import replay_ledger

CONFIG = AppConfig()


def _event(
    event_id: str,
    event_datetime: str,
    event_type: str,
    symbol: str = "CASH",
    shares: float | None = None,
    price: float | None = None,
    amount: float = 0.0,
    meta: dict | None = None,
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
        "meta": meta or {},
    }


def _ledger(*events: dict) -> pl.DataFrame:
    return pl.DataFrame(list(events))


def _open_lots(*lots: Lot) -> pl.DataFrame:
    return lots_to_frame(list(lots))


def _lot(
    lot_id: str,
    opened_at: str,
    shares: float,
    cost_per_share: float,
    symbol: str = "VOO",
    dividends_received: float = 0.0,
) -> Lot:
    return Lot(
        lot_id=lot_id,
        symbol=symbol,
        opened_at=datetime.fromisoformat(opened_at),
        shares=shares,
        cost_per_share=cost_per_share,
        dividends_received=dividends_received,
    )


def test_lot_returns_computes_raw_return_from_price_change() -> None:
    lots = _open_lots(_lot("1", "2025-01-01", shares=10.0, cost_per_share=100.0))
    result = lot_returns(lots, lambda symbol, as_of: 110.0, as_of=date(2026, 1, 1), config=CONFIG)
    row = result.row(0, named=True)
    assert row["raw_return_pct"] == pytest.approx(10.0)


def test_lot_returns_includes_dividends_in_raw_return() -> None:
    lots = _open_lots(_lot("1", "2025-01-01", shares=10.0, cost_per_share=100.0, dividends_received=50.0))
    result = lot_returns(lots, lambda symbol, as_of: 100.0, as_of=date(2026, 1, 1), config=CONFIG)
    row = result.row(0, named=True)
    # (10*100 + 50) / (10*100) - 1 = 5%, price unchanged.
    assert row["raw_return_pct"] == pytest.approx(5.0)


def test_lot_returns_annualizes_holds_of_at_least_365_days() -> None:
    lots = _open_lots(_lot("1", "2025-01-01", shares=10.0, cost_per_share=100.0))
    result = lot_returns(lots, lambda symbol, as_of: 110.0, as_of=date(2026, 1, 1), config=CONFIG)
    row = result.row(0, named=True)
    assert row["days_held"] == 365
    assert row["annualized_return_pct"] == pytest.approx(10.0)


def test_lot_returns_leaves_annualized_blank_under_365_days() -> None:
    lots = _open_lots(_lot("1", "2025-06-01", shares=10.0, cost_per_share=100.0))
    result = lot_returns(lots, lambda symbol, as_of: 110.0, as_of=date(2026, 1, 1), config=CONFIG)
    row = result.row(0, named=True)
    assert row["days_held"] < 365
    assert row["annualized_return_pct"] is None


def test_lot_returns_raises_on_missing_price() -> None:
    lots = _open_lots(_lot("1", "2025-01-01", shares=10.0, cost_per_share=100.0))
    with pytest.raises(ValueError, match="No price available"):
        lot_returns(lots, lambda symbol, as_of: None, as_of=date(2026, 1, 1), config=CONFIG)


def test_lot_returns_raises_on_as_of_before_lot_opened() -> None:
    lots = _open_lots(_lot("1", "2026-06-01", shares=10.0, cost_per_share=100.0))
    with pytest.raises(ValueError, match="days_held"):
        lot_returns(lots, lambda symbol, as_of: 110.0, as_of=date(2026, 1, 1), config=CONFIG)


def test_lot_returns_accepts_a_lazyframe() -> None:
    lots = _open_lots(_lot("1", "2025-01-01", shares=10.0, cost_per_share=100.0))
    result = lot_returns(lots.lazy(), lambda symbol, as_of: 110.0, as_of=date(2026, 1, 1), config=CONFIG)
    assert isinstance(result, pl.LazyFrame)
    assert result.collect().row(0, named=True)["raw_return_pct"] == pytest.approx(10.0)


def test_xirr_recovers_a_known_rate_for_a_single_flow() -> None:
    # $1000 out on day 0, $1100 back exactly 1 year later -> 10%.
    rate = xirr([date(2025, 1, 1), date(2026, 1, 1)], [-1000.0, 1100.0])
    assert rate == pytest.approx(0.10, abs=1e-4)


def test_xirr_handles_multiple_deposits() -> None:
    # Two $500 deposits 6 months apart, $1100 terminal value 1 year after the first.
    rate = xirr(
        [date(2025, 1, 1), date(2025, 7, 1), date(2026, 1, 1)],
        [-500.0, -500.0, 1100.0],
    )
    npv = sum(
        amount / (1 + rate) ** ((d - date(2025, 1, 1)).days / 365)
        for d, amount in zip(
            [date(2025, 1, 1), date(2025, 7, 1), date(2026, 1, 1)], [-500.0, -500.0, 1100.0], strict=True
        )
    )
    assert npv == pytest.approx(0.0, abs=1e-3)


def test_xirr_is_unaffected_by_an_internal_reallocation() -> None:
    # A same-day sell+buy (net zero) should not move the rate at all.
    with_reallocation = xirr(
        [date(2025, 1, 1), date(2025, 6, 1), date(2025, 6, 1), date(2026, 1, 1)],
        [-1000.0, -50.0, 50.0, 1100.0],
    )
    without_reallocation = xirr([date(2025, 1, 1), date(2026, 1, 1)], [-1000.0, 1100.0])
    assert with_reallocation == pytest.approx(without_reallocation, abs=1e-6)


def test_xirr_raises_with_fewer_than_two_cashflows() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        xirr([date(2025, 1, 1)], [-1000.0])


def test_xirr_raises_when_all_cashflows_share_a_sign() -> None:
    with pytest.raises(ValueError, match="negative and a positive"):
        xirr([date(2025, 1, 1), date(2026, 1, 1)], [1000.0, 1100.0])


def _closed_lot(lot_id: str, realized_gain: float, symbol: str = "VOO") -> ClosedLot:
    return ClosedLot(
        lot_id=lot_id,
        symbol=symbol,
        opened_at=datetime(2025, 1, 1),
        closed_at=datetime(2025, 6, 1),
        shares=1.0,
        cost_per_share=100.0,
        exit_price=100.0 + realized_gain,
        realized_gain=realized_gain,
        term="SHORT",
        closed_by_event_id="s1",
    )


def test_realized_gain_total_sums_closed_lots() -> None:
    closed = closed_lots_to_frame([_closed_lot("1", 20.0), _closed_lot("2", -5.0)])
    assert realized_gain_total(closed) == pytest.approx(15.0)


def test_realized_gain_total_of_no_closed_lots_is_zero() -> None:
    assert realized_gain_total(closed_lots_to_frame([])) == pytest.approx(0.0)


def test_unrealized_gain_sums_price_appreciation_across_open_lots() -> None:
    lots = _open_lots(
        _lot("1", "2025-01-01", shares=10.0, cost_per_share=100.0),
        _lot("2", "2025-01-01", shares=5.0, cost_per_share=50.0, symbol="BND"),
    )
    prices = {"VOO": 110.0, "BND": 50.0}
    gain = unrealized_gain(lots, lambda symbol, as_of: prices[symbol], as_of=date(2026, 1, 1))
    assert gain == pytest.approx(10 * (110 - 100) + 5 * (50 - 50))


def test_unrealized_gain_optionally_includes_dividends() -> None:
    lots = _open_lots(_lot("1", "2025-01-01", shares=10.0, cost_per_share=100.0, dividends_received=30.0))
    gain = unrealized_gain(lots, lambda symbol, as_of: 100.0, as_of=date(2026, 1, 1), include_dividends=True)
    assert gain == pytest.approx(30.0)


def test_unrealized_gain_excludes_dividends_by_default() -> None:
    lots = _open_lots(_lot("1", "2025-01-01", shares=10.0, cost_per_share=100.0, dividends_received=30.0))
    gain = unrealized_gain(lots, lambda symbol, as_of: 100.0, as_of=date(2026, 1, 1))
    assert gain == pytest.approx(0.0)


def test_unrealized_gain_of_no_open_lots_is_zero() -> None:
    assert unrealized_gain(lots_to_frame([]), lambda symbol, as_of: 100.0, as_of=date(2026, 1, 1)) == pytest.approx(0.0)


def test_unrealized_gain_raises_on_missing_price() -> None:
    lots = _open_lots(_lot("1", "2025-01-01", shares=10.0, cost_per_share=100.0))
    with pytest.raises(ValueError, match="No price available"):
        unrealized_gain(lots, lambda symbol, as_of: None, as_of=date(2026, 1, 1))


def test_symbol_metrics_excludes_drip_buys_from_invested_but_includes_the_dividend() -> None:
    ledger = _ledger(
        _event("b1", "2025-01-01", "BUY", symbol="BND", shares=10.0, price=100.0, amount=1000.0),
        _event("div1", "2025-06-01", "DIVIDEND", symbol="BND", amount=50.0),
        _event(
            "b2",
            "2025-06-01",
            "BUY",
            symbol="BND",
            shares=0.5,
            price=100.0,
            amount=50.0,
            meta={"drip_reinvestment": "true"},
        ),
    )
    result = replay_ledger(ledger, CONFIG)
    metrics = symbol_metrics(ledger, result, "BND", lambda symbol, as_of: 100.0, date(2026, 1, 1))
    assert metrics.invested == pytest.approx(1000.0)
    assert metrics.dividends_received == pytest.approx(50.0)
    assert metrics.current_value == pytest.approx(10.5 * 100.0)
    assert metrics.status == "open"


def test_symbol_metrics_of_a_fully_closed_symbol_has_no_terminal_value() -> None:
    ledger = _ledger(
        _event("b1", "2025-01-01", "BUY", symbol="VOO", shares=10.0, price=100.0, amount=1000.0),
        _event("s1", "2025-06-01", "SELL", symbol="VOO", shares=10.0, price=120.0, amount=1200.0),
    )
    result = replay_ledger(ledger, CONFIG)
    metrics = symbol_metrics(ledger, result, "VOO", lambda symbol, as_of: 999.0, date(2026, 1, 1))
    assert metrics.status == "closed"
    assert metrics.current_value == pytest.approx(0.0)
    assert metrics.unrealized_gain == pytest.approx(0.0)
    assert metrics.realized_gain == pytest.approx(10.0 * (120.0 - 100.0))
    assert metrics.proceeds_received == pytest.approx(1200.0)
    expected_xirr = xirr([date(2025, 1, 1), date(2025, 6, 1)], [-1000.0, 1200.0])
    assert metrics.xirr == pytest.approx(expected_xirr)


def test_symbol_metrics_open_position_xirr_includes_current_value_as_terminal_flow() -> None:
    ledger = _ledger(_event("b1", "2025-01-01", "BUY", symbol="VOO", shares=10.0, price=100.0, amount=1000.0))
    result = replay_ledger(ledger, CONFIG)
    metrics = symbol_metrics(ledger, result, "VOO", lambda symbol, as_of: 110.0, date(2026, 1, 1))
    expected_xirr = xirr([date(2025, 1, 1), date(2026, 1, 1)], [-1000.0, 1100.0])
    assert metrics.xirr == pytest.approx(expected_xirr)


def test_max_drawdown_finds_the_largest_peak_to_trough_decline() -> None:
    series = pl.DataFrame({"nav": [100.0, 120.0, 90.0, 110.0]})
    assert max_drawdown(series, "nav") == pytest.approx(-0.25)


def test_max_drawdown_of_a_monotonically_rising_series_is_zero() -> None:
    series = pl.DataFrame({"nav": [100.0, 110.0, 120.0]})
    assert max_drawdown(series, "nav") == pytest.approx(0.0)


def test_max_drawdown_of_an_empty_series_is_zero() -> None:
    series = pl.DataFrame({"nav": []}, schema={"nav": pl.Float64})
    assert max_drawdown(series, "nav") == pytest.approx(0.0)
