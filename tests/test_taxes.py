from datetime import date, datetime

import polars as pl
import pytest

from trades.config import AppConfig
from trades.ledger.lots import ClosedLot, Lot, closed_lots_to_frame, lots_to_frame
from trades.ledger.taxes import (
    after_tax_rate_lookup,
    annual_tax_report,
    classify_dividend,
    flag_wash_sales,
    liquidation_gain_buckets,
    liquidation_tax_usd,
    preview_sale,
    tax_owed_by_year_and_regime,
)

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
    shares: float | None = None,
    meta: dict | None = None,
) -> dict:
    return {
        "event_id": event_id,
        "event_datetime": datetime.fromisoformat(event_datetime),
        "symbol": symbol,
        "event_type": event_type,
        "shares": shares,
        "price": None,
        "amount": amount,
        "currency": "USD",
        "meta": meta or {},
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


def test_classify_dividend_explicit_override_wins_over_everything() -> None:
    config = AppConfig(tax={"tax_character": {"BND": "ordinary_interest"}})
    assert classify_dividend("BND", {}, _ledger(), config) == "ordinary_interest"


def test_classify_dividend_interest_income_type_is_never_qualified() -> None:
    ledger = _ledger(_event("buy1", "2020-01-01", "BUY", symbol="VMFXX", shares=10.0))
    meta = {"income_type": "interest", "ex_date": "2026-01-01"}
    assert classify_dividend("VMFXX", meta, ledger, CONFIG) == "ordinary_interest"


def test_classify_dividend_substitute_payment_is_never_qualified() -> None:
    ledger = _ledger(_event("buy1", "2020-01-01", "BUY", symbol="VOO", shares=10.0))
    meta = {"income_type": "substitute_payment", "ex_date": "2026-01-01"}
    assert classify_dividend("VOO", meta, ledger, CONFIG) == "ordinary_dividend"


def test_classify_dividend_honors_ibkrs_unqualified_label() -> None:
    ledger = _ledger(_event("buy1", "2020-01-01", "BUY", symbol="BND", shares=10.0))
    meta = {"dividend_type": "Unqualified Dividend", "ex_date": "2026-01-01"}
    assert classify_dividend("BND", meta, ledger, CONFIG) == "ordinary_dividend"


def test_classify_dividend_with_no_ex_date_defaults_to_ordinary() -> None:
    assert classify_dividend("VOO", {}, _ledger(), CONFIG) == "ordinary_dividend"


def test_classify_dividend_qualifies_when_held_continuously_through_the_window() -> None:
    ledger = _ledger(_event("buy1", "2025-01-01", "BUY", symbol="VOO", shares=10.0))
    meta = {"ex_date": "2025-06-01"}
    assert classify_dividend("VOO", meta, ledger, CONFIG) == "qualified_dividend"


def test_classify_dividend_does_not_qualify_when_sold_shortly_after_buying() -> None:
    ledger = _ledger(
        _event("buy1", "2025-05-20", "BUY", symbol="VOO", shares=10.0),
        _event("sell1", "2025-06-10", "SELL", symbol="VOO", shares=10.0),
    )
    meta = {"ex_date": "2025-06-01"}
    assert classify_dividend("VOO", meta, ledger, CONFIG) == "ordinary_dividend"


def test_classify_dividend_ignores_a_later_sell_and_rebuy_outside_the_run() -> None:
    # Bought well before the ex-date and held through it; a much later
    # sale (long after the 121-day window closes) shouldn't matter.
    ledger = _ledger(
        _event("buy1", "2025-01-01", "BUY", symbol="VOO", shares=10.0),
        _event("sell1", "2026-01-01", "SELL", symbol="VOO", shares=10.0),
    )
    meta = {"ex_date": "2025-06-01"}
    assert classify_dividend("VOO", meta, ledger, CONFIG) == "qualified_dividend"


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


def _annual_row(
    year: int = 2025,
    regime: str = "RESIDENT",
    long_term_gain_usd: float = 0.0,
    short_term_gain_usd: float = 0.0,
    qualified_dividends_usd: float = 0.0,
    ordinary_dividends_usd: float = 0.0,
    ordinary_interest_usd: float = 0.0,
    withholding_tax_usd: float = 0.0,
) -> pl.DataFrame:
    return pl.DataFrame({
        "year": [year],
        "regime": [regime],
        "long_term_gain_usd": [long_term_gain_usd],
        "short_term_gain_usd": [short_term_gain_usd],
        "qualified_dividends_usd": [qualified_dividends_usd],
        "ordinary_dividends_usd": [ordinary_dividends_usd],
        "ordinary_interest_usd": [ordinary_interest_usd],
        "withholding_tax_usd": [withholding_tax_usd],
    })


def test_tax_owed_taxes_a_residents_gains_and_dividends_at_their_own_rates() -> None:
    annual = _annual_row(
        regime="RESIDENT",
        long_term_gain_usd=1000.0,
        short_term_gain_usd=500.0,
        qualified_dividends_usd=200.0,
        ordinary_dividends_usd=100.0,
        ordinary_interest_usd=50.0,
    )
    owed = tax_owed_by_year_and_regime(
        annual, marginal_ordinary_rate=0.24, qualified_ltcg_rate=0.15, nra_dividend_tax_rate=0.30
    )
    row = owed.row(0, named=True)
    assert row["capital_gains_tax_usd"] == pytest.approx(1000.0 * 0.15 + 500.0 * 0.24)
    assert row["dividend_tax_usd"] == pytest.approx(200.0 * 0.15 + 100.0 * 0.24 + 50.0 * 0.24)
    assert row["total_tax_usd"] == pytest.approx(row["capital_gains_tax_usd"] + row["dividend_tax_usd"])


def test_tax_owed_floors_a_residents_net_loss_at_zero() -> None:
    annual = _annual_row(regime="RESIDENT", long_term_gain_usd=-500.0, short_term_gain_usd=-100.0)
    owed = tax_owed_by_year_and_regime(
        annual, marginal_ordinary_rate=0.24, qualified_ltcg_rate=0.15, nra_dividend_tax_rate=0.30
    )
    assert owed.row(0, named=True)["capital_gains_tax_usd"] == pytest.approx(0.0)


def test_tax_owed_charges_a_nonresident_alien_no_capital_gains_tax() -> None:
    annual = _annual_row(regime="NRA", long_term_gain_usd=1000.0, short_term_gain_usd=500.0)
    owed = tax_owed_by_year_and_regime(
        annual, marginal_ordinary_rate=0.24, qualified_ltcg_rate=0.15, nra_dividend_tax_rate=0.30
    )
    assert owed.row(0, named=True)["capital_gains_tax_usd"] == pytest.approx(0.0)


def test_tax_owed_charges_a_nonresident_alien_the_flat_dividend_rate_and_exempts_interest() -> None:
    annual = _annual_row(
        regime="NRA", qualified_dividends_usd=200.0, ordinary_dividends_usd=100.0, ordinary_interest_usd=1000.0
    )
    owed = tax_owed_by_year_and_regime(
        annual, marginal_ordinary_rate=0.24, qualified_ltcg_rate=0.15, nra_dividend_tax_rate=0.15
    )
    assert owed.row(0, named=True)["dividend_tax_usd"] == pytest.approx((200.0 + 100.0) * 0.15)


def test_tax_owed_computes_balance_due_against_withholding_already_paid() -> None:
    annual = _annual_row(regime="NRA", ordinary_dividends_usd=100.0, withholding_tax_usd=10.0)
    owed = tax_owed_by_year_and_regime(
        annual, marginal_ordinary_rate=0.24, qualified_ltcg_rate=0.15, nra_dividend_tax_rate=0.30
    )
    row = owed.row(0, named=True)
    assert row["total_tax_usd"] == pytest.approx(30.0)
    assert row["balance_due_usd"] == pytest.approx(20.0)


def _preview_row(term: str, unrealized_gain_usd: float) -> pl.DataFrame:
    return pl.DataFrame({
        "lot_id": ["1"],
        "symbol": ["VOO"],
        "shares": [1.0],
        "days_held": [400],
        "term": [term],
        "unrealized_gain_usd": [unrealized_gain_usd],
        "would_wash_sale": [False],
    })


def test_liquidation_gain_buckets_sums_by_term_and_floors_a_loss_at_zero() -> None:
    previews = pl.concat([_preview_row("LONG", 1000.0), _preview_row("SHORT", -400.0)])
    long_term_gain, short_term_gain = liquidation_gain_buckets(previews)
    assert long_term_gain == pytest.approx(1000.0)
    assert short_term_gain == pytest.approx(0.0)


def test_liquidation_gain_buckets_with_no_open_lots_is_zero() -> None:
    previews = pl.DataFrame(
        schema={
            "lot_id": pl.Utf8,
            "symbol": pl.Utf8,
            "shares": pl.Float64,
            "days_held": pl.Int64,
            "term": pl.Utf8,
            "unrealized_gain_usd": pl.Float64,
            "would_wash_sale": pl.Boolean,
        }
    )
    assert liquidation_gain_buckets(previews) == (0.0, 0.0)


def test_liquidation_tax_is_zero_for_a_nonresident_alien() -> None:
    previews = _preview_row("LONG", 1000.0)
    assert liquidation_tax_usd(previews, "NRA", marginal_ordinary_rate=0.24, qualified_ltcg_rate=0.15) == pytest.approx(
        0.0
    )


def test_liquidation_tax_applies_the_right_rate_by_term_for_a_resident() -> None:
    previews = pl.concat([_preview_row("LONG", 1000.0), _preview_row("SHORT", 400.0)])
    tax = liquidation_tax_usd(previews, "RESIDENT", marginal_ordinary_rate=0.24, qualified_ltcg_rate=0.15)
    assert tax == pytest.approx(1000.0 * 0.15 + 400.0 * 0.24)


def test_liquidation_tax_floors_a_net_unrealized_loss_at_zero() -> None:
    previews = _preview_row("LONG", -500.0)
    assert liquidation_tax_usd(
        previews, "RESIDENT", marginal_ordinary_rate=0.24, qualified_ltcg_rate=0.15
    ) == pytest.approx(0.0)


def test_liquidation_tax_is_zero_with_no_open_lots() -> None:
    previews = pl.DataFrame(
        schema={
            "lot_id": pl.Utf8,
            "symbol": pl.Utf8,
            "shares": pl.Float64,
            "days_held": pl.Int64,
            "term": pl.Utf8,
            "unrealized_gain_usd": pl.Float64,
            "would_wash_sale": pl.Boolean,
        }
    )
    assert liquidation_tax_usd(
        previews, "RESIDENT", marginal_ordinary_rate=0.24, qualified_ltcg_rate=0.15
    ) == pytest.approx(0.0)
