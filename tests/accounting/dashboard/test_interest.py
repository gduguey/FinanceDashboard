from datetime import date, datetime

import polars as pl
import pytest

from accounting.dashboard.interest import interest_summary
from accounting.models import Account, Posting

SCHEMA = Posting.polars_schema


def _posting(
    posting_id: str,
    transaction_id: str,
    account_id: str,
    amount: float,
    posted_at: str,
    category_id: str | None = None,
) -> dict:
    return {
        "posting_id": posting_id,
        "transaction_id": transaction_id,
        "account_id": account_id,
        "posted_at": datetime.fromisoformat(posted_at),
        "amount": amount,
        "currency": "USD",
        "category_id": category_id,
        "subcategory_id": None,
        "budget_id": None,
        "tag_ids": [],
        "description": "",
        "meta": {},
    }


def _postings(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=SCHEMA)


SAVINGS = Account(
    account_id="sofi:savings:3680",
    name="SoFi Savings",
    kind="savings",
    institution="SoFi",
    currency="USD",
    meta={"apy_pct": "4.5"},
)
CHECKING = Account(
    account_id="chase:checking:9579", name="Checking", kind="checking", institution="Chase", currency="USD"
)


def test_interest_summary_only_includes_savings_and_vault_accounts() -> None:
    rows = interest_summary(
        _postings(), {SAVINGS.account_id: SAVINGS, CHECKING.account_id: CHECKING}, date(2026, 6, 30)
    )
    assert {row.account_id for row in rows} == {"sofi:savings:3680"}


def test_interest_summary_sums_interest_earned_this_calendar_year() -> None:
    postings = _postings(
        _posting("p1", "t1", "sofi:savings:3680", 10.0, "2026-01-15", "income:interest-earned"),
        _posting("p2", "t1", "uncategorized:income", -10.0, "2026-01-15"),
        _posting("p3", "t2", "sofi:savings:3680", 12.0, "2026-02-15", "income:interest-earned"),
        _posting("p4", "t2", "uncategorized:income", -12.0, "2026-02-15"),
        _posting("p5", "t3", "sofi:savings:3680", 8.0, "2025-12-15", "income:interest-earned"),
        _posting("p6", "t3", "uncategorized:income", -8.0, "2025-12-15"),
    )
    rows = interest_summary(postings, {SAVINGS.account_id: SAVINGS}, date(2026, 6, 30))
    assert rows[0].interest_earned_this_year == pytest.approx(22.0)


def test_interest_summary_projects_a_year_forward_at_the_current_apy() -> None:
    postings = _postings(
        _posting("p1", "t1", "sofi:savings:3680", 1000.0, "2026-01-01"),
        _posting("p2", "t1", "uncategorized:income", -1000.0, "2026-01-01"),
    )
    rows = interest_summary(postings, {SAVINGS.account_id: SAVINGS}, date(2026, 6, 30))
    assert rows[0].current_balance == pytest.approx(1000.0)
    assert rows[0].projected_next_12_months == pytest.approx(45.0)


def test_interest_summary_carries_the_benchmark_rate_through() -> None:
    rows = interest_summary(_postings(), {SAVINGS.account_id: SAVINGS}, date(2026, 6, 30), benchmark_apy_pct=4.0)
    assert rows[0].benchmark_apy_pct == pytest.approx(4.0)
