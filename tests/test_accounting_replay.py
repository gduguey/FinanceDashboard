from datetime import date, datetime

import polars as pl
import pytest

from accounting.ledger.replay import (
    account_balances,
    account_balances_over_time,
    unbalanced_transactions,
    validate_balanced,
)

SCHEMA = {
    "posting_id": pl.Utf8,
    "transaction_id": pl.Utf8,
    "account_id": pl.Utf8,
    "posted_at": pl.Datetime("us"),
    "amount": pl.Float64,
    "currency": pl.Utf8,
}


def _posting(
    posting_id: str,
    transaction_id: str,
    account_id: str,
    amount: float,
    posted_at: str = "2026-01-01T00:00:00",
    currency: str = "USD",
) -> dict:
    return {
        "posting_id": posting_id,
        "transaction_id": transaction_id,
        "account_id": account_id,
        "posted_at": datetime.fromisoformat(posted_at),
        "amount": amount,
        "currency": currency,
    }


def _postings(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=SCHEMA) if rows else pl.DataFrame(schema=SCHEMA)


def test_account_balances_sums_per_account() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -33.70),
        _posting("p2", "t1", "uncategorized:expense", 33.70),
        _posting("p3", "t2", "chase:checking:9579", 100.0),
        _posting("p4", "t2", "uncategorized:income", -100.0),
    )
    balances = account_balances(postings)
    checking = balances.filter(pl.col("account_id") == "chase:checking:9579").row(0, named=True)
    assert checking["balance"] == pytest.approx(66.30)


def test_account_balances_truncates_to_as_of() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", 100.0, posted_at="2026-01-01T00:00:00"),
        _posting("p2", "t1", "uncategorized:income", -100.0, posted_at="2026-01-01T00:00:00"),
        _posting("p3", "t2", "chase:checking:9579", 50.0, posted_at="2026-02-01T00:00:00"),
        _posting("p4", "t2", "uncategorized:income", -50.0, posted_at="2026-02-01T00:00:00"),
    )
    balances = account_balances(postings, as_of=date(2026, 1, 15))
    checking = balances.filter(pl.col("account_id") == "chase:checking:9579").row(0, named=True)
    assert checking["balance"] == pytest.approx(100.0)


def test_account_balances_over_time_gives_zero_before_the_first_posting() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", 100.0, posted_at="2026-02-01T00:00:00"),
        _posting("p2", "t1", "uncategorized:income", -100.0, posted_at="2026-02-01T00:00:00"),
    )
    result = account_balances_over_time(postings, [date(2026, 1, 1), date(2026, 2, 15)])
    checking = result.filter(pl.col("account_id") == "chase:checking:9579").sort("date")
    assert checking["balance"].to_list() == pytest.approx([0.0, 100.0])


def test_account_balances_over_time_carries_the_running_balance_forward() -> None:
    postings = _postings(
        _posting("p1", "t1", "a", 100.0, posted_at="2026-01-01T00:00:00"),
        _posting("p2", "t1", "b", -100.0, posted_at="2026-01-01T00:00:00"),
        _posting("p3", "t2", "a", 50.0, posted_at="2026-01-10T00:00:00"),
        _posting("p4", "t2", "b", -50.0, posted_at="2026-01-10T00:00:00"),
    )
    result = account_balances_over_time(postings, [date(2026, 1, 5), date(2026, 1, 20)])
    account_a = result.filter(pl.col("account_id") == "a").sort("date")
    assert account_a["balance"].to_list() == pytest.approx([100.0, 150.0])


def test_account_balances_preserves_lazy_type() -> None:
    postings = _postings(_posting("p1", "t1", "a", 10.0), _posting("p2", "t1", "b", -10.0))
    result = account_balances(postings.lazy())
    assert isinstance(result, pl.LazyFrame)


def test_unbalanced_transactions_is_empty_when_everything_balances() -> None:
    postings = _postings(_posting("p1", "t1", "a", 10.0), _posting("p2", "t1", "b", -10.0))
    assert unbalanced_transactions(postings).is_empty()


def test_unbalanced_transactions_flags_a_missing_leg() -> None:
    postings = _postings(_posting("p1", "t1", "a", 10.0))
    offenders = unbalanced_transactions(postings)
    assert len(offenders) == 1
    assert offenders.row(0, named=True)["total"] == pytest.approx(10.0)


def test_validate_balanced_raises_on_an_offender() -> None:
    postings = _postings(_posting("p1", "t1", "a", 10.0))
    with pytest.raises(ValueError, match="not zero"):
        validate_balanced(postings)


def test_validate_balanced_passes_silently_when_balanced() -> None:
    postings = _postings(_posting("p1", "t1", "a", 10.0), _posting("p2", "t1", "b", -10.0))
    validate_balanced(postings)
