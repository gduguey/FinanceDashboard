from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from trades import transactions
from trades.config import AppConfig
from trades.models import RawTrade

DATA_CSV = Path(__file__).resolve().parents[1] / "data" / "brokers" / "ibkr" / "manual_20260701_trades.csv"
CONFIG = AppConfig()


def test_raw_trade_parses_currency_string() -> None:
    trade = RawTrade.model_validate({"Date": "2026-06-01", "Symbol": "voo", "Shares": "9", "USD Spent": "$6,286.59"})
    assert trade.usd_spent == pytest.approx(6286.59)
    assert trade.symbol == "VOO"


def test_raw_trade_rejects_non_positive_shares() -> None:
    with pytest.raises(ValueError, match="Shares"):
        RawTrade.model_validate({"Date": "2026-06-01", "Symbol": "VOO", "Shares": "0", "USD Spent": "$1.00"})


def test_load_raw_trades_matches_csv_row_count() -> None:
    df = transactions.load_raw_trades(DATA_CSV)
    with DATA_CSV.open() as f:
        assert len(df) == sum(1 for _ in f) - 1
    assert set(df.columns) == {"trade_date", "symbol", "shares", "usd_spent"}


def test_enrich_trades_computes_usd_per_share() -> None:
    df = pl.DataFrame([{"trade_date": date(2026, 1, 1), "symbol": "VOO", "shares": 2.0, "usd_spent": 10.0}])
    enriched = transactions.enrich_trades(df)
    assert enriched["usd_per_share"][0] == pytest.approx(5.0)


def test_enrich_trades_accepts_a_lazyframe() -> None:
    df = pl.DataFrame([{"trade_date": date(2026, 1, 1), "symbol": "VOO", "shares": 2.0, "usd_spent": 10.0}])
    enriched = transactions.enrich_trades(df.lazy())
    assert isinstance(enriched, pl.LazyFrame)
    assert enriched.collect()["usd_per_share"][0] == pytest.approx(5.0)


def _trade(date_str: str, symbol: str, shares: float, usd_per_share: float) -> dict:
    return {
        "trade_date": date.fromisoformat(date_str),
        "symbol": symbol,
        "shares": shares,
        "usd_spent": shares * usd_per_share,
        "usd_per_share": usd_per_share,
    }


def test_aggregate_merges_close_prices_same_day_same_symbol() -> None:
    df = pl.DataFrame([
        _trade("2026-05-05", "VOO", 1.0, 665.87),
        _trade("2026-05-05", "VOO", 1.0, 665.86),
        _trade("2026-05-05", "VOO", 0.6281, 665.90),
    ])
    out = transactions.aggregate_same_day_trades(df, CONFIG)
    assert len(out) == 1
    row = out.row(0, named=True)
    assert row["shares"] == pytest.approx(2.6281)
    assert row["n_trades"] == 3
    assert row["usd_per_share"] == pytest.approx(row["usd_spent"] / row["shares"])


def test_aggregate_keeps_distinct_prices_separate() -> None:
    df = pl.DataFrame([
        _trade("2026-05-05", "VOO", 1.0, 100.0),
        _trade("2026-05-05", "VOO", 1.0, 120.0),  # 20% away: must not merge
    ])
    out = transactions.aggregate_same_day_trades(df, CONFIG)
    assert len(out) == 2
    assert set(out["n_trades"]) == {1}


def test_aggregate_does_not_merge_across_symbols_or_dates() -> None:
    df = pl.DataFrame([
        _trade("2026-05-05", "VOO", 1.0, 100.0),
        _trade("2026-05-06", "VOO", 1.0, 100.0),
        _trade("2026-05-05", "BND", 1.0, 100.0),
    ])
    out = transactions.aggregate_same_day_trades(df, CONFIG)
    assert len(out) == 3


def test_monthly_invested_totals_match_raw_sum() -> None:
    df = pl.DataFrame([
        _trade("2026-01-27", "VOO", 1.0, 96.06),
        _trade("2026-04-10", "BND", 1.0, 125.0),
    ])
    pivot = transactions.monthly_invested(df)
    assert pivot["Total"].sum() == pytest.approx(df["usd_spent"].sum())


def test_daily_investment_timeline_gap_in_days() -> None:
    df = pl.DataFrame([
        _trade("2026-01-27", "VOO", 1.0, 96.06),
        _trade("2026-04-10", "BND", 1.0, 125.0),
    ])
    daily = transactions.daily_investment_timeline(df)
    assert daily["days_since_previous_investment"][1] == 73
    assert daily["cumulative_usd_spent"][-1] == pytest.approx(df["usd_spent"].sum())


def test_pie_breakdown_by_symbol_and_by_date() -> None:
    df = pl.DataFrame([
        _trade("2026-01-27", "VOO", 1.0, 96.06),
        _trade("2026-04-10", "VOO", 1.0, 125.0),
        _trade("2026-04-10", "BND", 1.0, 10.0),
    ])
    by_symbol = transactions.pie_breakdown(df, symbol=None)
    assert set(by_symbol["symbol"]) == {"VOO", "BND"}

    by_date = transactions.pie_breakdown(df, symbol="VOO")
    assert len(by_date) == 2

    options = transactions.pie_chart_options(df)
    assert "Whole portfolio — by symbol" in options
    assert "VOO — by date" in options


def test_standardize_ibkr_trades_excludes_cash_and_non_buy_events() -> None:
    ledger = pl.DataFrame([
        {
            "event_id": "b1",
            "event_datetime": datetime(2026, 1, 1),
            "symbol": "VOO",
            "event_type": "BUY",
            "shares": 2.0,
            "price": 500.0,
            "amount": 1000.0,
            "currency": "USD",
            "meta": {},
        },
        {
            "event_id": "d1",
            "event_datetime": datetime(2026, 1, 1),
            "symbol": "CASH",
            "event_type": "DEPOSIT",
            "shares": None,
            "price": None,
            "amount": 1000.0,
            "currency": "USD",
            "meta": {},
        },
    ])
    standardized = transactions.standardize_ibkr_trades(ledger, CONFIG)
    assert len(standardized) == 1
    assert standardized.row(0, named=True)["symbol"] == "VOO"


def test_standardize_ibkr_trades_accepts_a_lazyframe() -> None:
    ledger = pl.DataFrame([
        {
            "event_id": "b1",
            "event_datetime": datetime(2026, 1, 1),
            "symbol": "VOO",
            "event_type": "BUY",
            "shares": 2.0,
            "price": 500.0,
            "amount": 1000.0,
            "currency": "USD",
            "meta": {},
        }
    ])
    standardized = transactions.standardize_ibkr_trades(ledger.lazy(), CONFIG)
    assert isinstance(standardized, pl.LazyFrame)
    assert len(standardized.collect()) == 1
