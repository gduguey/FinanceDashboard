from datetime import date, datetime

import polars as pl
import pytest

from trades.config import AppConfig
from trades.ledger.replay import external_cashflows, portfolio_value, replay_ledger


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


CONFIG = AppConfig()


def test_replay_empty_ledger_has_zero_cash_and_no_lots() -> None:
    empty = pl.DataFrame(
        schema={
            "event_id": pl.Utf8,
            "event_datetime": pl.Datetime,
            "symbol": pl.Utf8,
            "event_type": pl.Utf8,
            "shares": pl.Float64,
            "price": pl.Float64,
            "amount": pl.Float64,
            "currency": pl.Utf8,
            "meta": pl.Object,
        }
    )
    result = replay_ledger(empty, CONFIG)
    assert result.cash_balance == pytest.approx(0.0)
    assert result.open_lots.is_empty()
    assert result.closed_lots.is_empty()


def test_replay_deposit_increases_cash() -> None:
    result = replay_ledger(_ledger(_event("d1", "2026-01-01", "DEPOSIT", amount=1000.0)), CONFIG)
    assert result.cash_balance == pytest.approx(1000.0)


def test_replay_withdrawal_decreases_cash() -> None:
    result = replay_ledger(
        _ledger(
            _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
            _event("w1", "2026-01-02", "WITHDRAWAL", amount=200.0),
        ),
        CONFIG,
    )
    assert result.cash_balance == pytest.approx(800.0)


def test_replay_buy_opens_a_lot_and_reduces_cash() -> None:
    result = replay_ledger(
        _ledger(
            _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
            _event("b1", "2026-01-02", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
        ),
        CONFIG,
    )
    assert result.cash_balance == pytest.approx(0.0)
    assert len(result.open_lots) == 1
    lot = result.open_lots.row(0, named=True)
    assert lot["symbol"] == "VOO"
    assert lot["shares"] == pytest.approx(2.0)
    assert lot["cost_per_share"] == pytest.approx(500.0)
    assert lot["lot_id"] == "b1"


def test_replay_sell_closes_lot_and_increases_cash() -> None:
    result = replay_ledger(
        _ledger(
            _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
            _event("s1", "2026-06-01", "SELL", symbol="VOO", shares=2.0, price=600.0, amount=1200.0),
        ),
        CONFIG,
    )
    assert result.cash_balance == pytest.approx(200.0)  # -1000 (buy) + 1200 (sell)
    assert result.open_lots.is_empty()
    assert len(result.closed_lots) == 1
    closed = result.closed_lots.row(0, named=True)
    assert closed["realized_gain"] == pytest.approx(200.0)
    assert closed["term"] == "SHORT"
    assert closed["closed_by_event_id"] == "s1"


def test_replay_dividend_increases_cash_without_opening_or_closing_lots() -> None:
    result = replay_ledger(
        _ledger(
            _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
            _event("div1", "2026-03-01", "DIVIDEND", symbol="VOO", amount=5.0),
        ),
        CONFIG,
    )
    assert result.cash_balance == pytest.approx(-995.0)
    assert len(result.open_lots) == 1
    assert result.open_lots.row(0, named=True)["shares"] == pytest.approx(2.0)


def test_replay_dividend_accrues_pro_rata_across_open_lots_of_that_symbol() -> None:
    result = replay_ledger(
        _ledger(
            _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=10.0, price=100.0, amount=1000.0),
            _event("b2", "2026-01-01", "BUY", symbol="VOO", shares=30.0, price=100.0, amount=3000.0),
            _event("div1", "2026-03-01", "DIVIDEND", symbol="VOO", amount=100.0),
        ),
        CONFIG,
    )
    lots_by_id = {lot["lot_id"]: lot for lot in result.open_lots.iter_rows(named=True)}
    assert lots_by_id["b1"]["dividends_received"] == pytest.approx(25.0)
    assert lots_by_id["b2"]["dividends_received"] == pytest.approx(75.0)


def test_replay_dividend_on_a_different_symbol_does_not_accrue_to_unrelated_lots() -> None:
    result = replay_ledger(
        _ledger(
            _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=10.0, price=100.0, amount=1000.0),
            _event("div1", "2026-03-01", "DIVIDEND", symbol="BND", amount=100.0),
        ),
        CONFIG,
    )
    assert result.open_lots.row(0, named=True)["dividends_received"] == pytest.approx(0.0)


