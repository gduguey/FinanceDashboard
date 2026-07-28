from datetime import date, datetime

import polars as pl
import pytest

from trades.config import AppConfig
from trades.ledger.metrics import symbol_metrics, xirr
from trades.ledger.nav import nav_series
from trades.ledger.replay import external_cashflows, portfolio_value, replay_ledger

CONFIG = AppConfig()


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


REALLOCATION_LEDGER = _ledger(
    _event("d1", "2026-01-01", "DEPOSIT", amount=1000.0),
    _event("b1", "2026-01-02", "BUY", symbol="VOO", shares=2.0, price=500.0, amount=1000.0),
    _event("s1", "2026-06-01", "SELL", symbol="VOO", shares=2.0, price=600.0, amount=1200.0),
    _event("b2", "2026-06-01", "BUY", symbol="BND", shares=2.0, price=600.0, amount=1200.0),
)


def test_reallocation_contributes_no_external_cashflow() -> None:
    flows = external_cashflows(REALLOCATION_LEDGER)
    assert len(flows) == 1
    assert flows.row(0, named=True)["event_datetime"] == datetime(2026, 1, 1)


def test_reallocation_does_not_enter_portfolio_xirrs_cashflow_set() -> None:
    flows = external_cashflows(REALLOCATION_LEDGER)
    result = replay_ledger(REALLOCATION_LEDGER, CONFIG)
    terminal_value = portfolio_value(
        result, price_lookup=lambda symbol, as_of: {"BND": 600.0}[symbol], as_of=date(2026, 12, 1)
    )
    dates = [*flows["event_datetime"].dt.date().to_list(), date(2026, 12, 1)]
    amounts = [*flows["amount"].to_list(), terminal_value]
    rate = xirr(dates, amounts, CONFIG)
    days = (date(2026, 12, 1) - date(2026, 1, 1)).days
    assert rate == pytest.approx((1200.0 / 1000.0) ** (CONFIG.returns.days_per_year / days) - 1)


def test_reallocation_sell_proceeds_enter_the_sold_symbols_per_symbol_cashflows() -> None:
    result = replay_ledger(REALLOCATION_LEDGER, CONFIG)
    voo_metrics = symbol_metrics(
        REALLOCATION_LEDGER,
        result,
        "VOO",
        price_lookup=lambda symbol, as_of: None,
        as_of=date(2026, 12, 1),
        config=CONFIG,
    )
    assert voo_metrics.status == "closed"
    assert voo_metrics.proceeds_received == pytest.approx(1200.0)
    assert voo_metrics.realized_gain == pytest.approx(200.0)


def test_reallocation_does_not_mint_or_burn_nav_units() -> None:
    values = pl.DataFrame([
        {"date": date(2026, 1, 1), "value": 1000.0},
        {"date": date(2026, 6, 1), "value": 1200.0},
    ])
    flows = external_cashflows(REALLOCATION_LEDGER)
    nav = nav_series(values, flows)
    units_outstanding = nav["units_outstanding"].to_list()
    assert units_outstanding[0] == pytest.approx(units_outstanding[1])
