import json

import pandas as pd
import pytest

from trades.brokers.ibkr import preprocessing


def _ibkr_trade(
    buy_sell: str,
    symbol: str,
    quantity: float,
    trade_price: float,
    ib_commission: float = 0.0,
    notes: str = "P",
    transaction_id: str = "1",
    trade_id: str = "1",
    date_time: str = "2026-06-30 09:48:03",
) -> dict:
    trade_money = quantity * trade_price
    signed_flow = trade_money + ib_commission
    return {
        "account_id": "U24174819",
        "transaction_id": transaction_id,
        "trade_id": trade_id,
        "symbol": symbol,
        "asset_category": "STK",
        "currency": "USD",
        "buy_sell": buy_sell,
        "trade_date": pd.Timestamp(date_time).normalize(),
        "date_time": pd.Timestamp(date_time),
        "quantity": quantity,
        "trade_price": trade_price,
        "trade_money": trade_money,
        "ib_commission": ib_commission,
        "net_cash": -signed_flow if buy_sell == "BUY" else signed_flow,
        "notes": notes,
    }


def test_standardize_ibkr_ledger_maps_a_buy_with_fee_to_two_events() -> None:
    df = pd.DataFrame(
        [_ibkr_trade("BUY", "voo", 2.0, 681.81, ib_commission=-1.0, transaction_id="9001")]
    )
    ledger = preprocessing.standardize_ibkr_ledger(df)

    assert list(ledger.columns) == [
        "event_id",
        "event_datetime",
        "symbol",
        "event_type",
        "shares",
        "price",
        "amount",
        "currency",
        "meta",
    ]
    assert len(ledger) == 2

    buy = ledger[ledger["event_type"] == "BUY"].iloc[0]
    assert buy["event_id"] == "ibkr:9001"
    assert buy["symbol"] == "VOO"
    assert buy["shares"] == pytest.approx(2.0)
    assert buy["price"] == pytest.approx(681.81)
    assert buy["amount"] == pytest.approx(1363.62)
    assert buy["currency"] == "USD"
    assert buy["meta"] == {"transaction_id": "9001", "trade_id": "1", "notes": "P"}

    fee = ledger[ledger["event_type"] == "FEE"].iloc[0]
    assert fee["event_id"] == "ibkr:9001:fee"
    assert fee["symbol"] == "VOO"
    assert pd.isna(fee["shares"])
    assert pd.isna(fee["price"])
    assert fee["amount"] == pytest.approx(1.0)


def test_standardize_ibkr_ledger_maps_a_sell() -> None:
    df = pd.DataFrame([_ibkr_trade("SELL", "VOO", -1.0, 700.0, transaction_id="9002")])
    ledger = preprocessing.standardize_ibkr_ledger(df)

    assert len(ledger) == 1
    row = ledger.iloc[0]
    assert row["event_type"] == "SELL"
    assert row["shares"] == pytest.approx(1.0)
    assert row["amount"] == pytest.approx(700.0)


def test_standardize_ibkr_ledger_skips_fee_row_when_commission_is_zero() -> None:
    df = pd.DataFrame([_ibkr_trade("BUY", "VOO", 1.0, 100.0, ib_commission=0.0)])
    ledger = preprocessing.standardize_ibkr_ledger(df)
    assert len(ledger) == 1
    assert ledger.iloc[0]["event_type"] == "BUY"


def test_standardize_ibkr_ledger_drops_cancellations() -> None:
    df = pd.DataFrame(
        [
            _ibkr_trade("BUY", "VOO", 2.0, 681.81, transaction_id="1"),
            _ibkr_trade("BUY (Ca.)", "VOO", 2.0, 681.81, transaction_id="2"),
            _ibkr_trade("SELL (Ca.)", "VOO", -2.0, 681.81, transaction_id="3"),
        ]
    )
    ledger = preprocessing.standardize_ibkr_ledger(df)
    assert len(ledger) == 1
    assert ledger.iloc[0]["event_id"] == "ibkr:1"


