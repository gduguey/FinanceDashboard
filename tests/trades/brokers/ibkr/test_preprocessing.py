from datetime import datetime

import polars as pl
import pytest

from trades.brokers.ibkr import preprocessing
from trades.brokers.ibkr.api import ParsedStatement
from trades.brokers.ibkr.models import IbkrCashTransaction, IbkrTrade
from trades.config import AppConfig

CONFIG = AppConfig()


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
    return IbkrTrade(
        account_id="U24174819",
        transaction_id=transaction_id,
        trade_id=trade_id,
        symbol=symbol,
        asset_category="STK",
        currency="USD",
        buy_sell=buy_sell,
        trade_date=datetime.fromisoformat(date_time).date(),
        date_time=datetime.fromisoformat(date_time),
        quantity=quantity,
        trade_price=trade_price,
        trade_money=trade_money,
        ib_commission=ib_commission,
        net_cash=trade_money + ib_commission,
        level_of_detail="EXECUTION",
        notes=notes,
    ).model_dump()


def _trades_frame(*trades: dict) -> pl.DataFrame:
    return pl.DataFrame(trades)


def _ibkr_cash_transaction(
    transaction_type: str,
    amount: float,
    symbol: str = "",
    transaction_id: str = "1",
    level_of_detail: str = "DETAIL",
    date_time: str = "2026-06-30 09:48:03",
    dividend_type: str = "",
    ex_date: str = "",
) -> dict:
    return IbkrCashTransaction(
        account_id="U24174819",
        transaction_id=transaction_id,
        symbol=symbol,
        currency="USD",
        date_time=datetime.fromisoformat(date_time),
        amount=amount,
        type=transaction_type,
        description="",
        action_id="",
        level_of_detail=level_of_detail,
        dividend_type=dividend_type,
        ex_date=ex_date,
    ).model_dump()


def _cash_transactions_frame(*transactions: dict) -> pl.DataFrame:
    return pl.DataFrame(transactions)


