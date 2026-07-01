import pandas as pd
import pytest

from trades import preprocessing


def _ibkr_trade(buy_sell: str, symbol: str, quantity: float, net_cash: float) -> dict:
    return {
        "account_id": "U24174819",
        "transaction_id": "1",
        "trade_id": "1",
        "symbol": symbol,
        "asset_category": "STK",
        "currency": "USD",
        "buy_sell": buy_sell,
        "trade_date": pd.Timestamp("2026-06-30"),
        "quantity": quantity,
        "trade_price": abs(net_cash / quantity) if quantity else 0.0,
        "trade_money": abs(net_cash),
        "ib_commission": 0.0,
        "net_cash": net_cash,
    }


def test_standardize_ibkr_trades_maps_buys_onto_canonical_schema() -> None:
    df = pd.DataFrame([_ibkr_trade("BUY", "VOO", 2.0, -1363.62)])
    standardized = preprocessing.standardize_ibkr_trades(df)
    assert list(standardized.columns) == ["trade_date", "symbol", "shares", "usd_spent"]
    row = standardized.iloc[0]
    assert row["symbol"] == "VOO"
    assert row["shares"] == pytest.approx(2.0)
    assert row["usd_spent"] == pytest.approx(1363.62)


def test_standardize_ibkr_trades_drops_sells_and_cancellations() -> None:
    df = pd.DataFrame(
        [
            _ibkr_trade("BUY", "VOO", 2.0, -1363.62),
            _ibkr_trade("SELL", "VOO", -1.0, 681.81),
            _ibkr_trade("BUY (Ca.)", "VOO", 2.0, -1363.62),
        ]
    )
    standardized = preprocessing.standardize_ibkr_trades(df)
    assert len(standardized) == 1


def test_standardize_ibkr_trades_handles_no_buys() -> None:
    df = pd.DataFrame([_ibkr_trade("SELL", "VOO", -1.0, 681.81)])
    standardized = preprocessing.standardize_ibkr_trades(df)
    assert standardized.empty
    assert list(standardized.columns) == ["trade_date", "symbol", "shares", "usd_spent"]


def test_standardize_ibkr_trades_output_feeds_transactions_pipeline() -> None:
    from trades import transactions
    from trades.config import AggregationConfig

    df = pd.DataFrame(
        [
            _ibkr_trade("BUY", "VOO", 2.0, -1363.62),
            _ibkr_trade("BUY", "VXUS", 5.0, -417.65),
        ]
    )
    standardized = preprocessing.standardize_ibkr_trades(df)
    enriched = transactions.enrich_trades(standardized)
    aggregated = transactions.aggregate_same_day_trades(enriched, AggregationConfig())
    assert len(aggregated) == 2
    assert set(aggregated["symbol"]) == {"VOO", "VXUS"}