def test_standardize_ibkr_ledger_handles_no_trades() -> None:
    df = pd.DataFrame([_ibkr_trade("BUY (Ca.)", "VOO", 2.0, 681.81)])
    ledger = preprocessing.standardize_ibkr_ledger(df)
    assert ledger.empty
    assert list(ledger.columns) == [
        "event_id",
        "event_datetime",
        "symbol",
        "event_type",
        "shares",
        "price",
        "amount",
        "currency",
        "meta",
    ]


def test_standardize_ibkr_ledger_events_are_sorted_by_datetime() -> None:
    df = pd.DataFrame(
        [
            _ibkr_trade(
                "BUY", "VOO", 1.0, 100.0, transaction_id="2", date_time="2026-06-02 10:00:00"
            ),
            _ibkr_trade(
                "BUY", "VOO", 1.0, 100.0, transaction_id="1", date_time="2026-06-01 10:00:00"
            ),
        ]
    )
    ledger = preprocessing.standardize_ibkr_ledger(df)
    assert list(ledger["event_id"]) == ["ibkr:1", "ibkr:2"]


def _ledger_row(
    event_type: str,
    symbol: str,
    shares: float | None,
    price: float | None,
    amount: float,
    event_datetime: str = "2026-06-30 09:48:03",
    event_id: str = "ibkr:1",
) -> dict:
    return {
        "event_id": event_id,
        "event_datetime": pd.Timestamp(event_datetime),
        "symbol": symbol,
        "event_type": event_type,
        "shares": shares,
        "price": price,
        "amount": amount,
        "currency": "USD",
        "meta": json.dumps({}),
    }


def test_standardize_ibkr_trades_maps_buys_onto_canonical_schema() -> None:
    ledger = pd.DataFrame(
        [_ledger_row("BUY", "VOO", 2.0, 681.81, 1363.62, event_datetime="2026-06-30 09:48:03")]
    )
    standardized = preprocessing.standardize_ibkr_trades(ledger)
    assert list(standardized.columns) == ["trade_date", "symbol", "shares", "usd_spent"]
    row = standardized.iloc[0]
    assert row["symbol"] == "VOO"
    assert row["shares"] == pytest.approx(2.0)
    assert row["usd_spent"] == pytest.approx(1363.62)
    assert row["trade_date"] == pd.Timestamp("2026-06-30")


def test_standardize_ibkr_trades_drops_sells_and_fees_and_cash() -> None:
    ledger = pd.DataFrame(
        [
            _ledger_row("BUY", "VOO", 2.0, 681.81, 1363.62, event_id="ibkr:1"),
            _ledger_row("SELL", "VOO", 1.0, 700.0, 700.0, event_id="ibkr:2"),
            _ledger_row("FEE", "VOO", None, None, 1.0, event_id="ibkr:1:fee"),
            _ledger_row("DEPOSIT", "CASH", None, None, 1000.0, event_id="ibkr:3"),
        ]
    )
    standardized = preprocessing.standardize_ibkr_trades(ledger)
    assert len(standardized) == 1
    assert standardized.iloc[0]["symbol"] == "VOO"


def test_standardize_ibkr_trades_handles_no_buys() -> None:
    ledger = pd.DataFrame([_ledger_row("SELL", "VOO", 1.0, 700.0, 700.0)])
    standardized = preprocessing.standardize_ibkr_trades(ledger)
    assert standardized.empty
    assert list(standardized.columns) == ["trade_date", "symbol", "shares", "usd_spent"]


def test_standardize_ibkr_trades_output_feeds_transactions_pipeline() -> None:
    from trades import transactions
    from trades.config import AggregationConfig

    ledger = pd.DataFrame(
        [
            _ledger_row("BUY", "VOO", 2.0, 681.81, 1363.62, event_id="ibkr:1"),
            _ledger_row("BUY", "VXUS", 5.0, 83.53, 417.65, event_id="ibkr:2"),
        ]
    )
    standardized = preprocessing.standardize_ibkr_trades(ledger)
    enriched = transactions.enrich_trades(standardized)
    aggregated = transactions.aggregate_same_day_trades(enriched, AggregationConfig())
    assert len(aggregated) == 2
    assert set(aggregated["symbol"]) == {"VOO", "VXUS"}
