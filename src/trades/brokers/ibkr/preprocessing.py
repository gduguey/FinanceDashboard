"""IBKR Preprocessing: map native `<Trade>`/`<CashTransaction>` rows onto the ledger.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trades.brokers.ibkr.api import ParsedStatement
from trades.brokers.ibkr.models import IbkrCashTransaction, IbkrTrade
from trades.config import IbkrFlexApiConfig, LedgerEventType
from trades.models import LedgerEvent

_CASH_TRANSACTION_EVENT_TYPES: dict[str, LedgerEventType] = {
    "Dividends": "DIVIDEND",
    "Payment In Lieu Of Dividends": "DIVIDEND",
    "Withholding Tax": "WITHHOLDING",
    "Broker Interest Received": "DIVIDEND",
    "Bond Interest Received": "DIVIDEND",
    "Broker Interest Paid": "FEE",
    "Other Fees": "FEE",
    "Commission Adjustments": "FEE",
}


def _empty_ledger() -> pd.DataFrame:
    return pd.DataFrame(columns=list(LedgerEvent.model_fields)).astype(
        {
            "event_datetime": "datetime64[ns]",
            "shares": "float64",
            "price": "float64",
            "amount": "float64",
        }
    )


def _finish_ledger(rows: pd.DataFrame) -> pd.DataFrame:
    """Common tail of both `standardize_ibkr_*` functions below: `pd.concat`
    turns a `None` (no shares/price) sitting next to a real float into NaN,
    which a pydantic `float | None, gt=0` field rejects as an out-of-range
    float instead of accepting as absent — so NaN is turned back into `None`
    before validating every row through `LedgerEvent`."""
    for column in ("shares", "price"):
        rows[column] = rows[column].astype(object).where(rows[column].notna(), None)
    events = [LedgerEvent.model_validate(row.to_dict()) for _, row in rows.iterrows()]
    df = pd.DataFrame([event.model_dump() for event in events])
    df["event_datetime"] = pd.to_datetime(df["event_datetime"])
    return df.sort_values(["event_datetime", "symbol", "event_id"]).reset_index(drop=True)


def _standardize_ibkr_trades(ibkr_trades: pd.DataFrame, config: IbkrFlexApiConfig) -> pd.DataFrame:
    """Map `<Trade>` rows (validated via `brokers.models.IbkrTrade`) onto ledger
    events. `BUY (Ca.)`/`SELL (Ca.)` cancellations are dropped.

    - `BUY` / `SELL` — one per kept fill; `amount` is the principal
    (`trade_price x quantity`).
    - `FEE` — a second event on the same transaction when `ib_commission` is
    non-zero, kept separate so commission never inflates a lot's cost basis.
    - DRIP flag — a `BUY` carrying `config.drip_reinvestment_note_code` gets
    `meta["drip_reinvestment"] = "true"`.
    """
    trades = ibkr_trades[ibkr_trades["buy_sell"].isin(["BUY", "SELL"])]
    if trades.empty:
        return _empty_ledger()

    def trade_meta(row: pd.Series) -> dict[str, str]:
        meta = {
            "transaction_id": row["transaction_id"],
            "trade_id": row["trade_id"],
            "notes": row["notes"],
        }
        codes = row["notes"].split(";")
        if row["buy_sell"] == "BUY" and config.drip_reinvestment_note_code in codes:
            meta["drip_reinvestment"] = "true"
        return meta

    principal = pd.DataFrame(
        {
            "event_id": "ibkr:" + trades["transaction_id"].astype(str),
            "event_datetime": trades["date_time"],
            "symbol": trades["symbol"],
            "event_type": trades["buy_sell"],
            "shares": trades["quantity"].abs(),
            "price": trades["trade_price"],
            "amount": trades["trade_money"].abs(),
            "currency": trades["currency"],
            "meta": trades.apply(trade_meta, axis=1),
        }
    )

    fees = trades[trades["ib_commission"] != 0]
    fee_rows = pd.DataFrame(
        {
            "event_id": "ibkr:" + fees["transaction_id"].astype(str) + ":fee",
            "event_datetime": fees["date_time"],
            "symbol": fees["symbol"],
            "event_type": "FEE",
            "shares": None,
            "price": None,
            "amount": fees["ib_commission"].abs(),
            "currency": fees["currency"],
            "meta": fees.apply(
                lambda row: {"transaction_id": row["transaction_id"], "trade_id": row["trade_id"]},
                axis=1,
            ),
        }
    )

    return _finish_ledger(pd.concat([principal, fee_rows], ignore_index=True))


def _standardize_ibkr_cash_transactions(ibkr_cash_transactions: pd.DataFrame) -> pd.DataFrame:
    """Map `<CashTransaction>` rows (already validated through
    `brokers.models.IbkrCashTransaction`) onto the ledger. Only `level_of_detail
    == "DETAIL"` rows are kept. A blank `symbol` (deposits have none) becomes the `CASH` 
    pseudo-position. IBKR encodes direction as `amount`'s sign; the ledger never does that,
    so it's translated into `event_type` here and `amount` becomes a magnitude.
    """
    rows = ibkr_cash_transactions[ibkr_cash_transactions["level_of_detail"] == "DETAIL"]
    if rows.empty:
        return _empty_ledger()

    is_deposit_or_withdrawal = rows["type"] == "Deposits/Withdrawals"
    event_type = np.where(
        is_deposit_or_withdrawal,
        np.where(rows["amount"] >= 0, "DEPOSIT", "WITHDRAWAL"),
        rows["type"].map(_CASH_TRANSACTION_EVENT_TYPES).to_numpy(),
    )
    recognized = rows[pd.notna(event_type)]
    if recognized.empty:
        return _empty_ledger()

    events = pd.DataFrame(
        {
            "event_id": "ibkr:" + recognized["transaction_id"].astype(str),
            "event_datetime": recognized["date_time"],
            "symbol": recognized["symbol"].replace("", "CASH"),
            "event_type": event_type[pd.notna(event_type)],
            "shares": None,
            "price": None,
            "amount": recognized["amount"].abs(),
            "currency": recognized["currency"],
            "meta": recognized.apply(
                lambda row: {
                    "transaction_id": row["transaction_id"],
                    "type": row["type"],
                    "description": row["description"],
                    "action_id": row["action_id"],
                },
                axis=1,
            ),
        }
    )
    return _finish_ledger(events)


def statement_to_ledger(statement: ParsedStatement, config: IbkrFlexApiConfig) -> pd.DataFrame:
    """Native `<Trade>`/`<CashTransaction>` rows -> ledger rows (see
    `preprocessing.standardize_ibkr_ledger`/`standardize_ibkr_cash_transactions`).
    Left unsorted — sorts after merging only."""
    trades_df = pd.DataFrame(columns=list(IbkrTrade.model_fields))
    if statement.trades:
        trades_df = pd.DataFrame([t.model_dump() for t in statement.trades])
        trades_df["date_time"] = pd.to_datetime(trades_df["date_time"])

    cash_df = pd.DataFrame(columns=list(IbkrCashTransaction.model_fields))
    if statement.cash_transactions:
        cash_df = pd.DataFrame([t.model_dump() for t in statement.cash_transactions])
        cash_df["date_time"] = pd.to_datetime(cash_df["date_time"])

    return pd.concat(
        [
            _standardize_ibkr_trades(trades_df, config),
            _standardize_ibkr_cash_transactions(cash_df),
        ],
        ignore_index=True,
    )