def test_standardize_ibkr_trades_maps_a_buy_with_fee_to_two_events() -> None:
    trades = _trades_frame(_ibkr_trade("BUY", "voo", 2.0, 681.81, ib_commission=-1.0, transaction_id="9001"))
    ledger = preprocessing._standardize_ibkr_trades(trades, CONFIG)

    assert ledger.columns == [
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

    buy = ledger.filter(pl.col("event_type") == "BUY").row(0, named=True)
    assert buy["event_id"] == "ibkr:9001"
    assert buy["symbol"] == "VOO"
    assert buy["shares"] == pytest.approx(2.0)
    assert buy["price"] == pytest.approx(681.81)
    assert buy["amount"] == pytest.approx(1363.62)
    assert buy["currency"] == "USD"
    assert buy["meta"] == {"transaction_id": "9001", "trade_id": "1", "notes": "P"}

    fee = ledger.filter(pl.col("event_type") == "FEE").row(0, named=True)
    assert fee["event_id"] == "ibkr:9001:fee"
    assert fee["symbol"] == "VOO"
    assert fee["shares"] is None
    assert fee["price"] is None
    assert fee["amount"] == pytest.approx(1.0)


def test_standardize_ibkr_trades_maps_a_sell() -> None:
    trades = _trades_frame(_ibkr_trade("SELL", "VOO", -1.0, 700.0, transaction_id="9002"))
    ledger = preprocessing._standardize_ibkr_trades(trades, CONFIG)

    assert len(ledger) == 1
    row = ledger.row(0, named=True)
    assert row["event_type"] == "SELL"
    assert row["shares"] == pytest.approx(1.0)
    assert row["amount"] == pytest.approx(700.0)


def test_standardize_ibkr_trades_skips_fee_row_when_commission_is_zero() -> None:
    trades = _trades_frame(_ibkr_trade("BUY", "VOO", 1.0, 100.0, ib_commission=0.0))
    ledger = preprocessing._standardize_ibkr_trades(trades, CONFIG)
    assert len(ledger) == 1
    assert ledger.row(0, named=True)["event_type"] == "BUY"


def test_standardize_ibkr_trades_drops_cancellations() -> None:
    trades = _trades_frame(
        _ibkr_trade("BUY", "VOO", 2.0, 681.81, transaction_id="1"),
        _ibkr_trade("BUY (Ca.)", "VOO", 2.0, 681.81, transaction_id="2"),
        _ibkr_trade("SELL (Ca.)", "VOO", -2.0, 681.81, transaction_id="3"),
    )
    ledger = preprocessing._standardize_ibkr_trades(trades, CONFIG)
    assert len(ledger) == 1
    assert ledger.row(0, named=True)["event_id"] == "ibkr:1"


def test_standardize_ibkr_trades_flags_drip_reinvestment() -> None:
    trades = _trades_frame(_ibkr_trade("BUY", "VOO", 1.0, 100.0, notes="P;R"))
    ledger = preprocessing._standardize_ibkr_trades(trades, CONFIG)
    assert ledger.row(0, named=True)["meta"]["drip_reinvestment"] == "true"


def test_standardize_ibkr_trades_events_are_sorted_by_datetime() -> None:
    trades = _trades_frame(
        _ibkr_trade("BUY", "VOO", 1.0, 100.0, transaction_id="2", date_time="2026-06-02 10:00:00"),
        _ibkr_trade("BUY", "VOO", 1.0, 100.0, transaction_id="1", date_time="2026-06-01 10:00:00"),
    )
    ledger = preprocessing._standardize_ibkr_trades(trades, CONFIG)
    assert ledger["event_id"].to_list() == ["ibkr:1", "ibkr:2"]


def test_standardize_ibkr_cash_transactions_maps_deposit_and_withdrawal() -> None:
    transactions = _cash_transactions_frame(
        _ibkr_cash_transaction("Deposits/Withdrawals", 1000.0, transaction_id="1"),
        _ibkr_cash_transaction("Deposits/Withdrawals", -200.0, transaction_id="2"),
    )
    ledger = preprocessing._standardize_ibkr_cash_transactions(transactions)
    assert set(ledger["event_type"]) == {"DEPOSIT", "WITHDRAWAL"}
    deposit = ledger.filter(pl.col("event_type") == "DEPOSIT").row(0, named=True)
    assert deposit["amount"] == pytest.approx(1000.0)
    assert deposit["symbol"] == "CASH"


@pytest.mark.parametrize(
    ("transaction_type", "expected_event_type"),
    [
        ("Dividends", "DIVIDEND"),
        ("Payment In Lieu Of Dividends", "DIVIDEND"),
        ("Broker Interest Received", "DIVIDEND"),
        ("Bond Interest Received", "DIVIDEND"),
        ("Withholding Tax", "WITHHOLDING"),
        ("Broker Interest Paid", "FEE"),
        ("Other Fees", "FEE"),
        ("Commission Adjustments", "FEE"),
    ],
)
def test_standardize_ibkr_cash_transactions_maps_known_types(transaction_type: str, expected_event_type: str) -> None:
    transactions = _cash_transactions_frame(_ibkr_cash_transaction(transaction_type, 5.0, symbol="VOO"))
    ledger = preprocessing._standardize_ibkr_cash_transactions(transactions)
    assert ledger.row(0, named=True)["event_type"] == expected_event_type


def test_standardize_ibkr_cash_transactions_carries_dividend_type_and_ex_date_in_meta() -> None:
    transactions = _cash_transactions_frame(
        _ibkr_cash_transaction("Dividends", 5.0, symbol="VOO", dividend_type="Ordinary Dividend", ex_date="2026-03-27")
    )
    ledger = preprocessing._standardize_ibkr_cash_transactions(transactions)
    meta = ledger.row(0, named=True)["meta"]
    assert meta["dividend_type"] == "Ordinary Dividend"
    assert meta["ex_date"] == "2026-03-27"


def test_standardize_ibkr_cash_transactions_omits_dividend_type_for_non_dividend_rows() -> None:
    transactions = _cash_transactions_frame(
        _ibkr_cash_transaction("Withholding Tax", 1.0, symbol="VOO", dividend_type="Ordinary Dividend")
    )
    ledger = preprocessing._standardize_ibkr_cash_transactions(transactions)
    meta = ledger.row(0, named=True)["meta"]
    assert "dividend_type" not in meta
    assert "ex_date" not in meta


@pytest.mark.parametrize(
    ("transaction_type", "expected_income_type"),
    [
        ("Dividends", None),
        ("Broker Interest Received", "interest"),
        ("Bond Interest Received", "interest"),
        ("Payment In Lieu Of Dividends", "substitute_payment"),
    ],
)
def test_standardize_ibkr_cash_transactions_tags_income_type(
    transaction_type: str, expected_income_type: str | None
) -> None:
    transactions = _cash_transactions_frame(_ibkr_cash_transaction(transaction_type, 5.0, symbol="VOO"))
    ledger = preprocessing._standardize_ibkr_cash_transactions(transactions)
    meta = ledger.row(0, named=True)["meta"]
    assert meta.get("income_type") == expected_income_type


def test_standardize_ibkr_cash_transactions_drops_unrecognized_types() -> None:
    transactions = _cash_transactions_frame(_ibkr_cash_transaction("Some Unknown Type", 5.0))
    ledger = preprocessing._standardize_ibkr_cash_transactions(transactions)
    assert ledger.is_empty()


def test_standardize_ibkr_cash_transactions_drops_summary_rows() -> None:
    transactions = _cash_transactions_frame(
        _ibkr_cash_transaction("Dividends", 5.0, symbol="VOO", level_of_detail="SUMMARY")
    )
    ledger = preprocessing._standardize_ibkr_cash_transactions(transactions)
    assert ledger.is_empty()


def test_statement_to_ledger_combines_trades_and_cash_transactions() -> None:
    statement = ParsedStatement(
        from_date=datetime(2026, 6, 30).date(),
        to_date=datetime(2026, 6, 30).date(),
        when_generated=datetime(2026, 7, 1, 6, 0, 0),
        trades=[IbkrTrade(**_ibkr_trade("BUY", "VOO", 2.0, 681.81, transaction_id="9001"))],
        cash_transactions=[
            IbkrCashTransaction(**_ibkr_cash_transaction("Deposits/Withdrawals", 1000.0, transaction_id="8000"))
        ],
    )
    ledger = preprocessing.statement_to_ledger(statement, CONFIG)
    assert set(ledger["event_id"]) == {"ibkr:9001", "ibkr:8000"}
    assert set(ledger["event_type"]) == {"BUY", "DEPOSIT"}
