from datetime import datetime

import pytest

from trades.ledger.lots import Lot, apply_split, consume_fifo


def _lot(lot_id: str, opened_at: str, shares: float, cost_per_share: float, symbol: str = "VOO") -> Lot:
    return Lot(
        lot_id=lot_id,
        symbol=symbol,
        opened_at=datetime.fromisoformat(opened_at),
        shares=shares,
        cost_per_share=cost_per_share,
    )


def test_consume_fifo_consumes_oldest_lot_first() -> None:
    lots = [
        _lot("2", "2026-02-01", shares=5.0, cost_per_share=100.0),
        _lot("1", "2026-01-01", shares=5.0, cost_per_share=90.0),
    ]
    remaining, closed = consume_fifo(
        lots,
        shares_to_consume=3.0,
        exit_price=110.0,
        closed_at=datetime(2026, 3, 1),
        closed_by_event_id="sell-1",
        long_term_holding_days=365,
    )
    assert len(closed) == 1
    assert closed[0].lot_id == "1"
    assert closed[0].shares == pytest.approx(3.0)
    assert len(remaining) == 2
    untouched = next(lot for lot in remaining if lot.lot_id == "2")
    assert untouched.shares == pytest.approx(5.0)
    reduced = next(lot for lot in remaining if lot.lot_id == "1")
    assert reduced.shares == pytest.approx(2.0)


def test_consume_fifo_spans_multiple_lots() -> None:
    lots = [
        _lot("1", "2026-01-01", shares=2.0, cost_per_share=90.0),
        _lot("2", "2026-02-01", shares=5.0, cost_per_share=100.0),
    ]
    remaining, closed = consume_fifo(
        lots,
        shares_to_consume=4.0,
        exit_price=110.0,
        closed_at=datetime(2026, 3, 1),
        closed_by_event_id="sell-1",
        long_term_holding_days=365,
    )
    assert [c.lot_id for c in closed] == ["1", "2"]
    assert closed[0].shares == pytest.approx(2.0)
    assert closed[1].shares == pytest.approx(2.0)
    assert len(remaining) == 1
    assert remaining[0].shares == pytest.approx(3.0)


def test_consume_fifo_fully_closes_a_lot_and_drops_it() -> None:
    lots = [_lot("1", "2026-01-01", shares=2.0, cost_per_share=90.0)]
    remaining, closed = consume_fifo(
        lots,
        shares_to_consume=2.0,
        exit_price=110.0,
        closed_at=datetime(2026, 3, 1),
        closed_by_event_id="sell-1",
        long_term_holding_days=365,
    )
    assert remaining == []
    assert len(closed) == 1
    assert closed[0].shares == pytest.approx(2.0)


def test_consume_fifo_computes_realized_gain() -> None:
    lots = [_lot("1", "2026-01-01", shares=2.0, cost_per_share=90.0)]
    _, closed = consume_fifo(
        lots,
        shares_to_consume=2.0,
        exit_price=110.0,
        closed_at=datetime(2026, 3, 1),
        closed_by_event_id="sell-1",
        long_term_holding_days=365,
    )
    assert closed[0].realized_gain == pytest.approx(2.0 * (110.0 - 90.0))


def test_consume_fifo_allocates_fees_pro_rata() -> None:
    lots = [
        _lot("1", "2026-01-01", shares=2.0, cost_per_share=90.0),
        _lot("2", "2026-02-01", shares=2.0, cost_per_share=90.0),
    ]
    _, closed = consume_fifo(
        lots,
        shares_to_consume=4.0,
        exit_price=110.0,
        closed_at=datetime(2026, 3, 1),
        closed_by_event_id="sell-1",
        long_term_holding_days=365,
        total_fees=4.0,
    )
    # $4 fee over 4 shares = $1/share; each closed portion is 2 shares -> $2 allocated.
    assert closed[0].realized_gain == pytest.approx(2.0 * (110.0 - 90.0) - 2.0)
    assert closed[1].realized_gain == pytest.approx(2.0 * (110.0 - 90.0) - 2.0)


@pytest.mark.parametrize(
    ("opened_at", "closed_at", "expected_term"),
    [
        ("2025-01-01", "2026-01-01", "LONG"),  # exactly 365 days
        ("2026-01-01", "2026-06-01", "SHORT"),  # 151 days
    ],
)
def test_consume_fifo_tags_term_by_holding_period(opened_at, closed_at, expected_term) -> None:
    lots = [_lot("1", opened_at, shares=1.0, cost_per_share=90.0)]
    _, closed = consume_fifo(
        lots,
        shares_to_consume=1.0,
        exit_price=110.0,
        closed_at=datetime.fromisoformat(closed_at),
        closed_by_event_id="sell-1",
        long_term_holding_days=365,
    )
    assert closed[0].term == expected_term


def test_consume_fifo_raises_when_insufficient_shares() -> None:
    lots = [_lot("1", "2026-01-01", shares=1.0, cost_per_share=90.0)]
    with pytest.raises(ValueError, match="only"):
        consume_fifo(
            lots,
            shares_to_consume=5.0,
            exit_price=110.0,
            closed_at=datetime(2026, 3, 1),
            closed_by_event_id="sell-1",
            long_term_holding_days=365,
        )


def test_consume_fifo_ignores_other_symbols_lots() -> None:
    lots = [
        _lot("1", "2026-01-01", shares=5.0, cost_per_share=90.0, symbol="VOO"),
    ]
    remaining, closed = consume_fifo(
        lots,
        shares_to_consume=5.0,
        exit_price=110.0,
        closed_at=datetime(2026, 3, 1),
        closed_by_event_id="sell-1",
        long_term_holding_days=365,
    )
    assert closed[0].symbol == "VOO"
    assert remaining == []


def test_apply_split_multiplies_shares_and_divides_cost_for_matching_symbol() -> None:
    lots = [
        _lot("1", "2026-01-01", shares=10.0, cost_per_share=100.0, symbol="VOO"),
        _lot("2", "2026-01-01", shares=5.0, cost_per_share=50.0, symbol="BND"),
    ]
    result = apply_split(lots, symbol="VOO", ratio=2.0)
    voo_lot = next(lot for lot in result if lot.symbol == "VOO")
    bnd_lot = next(lot for lot in result if lot.symbol == "BND")
    assert voo_lot.shares == pytest.approx(20.0)
    assert voo_lot.cost_per_share == pytest.approx(50.0)
    assert bnd_lot.shares == pytest.approx(5.0)
    assert bnd_lot.cost_per_share == pytest.approx(50.0)
