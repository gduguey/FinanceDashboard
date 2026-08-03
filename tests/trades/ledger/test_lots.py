from datetime import datetime

import pytest

from trades.ledger.lots import LotBook


def _book(*opens: tuple[str, str, float, float]) -> LotBook:
    """A one-symbol book with a lot opened per `(lot_id, opened_at, shares, cost_per_share)`."""
    book = LotBook("VOO")
    for lot_id, opened_at, shares, cost_per_share in opens:
        book.open(
            lot_id=lot_id,
            opened_at=datetime.fromisoformat(opened_at),
            shares=shares,
            cost_per_share=cost_per_share,
        )
    return book


def _consume(book: LotBook, shares: float, exit_price: float = 110.0, closed_at: str = "2026-03-01"):
    return book.consume_fifo(
        shares_to_consume=shares,
        exit_price=exit_price,
        closed_at=datetime.fromisoformat(closed_at),
        closed_by_event_id="sell-1",
        long_term_holding_days=365,
    )


def _open_by_id(book: LotBook) -> dict:
    return {lot.lot_id: lot for lot in book.open_lots()}


def test_consume_fifo_consumes_oldest_lot_first() -> None:
    # Opened newest-first on purpose: a book keeps itself in oldest-first
    # order, which is what makes never sorting on a SELL correct.
    book = _book(("2", "2026-02-01", 5.0, 100.0), ("1", "2026-01-01", 5.0, 90.0))
    closed = _consume(book, 3.0)
    assert len(closed) == 1
    assert closed[0].lot_id == "1"
    assert closed[0].shares == pytest.approx(3.0)
    remaining = _open_by_id(book)
    assert len(remaining) == 2
    assert remaining["2"].shares == pytest.approx(5.0)
    assert remaining["1"].shares == pytest.approx(2.0)


def test_open_lots_are_returned_oldest_first() -> None:
    book = _book(("2", "2026-02-01", 5.0, 100.0), ("1", "2026-01-01", 5.0, 90.0))
    assert [lot.lot_id for lot in book.open_lots()] == ["1", "2"]


def test_consume_fifo_spans_multiple_lots() -> None:
    book = _book(("1", "2026-01-01", 2.0, 90.0), ("2", "2026-02-01", 5.0, 100.0))
    closed = _consume(book, 4.0)
    assert [c.lot_id for c in closed] == ["1", "2"]
    assert closed[0].shares == pytest.approx(2.0)
    assert closed[1].shares == pytest.approx(2.0)
    assert [lot.shares for lot in book.open_lots()] == [pytest.approx(3.0)]


def test_consume_fifo_fully_closes_a_lot_and_drops_it() -> None:
    book = _book(("1", "2026-01-01", 2.0, 90.0))
    closed = _consume(book, 2.0)
    assert book.open_lots() == []
    assert book.total_shares == pytest.approx(0.0)
    assert len(closed) == 1
    assert closed[0].shares == pytest.approx(2.0)


def test_consume_fifo_computes_realized_gain() -> None:
    book = _book(("1", "2026-01-01", 2.0, 90.0))
    closed = _consume(book, 2.0)
    assert closed[0].realized_gain == pytest.approx(2.0 * (110.0 - 90.0))


def test_consume_fifo_carries_the_books_symbol_onto_every_closed_lot() -> None:
    book = _book(("1", "2026-01-01", 5.0, 90.0))
    closed = _consume(book, 5.0)
    assert closed[0].symbol == "VOO"


@pytest.mark.parametrize(
    ("opened_at", "closed_at", "expected_term"),
    [
        ("2025-01-01", "2026-01-01", "LONG"),  # exactly 365 days
        ("2026-01-01", "2026-06-01", "SHORT"),  # 151 days
    ],
)
def test_consume_fifo_tags_term_by_holding_period(opened_at, closed_at, expected_term) -> None:
    book = _book(("1", opened_at, 1.0, 90.0))
    closed = _consume(book, 1.0, closed_at=closed_at)
    assert closed[0].term == expected_term


def test_consume_fifo_raises_when_insufficient_shares() -> None:
    book = _book(("1", "2026-01-01", 1.0, 90.0))
    with pytest.raises(ValueError, match="only"):
        _consume(book, 5.0)


def test_consume_fifo_raises_on_an_empty_book_rather_than_closing_nothing() -> None:
    with pytest.raises(ValueError, match="only"):
        _consume(LotBook("VOO"), 1.0)


def test_consume_fifo_leaves_the_book_untouched_when_it_raises() -> None:
    book = _book(("1", "2026-01-01", 1.0, 90.0))
    with pytest.raises(ValueError, match="only"):
        _consume(book, 5.0)
    assert [lot.lot_id for lot in book.open_lots()] == ["1"]
    assert book.total_shares == pytest.approx(1.0)


def test_consume_fifo_splits_accrued_dividends_proportionally_on_partial_close() -> None:
    book = _book(("1", "2026-01-01", 10.0, 90.0))
    book.accrue_dividend(40.0)
    closed = _consume(book, 4.0)
    # 4 of 10 shares closed -> 40% of the $40 accrued dividend follows them.
    assert closed[0].dividends_received == pytest.approx(16.0)
    assert book.open_lots()[0].dividends_received == pytest.approx(24.0)


def test_consume_fifo_fully_closing_a_lot_keeps_its_full_dividend_total() -> None:
    book = _book(("1", "2026-01-01", 2.0, 90.0))
    book.accrue_dividend(15.0)
    closed = _consume(book, 2.0)
    assert closed[0].dividends_received == pytest.approx(15.0)


