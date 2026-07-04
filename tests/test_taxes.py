from datetime import date, datetime

import polars as pl
import pytest

from trades.config import AppConfig
from trades.ledger.lots import ClosedLot, Lot, closed_lots_to_frame, lots_to_frame
from trades.ledger.taxes import after_tax_rate_lookup, annual_tax_report, flag_wash_sales, preview_sale

CONFIG = AppConfig()


def _closed_lot(
    lot_id: str,
    closed_at: str,
    realized_gain: float,
    symbol: str = "VOO",
    opened_at: str = "2024-01-01",
    term: str = "SHORT",
) -> ClosedLot:
    return ClosedLot(
        lot_id=lot_id,
        symbol=symbol,
        opened_at=datetime.fromisoformat(opened_at),
        closed_at=datetime.fromisoformat(closed_at),
        shares=1.0,
        cost_per_share=100.0,
        exit_price=100.0 + realized_gain,
        realized_gain=realized_gain,
        term=term,
        closed_by_event_id=f"sell-{lot_id}",
    )


def _closed_lots(*lots: ClosedLot) -> pl.DataFrame:
    return closed_lots_to_frame(list(lots))


def _lot(lot_id: str, opened_at: str, symbol: str = "VOO", shares: float = 1.0, cost_per_share: float = 100.0) -> Lot:
    return Lot(
        lot_id=lot_id,
        symbol=symbol,
        opened_at=datetime.fromisoformat(opened_at),
        shares=shares,
        cost_per_share=cost_per_share,
    )


def _open_lots(*lots: Lot) -> pl.DataFrame:
    return lots_to_frame(list(lots))


def _event(
    event_id: str,
    event_datetime: str,
    event_type: str,
    symbol: str = "CASH",
    amount: float = 0.0,
) -> dict:
    return {
        "event_id": event_id,
        "event_datetime": datetime.fromisoformat(event_datetime),
        "symbol": symbol,
        "event_type": event_type,
        "shares": None,
        "price": None,
        "amount": amount,
        "currency": "USD",
        "meta": {},
    }