def test_replay_withholding_and_fee_decrease_cash() -> None:
    result = replay_ledger(
        _ledger(
            _event("div1", "2026-03-01", "DIVIDEND", symbol="VOO", amount=5.0),
            _event("wh1", "2026-03-01", "WITHHOLDING", symbol="VOO", amount=0.75),
            _event("fee1", "2026-03-02", "FEE", symbol="VOO", amount=1.0),
        ),
        CONFIG,
    )
    assert result.cash_balance == pytest.approx(5.0 - 0.75 - 1.0)


def test_replay_split_adjusts_open_lot_shares_and_cost() -> None:
    result = replay_ledger(
        _ledger(
            _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=10.0, price=100.0, amount=1000.0),
            _event("sp1", "2026-02-01", "SPLIT", symbol="VOO", amount=0.0, meta={"ratio": "2"}),
        ),
        CONFIG,
    )
    lot = result.open_lots.row(0, named=True)
    assert lot["shares"] == pytest.approx(20.0)
    assert lot["cost_per_share"] == pytest.approx(50.0)


def test_replay_tracks_symbols_independently() -> None:
    result = replay_ledger(
        _ledger(
            _event("b1", "2026-01-01", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
            _event("b2", "2026-01-01", "BUY", symbol="BND", shares=10.0, price=70.0, amount=700.0),
            _event("s1", "2026-06-01", "SELL", symbol="VOO", shares=2.0, price=600.0, amount=1200.0),
        ),
        CONFIG,
    )
    assert len(result.open_lots) == 1
    assert result.open_lots.row(0, named=True)["symbol"] == "BND"
    assert len(result.closed_lots) == 1
    assert result.closed_lots.row(0, named=True)["symbol"] == "VOO"


def test_replay_raises_on_unknown_event_type() -> None:
    with pytest.raises(ValueError, match="event_type"):
        replay_ledger(_ledger(_event("x1", "2026-01-01", "NOT_A_REAL_TYPE", amount=1.0)), CONFIG)


def test_replay_accepts_a_lazyframe() -> None:
    result = replay_ledger(_ledger(_event("d1", "2026-01-01", "DEPOSIT", amount=1000.0)).lazy(), CONFIG)
    assert result.cash_balance == pytest.approx(1000.0)


def test_external_cashflows_signs_deposit_negative_and_withdrawal_positive() -> None:
    result = external_cashflows(
        _ledger(
            _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
            _event("w1", "2026-01-02", "WITHDRAWAL", amount=200.0),
            _event("b1", "2026-01-03", "BUY", symbol="VOO", shares=1.0, price=100.0, amount=100.0),
        )
    )
    rows = {row["event_datetime"].date().isoformat(): row["amount"] for row in result.iter_rows(named=True)}
    assert rows == {"2026-01-01": -1000.0, "2026-01-02": 200.0}


def test_external_cashflows_accepts_a_lazyframe() -> None:
    result = external_cashflows(_ledger(_event("d1", "2026-01-01", "DEPOSIT", amount=1000.0)).lazy())
    assert isinstance(result, pl.LazyFrame)
    assert result.collect()["amount"][0] == pytest.approx(-1000.0)


def test_portfolio_value_sums_holdings_at_current_price_plus_cash() -> None:
    result = replay_ledger(
        _ledger(
            _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
            _event("b1", "2026-01-02", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
        ),
        CONFIG,
    )
    value = portfolio_value(result, lambda symbol, as_of: 600.0, date(2026, 6, 1))
    assert value == pytest.approx(2.0 * 600.0)


def test_portfolio_value_raises_on_missing_price() -> None:
    result = replay_ledger(
        _ledger(_event("b1", "2026-01-02", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0)),
        CONFIG,
    )
    with pytest.raises(ValueError, match="No price available"):
        portfolio_value(result, lambda symbol, as_of: None, date(2026, 6, 1))


def test_portfolio_value_with_no_open_lots_is_just_cash() -> None:
    result = replay_ledger(_ledger(_event("d1", "2026-01-01", "DEPOSIT", amount=1000.0)), CONFIG)
    value = portfolio_value(result, lambda symbol, as_of: None, date(2026, 6, 1))
    assert value == pytest.approx(1000.0)