def test_a_dividend_after_a_partial_close_splits_over_the_shares_that_remain() -> None:
    book = _book(("1", "2026-01-01", 10.0, 90.0), ("2", "2026-02-01", 10.0, 90.0))
    _consume(book, 5.0)
    book.accrue_dividend(150.0)
    # 15 shares remain, 5 on lot 1 and 10 on lot 2.
    lots = _open_by_id(book)
    assert lots["1"].dividends_received == pytest.approx(50.0)
    assert lots["2"].dividends_received == pytest.approx(100.0)


def test_accrue_dividend_splits_pro_rata_by_shares() -> None:
    book = _book(("1", "2026-01-01", 10.0, 100.0), ("2", "2026-01-01", 30.0, 100.0))
    book.accrue_dividend(100.0)
    lots = _open_by_id(book)
    assert lots["1"].dividends_received == pytest.approx(25.0)
    assert lots["2"].dividends_received == pytest.approx(75.0)


def test_accrue_dividend_adds_to_any_existing_total() -> None:
    book = _book(("1", "2026-01-01", 10.0, 100.0))
    book.accrue_dividend(5.0)
    book.accrue_dividend(20.0)
    assert book.open_lots()[0].dividends_received == pytest.approx(25.0)


def test_accrue_dividend_on_a_book_holding_nothing_is_dropped_rather_than_divided_by_zero() -> None:
    book = _book(("1", "2026-01-01", 1.0, 100.0))
    _consume(book, 1.0)
    book.accrue_dividend(50.0)
    book.open(lot_id="2", opened_at=datetime(2026, 4, 1), shares=1.0, cost_per_share=100.0)
    assert book.open_lots()[0].dividends_received == pytest.approx(0.0)


def test_a_lot_opened_after_a_dividend_receives_none_of_it() -> None:
    book = _book(("1", "2026-01-01", 10.0, 100.0))
    book.accrue_dividend(100.0)
    book.open(lot_id="2", opened_at=datetime(2026, 4, 1), shares=10.0, cost_per_share=100.0)
    lots = _open_by_id(book)
    assert lots["1"].dividends_received == pytest.approx(100.0)
    assert lots["2"].dividends_received == pytest.approx(0.0)


def test_apply_split_multiplies_shares_and_divides_cost() -> None:
    book = _book(("1", "2026-01-01", 10.0, 100.0))
    book.apply_split(2.0)
    lot = book.open_lots()[0]
    assert lot.shares == pytest.approx(20.0)
    assert lot.cost_per_share == pytest.approx(50.0)
    assert book.total_shares == pytest.approx(20.0)


def test_apply_split_keeps_dividends_already_accrued_and_rebases_the_ones_after_it() -> None:
    book = _book(("1", "2026-01-01", 10.0, 100.0))
    book.accrue_dividend(100.0)
    book.apply_split(2.0)
    assert book.open_lots()[0].dividends_received == pytest.approx(100.0)
    book.accrue_dividend(40.0)
    assert book.open_lots()[0].dividends_received == pytest.approx(140.0)


def test_a_sale_within_tolerance_of_the_whole_position_empties_the_book() -> None:
    # `_SHORTFALL_TOLERANCE` admits it, and `take` is clamped to the lot, so the
    # overshoot is dropped rather than subtracted from anything.
    book = _book(("1", "2026-01-01", 10.0, 90.0))
    closed = _consume(book, 10.0 + 5e-10)
    assert book.open_lots() == []
    assert closed[0].shares == pytest.approx(10.0)
    assert book.total_shares == pytest.approx(0.0)


def test_an_emptied_book_forgets_its_dividend_baseline() -> None:
    # `divps` only ever grows, so a book that fills again would otherwise hand
    # every new lot a baseline inherited from a position nobody holds any more.
    book = _book(("1", "2026-01-01", 10.0, 90.0))
    book.accrue_dividend(100.0)
    _consume(book, 10.0)
    assert book.divps == pytest.approx(0.0)

    book.open(lot_id="2", opened_at=datetime(2026, 4, 1), shares=10.0, cost_per_share=90.0)
    book.accrue_dividend(20.0)
    assert book.open_lots()[0].dividends_received == pytest.approx(20.0)


def test_a_dividend_on_a_dust_position_does_not_poison_the_accumulator() -> None:
    """A partial close that misses a lot's shares by float noise leaves dust behind.

    Dividing a dividend by that dust would put a number 13 orders of magnitude
    too large into `divps`, permanently, and every lot opened afterwards would
    then derive its own total from the difference of two enormous floats.
    """
    book = _book(("1", "2026-01-01", 10.0, 90.0))
    book.accrue_dividend(100.0)
    _consume(book, 10.0 - 1e-13)
    assert 0.0 < book.total_shares < 1e-9, "the sale was meant to leave a dust remainder open"

    book.accrue_dividend(50.0)
    assert book.divps < 1e6, f"a dust position inflated the per-share accumulator to {book.divps:.3e}"
    # The dust lot is still the only holder, so it takes the whole dividend —
    # exactly what the per-lot implementation this replaced would have given it.
    assert book.open_lots()[0].dividends_received == pytest.approx(50.0, abs=1e-6)

    book.open(lot_id="2", opened_at=datetime(2026, 4, 1), shares=10.0, cost_per_share=90.0)
    book.accrue_dividend(20.0)
    assert _open_by_id(book)["2"].dividends_received == pytest.approx(20.0)
