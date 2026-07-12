"""Map native IBKR `<Trade>`/`<CashTransaction>` rows onto the canonical ledger."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from trades.models import LedgerEvent

if TYPE_CHECKING:
    from trades.brokers.ibkr.api import ParsedStatement
    from trades.config import AppConfig

_CASH_TRANSACTION_EVENT_TYPES = {
    "Dividends": "DIVIDEND",
    "Payment In Lieu Of Dividends": "DIVIDEND",
    "Withholding Tax": "WITHHOLDING",
    "Broker Interest Received": "DIVIDEND",
    "Bond Interest Received": "DIVIDEND",
    "Broker Interest Paid": "FEE",
    "Other Fees": "FEE",
    "Commission Adjustments": "FEE",
}

# Every one of these IBKR types becomes a canonical `DIVIDEND` ledger event
# (see `_CASH_TRANSACTION_EVENT_TYPES` above), but they aren't the same
# thing for tax purposes: real stock dividends can be qualified, interest
# never can be, and a substitute payment in lieu of a dividend (paid when a
# lent-out security's dividend is passed through instead of the dividend
# itself) is explicitly disqualified by the tax code regardless of holding
# period. Tagged here, at the one place that knows IBKR's own vocabulary,
# so `ledger.taxes` never has to recognize an IBKR-specific string.
_DIVIDEND_INCOME_TYPES = {
    "Broker Interest Received": "interest",
    "Bond Interest Received": "interest",
    "Payment In Lieu Of Dividends": "substitute_payment",
}


def _empty_ledger() -> pl.DataFrame:
    """Return an empty frame shaped like `LedgerEvent.polars_schema`.

    Returns
    -------
    polars.DataFrame
    """
    return pl.DataFrame(schema=LedgerEvent.polars_schema)


def _events_to_frame(events: list[LedgerEvent]) -> pl.DataFrame:
    """Convert validated `LedgerEvent` models into a sorted polars DataFrame.

    Returns
    -------
    polars.DataFrame
    """
    if not events:
        return _empty_ledger()
    frame = pl.DataFrame([event.model_dump() for event in events], schema=LedgerEvent.polars_schema)
    return frame.sort("event_datetime", "symbol", "event_id")


def _trade_meta(row: dict[str, object]) -> dict[str, str]:
    """Build one BUY/SELL event's `meta` dict from its raw IBKR trade row.

    Returns
    -------
    dict[str, str]
    """
    meta = {"transaction_id": str(row["transaction_id"]), "trade_id": str(row["trade_id"])}
    if row["notes"]:
        meta["notes"] = str(row["notes"])
    if row["is_drip"]:
        meta["drip_reinvestment"] = "true"
    return meta


def _standardize_ibkr_trades(ibkr_trades: pl.DataFrame, config: AppConfig) -> pl.DataFrame:
    """Map `<Trade>` rows (validated via `brokers.models.IbkrTrade`) onto ledger events.

    `BUY (Ca.)`/`SELL (Ca.)` cancellations are dropped.

    - `BUY`/`SELL` — one per kept fill; `amount` is the principal (`trade_price x quantity`).
    - `FEE` — a second event on the same transaction when `ib_commission` is
      non-zero, kept separate so commission never inflates a lot's cost basis.
    - DRIP flag — a `BUY` carrying `config.ibkr.drip_reinvestment_note_code`
      gets `meta["drip_reinvestment"] = "true"`.

    Parameters
    ----------
    ibkr_trades
        Native `<Trade>` rows.
    config
        Application configuration; `config.ibkr.drip_reinvestment_note_code` is read.

    Returns
    -------
    polars.DataFrame
        Ledger-shaped rows, validated through `LedgerEvent`.
    """
    ibkr_trades = ibkr_trades.filter(pl.col("level_of_detail") == "EXECUTION")
    trades = ibkr_trades.filter(
        pl.col("buy_sell").is_in(["BUY", "SELL"])
    )  # Exclude cancellations (`BUY (Ca.)`/`SELL (Ca.)`)
    if trades.is_empty():
        return _empty_ledger()

    principal = trades.select(
        event_id=pl.concat_str([pl.lit("ibkr:"), pl.col("transaction_id")]),
        event_datetime=pl.col("date_time"),
        symbol=pl.col("symbol"),
        event_type=pl.col("buy_sell"),
        shares=pl.col("quantity").abs(),
        price=pl.col("trade_price"),
        amount=pl.col("trade_money").abs(),
        currency=pl.col("currency"),
        transaction_id=pl.col("transaction_id"),
        trade_id=pl.col("trade_id"),
        notes=pl.col("notes"),
        is_drip=pl.col("buy_sell").eq("BUY")
        & pl.col("notes").str.split(";").list.contains(pl.lit(config.ibkr.drip_reinvestment_note_code)),
    )
    fees = trades.filter(pl.col("ib_commission") != 0).select(
        event_id=pl.concat_str([pl.lit("ibkr:"), pl.col("transaction_id"), pl.lit(":fee")]),
        event_datetime=pl.col("date_time"),
        symbol=pl.col("symbol"),
        event_type=pl.lit("FEE"),
        shares=pl.lit(None, dtype=pl.Float64),
        price=pl.lit(None, dtype=pl.Float64),
        amount=pl.col("ib_commission").abs(),
        currency=pl.col("currency"),
        transaction_id=pl.col("transaction_id"),
        trade_id=pl.col("trade_id"),
        notes=pl.lit(None, dtype=pl.Utf8),
        is_drip=pl.lit(value=False),
    )

    events = [
        LedgerEvent(
            event_id=row["event_id"],
            event_datetime=row["event_datetime"],
            symbol=row["symbol"],
            event_type=row["event_type"],
            shares=row["shares"],
            price=row["price"],
            amount=row["amount"],
            currency=row["currency"],
            meta=_trade_meta(row),
        )
        for row in pl.concat([principal, fees], how="vertical").iter_rows(named=True)
    ]
    return _events_to_frame(events)


def _cash_transaction_meta(row: dict[str, object]) -> dict[str, str]:
    """Build one cash event's `meta` dict from its raw IBKR cash-transaction row.

    Returns
    -------
    dict[str, str]
    """
    meta = {
        "transaction_id": str(row["transaction_id"]),
        "type": str(row["type"]),
        "description": str(row["description"]),
        "action_id": str(row["action_id"]),
    }
    if row["event_type"] == "DIVIDEND":
        if row["dividend_type"]:
            meta["dividend_type"] = str(row["dividend_type"])
        if row["ex_date"]:
            meta["ex_date"] = str(row["ex_date"])
        income_type = _DIVIDEND_INCOME_TYPES.get(str(row["type"]))
        if income_type:
            meta["income_type"] = income_type
    return meta


def _standardize_ibkr_cash_transactions(ibkr_cash_transactions: pl.DataFrame) -> pl.DataFrame:
    """Map `<CashTransaction>` rows onto ledger events.

    Only `level_of_detail == "DETAIL"` rows are kept (see
    `brokers.models.IbkrCashTransaction`). A blank `symbol` (deposits have
    none) becomes the `CASH` pseudo-position. IBKR encodes direction as
    `amount`'s sign; the ledger never does that, so it is translated into
    `event_type` here and `amount` becomes a magnitude. A `DIVIDEND` row
    additionally carries IBKR's own `dividend_type` label, the dividend's
    `ex_date`, and — since interest and substitute-dividend payments also
    become `DIVIDEND` events — an `income_type` tag distinguishing them
    from a real stock dividend, all in `meta`, for classifying the payment
    as qualified or ordinary later (see `ledger.taxes`).

    Parameters
    ----------
    ibkr_cash_transactions
        Native `<CashTransaction>` rows.

    Returns
    -------
    polars.DataFrame
        Ledger-shaped rows, validated through `LedgerEvent`.
    """
    rows = ibkr_cash_transactions.filter(pl.col("level_of_detail") == "DETAIL")
    if rows.is_empty():
        return _empty_ledger()

    recognized = rows.with_columns(
        event_type=pl
        .when(pl.col("type") == "Deposits/Withdrawals")
        .then(pl.when(pl.col("amount") >= 0).then(pl.lit("DEPOSIT")).otherwise(pl.lit("WITHDRAWAL")))
        .otherwise(pl.col("type").replace_strict(_CASH_TRANSACTION_EVENT_TYPES, default=None, return_dtype=pl.Utf8))
    ).filter(pl.col("event_type").is_not_null())
    if recognized.is_empty():
        return _empty_ledger()

    events = [
        LedgerEvent(
            event_id=f"ibkr:{row['transaction_id']}",
            event_datetime=row["date_time"],
            symbol=row["symbol"] or "CASH",
            event_type=row["event_type"],
            amount=abs(row["amount"]),
            currency=row["currency"],
            meta=_cash_transaction_meta(row),
        )
        for row in recognized.iter_rows(named=True)
    ]
    return _events_to_frame(events)


def statement_to_ledger(statement: ParsedStatement, config: AppConfig) -> pl.DataFrame:
    """Convert one parsed Flex statement's `<Trade>`/`<CashTransaction>` rows into ledger rows.

    Parameters
    ----------
    statement
        The parsed Flex statement.
    config
        Application configuration; `config.ibkr` is read.

    Returns
    -------
    polars.DataFrame
        Ledger-shaped rows, unsorted (the caller sorts after merging with the existing ledger).
    """
    trades = pl.DataFrame([trade.model_dump() for trade in statement.trades]) if statement.trades else pl.DataFrame()
    cash_transactions = (
        pl.DataFrame([transaction.model_dump() for transaction in statement.cash_transactions])
        if statement.cash_transactions
        else pl.DataFrame()
    )
    return pl.concat(
        [
            _standardize_ibkr_trades(trades, config) if not trades.is_empty() else _empty_ledger(),
            _standardize_ibkr_cash_transactions(cash_transactions)
            if not cash_transactions.is_empty()
            else _empty_ledger(),
        ],
        how="vertical",
    )