def _ledger(*events: dict) -> pl.DataFrame:
    return (
        pl.DataFrame(list(events))
        if events
        else pl.DataFrame(
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
    )


def test_annual_tax_report_splits_gains_by_term_and_year() -> None:
    lots = _closed_lots(
        _closed_lot("1", "2025-06-01", realized_gain=100.0, term="LONG"),
        _closed_lot("2", "2025-07-01", realized_gain=-30.0, term="SHORT"),
        _closed_lot("3", "2026-01-01", realized_gain=50.0, term="SHORT"),
    )
    report = annual_tax_report(lots, _ledger(), CONFIG, current_regime="RESIDENT", status_change_date=None)

    row_2025 = report.filter(pl.col("year") == 2025).row(0, named=True)
    assert row_2025["long_term_gain_usd"] == pytest.approx(100.0)
    assert row_2025["short_term_gain_usd"] == pytest.approx(-30.0)
    row_2026 = report.filter(pl.col("year") == 2026).row(0, named=True)
    assert row_2026["short_term_gain_usd"] == pytest.approx(50.0)


def test_annual_tax_report_splits_a_year_at_the_status_change_date() -> None:
    lots = _closed_lots(
        _closed_lot("1", "2025-03-01", realized_gain=100.0, term="SHORT"),
        _closed_lot("2", "2025-09-01", realized_gain=200.0, term="SHORT"),
    )
    report = annual_tax_report(lots, _ledger(), CONFIG, current_regime="RESIDENT", status_change_date=date(2025, 6, 1))

    assert len(report) == 2
    before = report.filter(pl.col("regime") == "NRA").row(0, named=True)
    after = report.filter(pl.col("regime") == "RESIDENT").row(0, named=True)
    assert before["short_term_gain_usd"] == pytest.approx(100.0)
    assert after["short_term_gain_usd"] == pytest.approx(200.0)


def test_annual_tax_report_classifies_dividends_by_tax_character() -> None:
    config = AppConfig(tax={"tax_character": {"VOO": "qualified_dividend", "BND": "ordinary_interest"}})
    ledger = _ledger(
        _event("d1", "2025-06-01", "DIVIDEND", symbol="VOO", amount=40.0),
        _event("d2", "2025-06-01", "DIVIDEND", symbol="BND", amount=15.0),
        _event("d3", "2025-06-01", "DIVIDEND", symbol="UNKNOWN", amount=5.0),
    )
    report = annual_tax_report(_closed_lots(), ledger, config, current_regime="RESIDENT", status_change_date=None)

    row = report.row(0, named=True)
    assert row["qualified_dividends_usd"] == pytest.approx(40.0)
    assert row["ordinary_interest_usd"] == pytest.approx(15.0)
    assert row["ordinary_dividends_usd"] == pytest.approx(5.0)  # unmapped symbol defaults to ordinary


def test_annual_tax_report_sums_withholding_tax() -> None:
    ledger = _ledger(
        _event("w1", "2025-06-01", "WITHHOLDING", symbol="VOO", amount=10.0),
        _event("w2", "2025-08-01", "WITHHOLDING", symbol="VOO", amount=4.0),
    )
    report = annual_tax_report(_closed_lots(), ledger, CONFIG, current_regime="RESIDENT", status_change_date=None)
    assert report.row(0, named=True)["withholding_tax_usd"] == pytest.approx(14.0)


def test_annual_tax_report_keeps_year_as_an_integer() -> None:
    lots = _closed_lots(_closed_lot("1", "2025-06-01", realized_gain=100.0, term="LONG"))
    ledger = _ledger(_event("d1", "2025-07-01", "DIVIDEND", symbol="VOO", amount=5.0))
    report = annual_tax_report(lots, ledger, CONFIG, current_regime="RESIDENT", status_change_date=None)
    assert report.schema["year"] == pl.Int32
    assert report.row(0, named=True)["year"] == 2025


def test_annual_tax_report_with_no_activity_is_empty() -> None:
    report = annual_tax_report(_closed_lots(), _ledger(), CONFIG, current_regime="RESIDENT", status_change_date=None)
    assert report.is_empty()
    assert report.schema["year"] == pl.Int32


def test_flag_wash_sales_flags_a_loss_with_a_nearby_repurchase() -> None:
    lots = _closed_lots(_closed_lot("buy-1", "2025-06-01", realized_gain=-50.0))
    ledger = _ledger(
        _event("buy-1", "2024-01-01", "BUY", symbol="VOO"),
        _event("buy-2", "2025-06-10", "BUY", symbol="VOO"),
    )
    result = flag_wash_sales(lots, ledger, CONFIG)
    assert result.row(0, named=True)["wash_sale_flag"] is True


def test_flag_wash_sales_does_not_flag_a_gain() -> None:
    lots = _closed_lots(_closed_lot("buy-1", "2025-06-01", realized_gain=50.0))
    ledger = _ledger(
        _event("buy-1", "2024-01-01", "BUY", symbol="VOO"),
        _event("buy-2", "2025-06-10", "BUY", symbol="VOO"),
    )
    result = flag_wash_sales(lots, ledger, CONFIG)
    assert result.row(0, named=True)["wash_sale_flag"] is False


def test_flag_wash_sales_does_not_flag_the_lots_own_opening_buy() -> None:
    lots = _closed_lots(_closed_lot("buy-1", "2025-06-01", realized_gain=-50.0, opened_at="2025-05-20"))
    ledger = _ledger(_event("buy-1", "2025-05-20", "BUY", symbol="VOO"))
    result = flag_wash_sales(lots, ledger, CONFIG)
    assert result.row(0, named=True)["wash_sale_flag"] is False


def test_flag_wash_sales_ignores_a_repurchase_outside_the_window() -> None:
    lots = _closed_lots(_closed_lot("buy-1", "2025-06-01", realized_gain=-50.0))
    ledger = _ledger(
        _event("buy-1", "2024-01-01", "BUY", symbol="VOO"),
        _event("buy-2", "2025-08-01", "BUY", symbol="VOO"),
    )
    result = flag_wash_sales(lots, ledger, CONFIG)
    assert result.row(0, named=True)["wash_sale_flag"] is False


def test_flag_wash_sales_treats_configured_similar_symbols_as_related() -> None:
    config = AppConfig(tax={"wash_sale_similar_symbols": {"VOO": ["IVV"]}})
    lots = _closed_lots(_closed_lot("buy-1", "2025-06-01", realized_gain=-50.0, symbol="VOO"))
    ledger = _ledger(
        _event("buy-1", "2024-01-01", "BUY", symbol="VOO"),
        _event("buy-2", "2025-06-05", "BUY", symbol="IVV"),
    )
    result = flag_wash_sales(lots, ledger, config)
    assert result.row(0, named=True)["wash_sale_flag"] is True


def test_preview_sale_computes_term_and_unrealized_gain() -> None:
    lots = _open_lots(_lot("1", "2024-01-01", cost_per_share=100.0))
    result = preview_sale(lots, _ledger(), lambda symbol, as_of: 150.0, as_of=date(2026, 1, 1), config=CONFIG)
    row = result.row(0, named=True)
    assert row["term"] == "LONG"
    assert row["unrealized_gain_usd"] == pytest.approx(50.0)
    assert row["would_wash_sale"] is False


def test_preview_sale_flags_a_would_be_wash_sale() -> None:
    lots = _open_lots(_lot("1", "2024-01-01", cost_per_share=100.0))
    ledger = _ledger(_event("buy-2", "2025-12-20", "BUY", symbol="VOO"))
    result = preview_sale(lots, ledger, lambda symbol, as_of: 80.0, as_of=date(2026, 1, 1), config=CONFIG)
    assert result.row(0, named=True)["would_wash_sale"] is True


def test_preview_sale_raises_on_missing_price() -> None:
    lots = _open_lots(_lot("1", "2024-01-01"))
    with pytest.raises(ValueError, match="No price available"):
        preview_sale(lots, _ledger(), lambda symbol, as_of: None, as_of=date(2026, 1, 1), config=CONFIG)


def test_after_tax_rate_lookup_passes_through_for_nra() -> None:
    wrapped = after_tax_rate_lookup(
        lambda day: 0.05, marginal_ordinary_rate=0.3, current_regime="NRA", status_change_date=None
    )
    assert wrapped(date(2026, 1, 1)) == pytest.approx(0.05)


def test_after_tax_rate_lookup_reduces_rate_for_resident() -> None:
    wrapped = after_tax_rate_lookup(
        lambda day: 0.05, marginal_ordinary_rate=0.3, current_regime="RESIDENT", status_change_date=None
    )
    assert wrapped(date(2026, 1, 1)) == pytest.approx(0.05 * 0.7)


def test_after_tax_rate_lookup_switches_at_the_status_change_date() -> None:
    wrapped = after_tax_rate_lookup(
        lambda day: 0.05, marginal_ordinary_rate=0.3, current_regime="RESIDENT", status_change_date=date(2025, 6, 1)
    )
    assert wrapped(date(2025, 1, 1)) == pytest.approx(0.05)  # still NRA, untaxed
    assert wrapped(date(2025, 12, 1)) == pytest.approx(0.05 * 0.7)  # resident now, taxed
