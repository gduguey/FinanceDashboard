from datetime import datetime

import polars as pl
import pytest

from accounting.dashboard.budgets import budget_comparison, month_bounds, suggested_budget_amount
from accounting.models import Account, Budget, Category, Posting

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


CHECKING = Account(
    account_id="chase:checking:9579", name="Checking", kind="checking", institution="Chase", currency="USD"
)
UNCATEGORIZED_EXPENSE = Account(
    account_id="uncategorized:expense",
    name="Uncategorized Expense",
    kind="expense_payee",
    institution="internal",
    currency="USD",
)
FOOD = Category(category_id="expense:food", name="Food & Drink", classification="expense", color="#abc")
ACCOUNTS = {CHECKING.account_id: CHECKING, UNCATEGORIZED_EXPENSE.account_id: UNCATEGORIZED_EXPENSE}
CATEGORIES = {FOOD.category_id: FOOD}


def test_month_bounds_spans_the_whole_calendar_month() -> None:
    start, end = month_bounds("2026-02")
    assert start.isoformat() == "2026-02-01"
    assert end.isoformat() == "2026-02-28"


def test_suggested_budget_amount_is_the_median_of_trailing_months() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -100.0, "2026-03-15", "expense:food"),
        _posting("p2", "t1", "uncategorized:expense", 100.0, "2026-03-15"),
        _posting("p3", "t2", "chase:checking:9579", -200.0, "2026-04-15", "expense:food"),
        _posting("p4", "t2", "uncategorized:expense", 200.0, "2026-04-15"),
        _posting("p5", "t3", "chase:checking:9579", -900.0, "2026-05-15", "expense:food"),
        _posting("p6", "t3", "uncategorized:expense", 900.0, "2026-05-15"),
    )
    suggestion = suggested_budget_amount(postings, ACCOUNTS, "expense:food", "2026-06", lookback_months=3)
    assert suggestion == pytest.approx(200.0)


def test_suggested_budget_amount_is_zero_with_no_history() -> None:
    assert suggested_budget_amount(_postings(), ACCOUNTS, "expense:food", "2026-06") == pytest.approx(0.0)


def test_budget_comparison_only_includes_categories_budgeted_for_that_month() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -150.0, "2026-06-10", "expense:food"),
        _posting("p2", "t1", "uncategorized:expense", 150.0, "2026-06-10"),
    )
    budgets = [
        Budget(budget_id="b1", month="2026-06", category_id="expense:food", amount=200.0),
        Budget(budget_id="b2", month="2026-05", category_id="expense:food", amount=999.0),
    ]
    rows = budget_comparison(postings, ACCOUNTS, CATEGORIES, budgets, "2026-06")
    assert len(rows) == 1
    assert rows[0].category_id == "expense:food"
    assert rows[0].budgeted == pytest.approx(200.0)
    assert rows[0].actual == pytest.approx(150.0)


def test_budget_comparison_defaults_actual_to_zero_when_nothing_spent() -> None:
    budgets = [Budget(budget_id="b1", month="2026-06", category_id="expense:food", amount=200.0)]
    rows = budget_comparison(_postings(), ACCOUNTS, CATEGORIES, budgets, "2026-06")
    assert rows[0].actual == pytest.approx(0.0)
