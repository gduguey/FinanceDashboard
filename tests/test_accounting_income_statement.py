from datetime import date, datetime

import polars as pl
import pytest

from accounting.dashboard.income_statement import (
    UNCATEGORIZED_EXPENSE_ID,
    UNCATEGORIZED_INCOME_ID,
    Scope,
    category_totals,
    monthly_income_expense,
    spend_curve_vs_average,
)
from accounting.ledger.currency import DisplayCurrency
from accounting.models import Account, Category, Posting

SCHEMA = Posting.polars_schema


def _posting(
    posting_id: str,
    transaction_id: str,
    account_id: str,
    amount: float,
    posted_at: str = "2026-06-01",
    category_id: str | None = None,
    subcategory_id: str | None = None,
    tag_ids: list[str] | None = None,
) -> dict:
    return {
        "posting_id": posting_id,
        "transaction_id": transaction_id,
        "account_id": account_id,
        "posted_at": datetime.fromisoformat(posted_at),
        "amount": amount,
        "currency": "USD",
        "category_id": category_id,
        "subcategory_id": subcategory_id,
        "budget_id": None,
        "tag_ids": tag_ids or [],
        "description": "",
        "meta": {},
    }


def _postings(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=SCHEMA)


CHECKING = Account(
    account_id="chase:checking:9579", name="Chase Checking", kind="checking", institution="Chase", currency="USD"
)
SAVINGS = Account(
    account_id="sofi:savings:3680", name="SoFi Savings", kind="savings", institution="SoFi", currency="USD"
)
UNCATEGORIZED_EXPENSE = Account(
    account_id="uncategorized:expense",
    name="Uncategorized Expense",
    kind="expense_payee",
    institution="internal",
    currency="USD",
)
UNCATEGORIZED_INCOME = Account(
    account_id="uncategorized:income",
    name="Uncategorized Income",
    kind="income_source",
    institution="internal",
    currency="USD",
)
FOOD = Category(category_id="expense:food", name="Food & Drink", classification="expense", color="#000")
GROCERIES = Category(
    category_id="expense:food:groceries",
    name="Groceries",
    classification="expense",
    parent_category_id="expense:food",
    color="#000",
)
SALARY = Category(category_id="income:salary", name="Salary", classification="income", color="#111")

EUR_CHECKING = Account(
    account_id="bnp:checking:0001", name="BNP Checking", kind="checking", institution="BNP", currency="EUR"
)

ACCOUNTS = {
    CHECKING.account_id: CHECKING,
    SAVINGS.account_id: SAVINGS,
    UNCATEGORIZED_EXPENSE.account_id: UNCATEGORIZED_EXPENSE,
    UNCATEGORIZED_INCOME.account_id: UNCATEGORIZED_INCOME,
    EUR_CHECKING.account_id: EUR_CHECKING,
}
CATEGORIES = {FOOD.category_id: FOOD, GROCERIES.category_id: GROCERIES, SALARY.category_id: SALARY}


def test_category_totals_sums_a_categorized_expense_leg() -> None:
    postings = _postings(
        _posting(
            "p1",
            "t1",
            "chase:checking:9579",
            -50.0,
            category_id="expense:food",
            subcategory_id="expense:food:groceries",
        ),
        _posting("p2", "t1", "uncategorized:expense", 50.0),
    )
    totals = category_totals(postings, ACCOUNTS, CATEGORIES, date(2026, 6, 1), date(2026, 6, 30))
    row = totals.row(0, named=True)
    assert row["classification"] == "expense"
    assert row["category_name"] == "Food & Drink"
    assert row["subcategory_name"] == "Groceries"
    assert row["amount"] == pytest.approx(50.0)


def test_category_totals_scoped_to_a_tag_excludes_untagged_legs() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -50.0, category_id="expense:food", tag_ids=["tag:japan-trip"]),
        _posting("p2", "t1", "uncategorized:expense", 50.0),
        _posting("p3", "t2", "chase:checking:9579", -30.0, category_id="expense:food"),
        _posting("p4", "t2", "uncategorized:expense", 30.0),
    )
    totals = category_totals(
        postings, ACCOUNTS, CATEGORIES, date(2026, 6, 1), date(2026, 6, 30), scope=Scope(tag_id="tag:japan-trip")
    )
    row = totals.row(0, named=True)
    assert row["amount"] == pytest.approx(50.0)


def test_category_totals_excludes_transfers_between_two_real_accounts() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -500.0),
        _posting("p2", "t1", "sofi:savings:3680", 500.0),
    )
    totals = category_totals(postings, ACCOUNTS, CATEGORIES, date(2026, 6, 1), date(2026, 6, 30))
    assert totals.is_empty()


def test_category_totals_buckets_uncategorized_legs_by_sign() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", 1500.0),
        _posting("p2", "t1", "uncategorized:income", -1500.0),
        _posting("p3", "t2", "chase:checking:9579", -20.0),
        _posting("p4", "t2", "uncategorized:expense", 20.0),
    )
    totals = category_totals(postings, ACCOUNTS, CATEGORIES, date(2026, 6, 1), date(2026, 6, 30))
    income_row = next(row for row in totals.iter_rows(named=True) if row["category_id"] == UNCATEGORIZED_INCOME_ID)
    expense_row = next(row for row in totals.iter_rows(named=True) if row["category_id"] == UNCATEGORIZED_EXPENSE_ID)
    assert income_row["amount"] == pytest.approx(1500.0)
    assert expense_row["amount"] == pytest.approx(20.0)


def test_category_totals_converts_a_eur_expense_into_the_display_currency() -> None:
    postings = _postings(
        _posting("p1", "t1", "bnp:checking:0001", -100.0, category_id="expense:food"),
        _posting("p2", "t1", "uncategorized:expense", 100.0),
    )
    totals = category_totals(
        postings,
        ACCOUNTS,
        CATEGORIES,
        date(2026, 6, 1),
        date(2026, 6, 30),
        display=DisplayCurrency("USD", {"USD": 1.0, "EUR": 1.10}),
    )
    row = totals.row(0, named=True)
    assert row["amount"] == pytest.approx(110.0)


def test_monthly_income_expense_splits_by_sign_and_month() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", 1500.0, posted_at="2026-06-01"),
        _posting("p2", "t1", "uncategorized:income", -1500.0, posted_at="2026-06-01"),
        _posting("p3", "t2", "chase:checking:9579", -70.0, posted_at="2026-06-15"),
        _posting("p4", "t2", "uncategorized:expense", 70.0, posted_at="2026-06-15"),
    )
    monthly = monthly_income_expense(postings, ACCOUNTS, date(2026, 6, 1), date(2026, 6, 30))
    row = monthly.row(0, named=True)
    assert row["month"] == "2026-06"
    assert row["income"] == pytest.approx(1500.0)
    assert row["expense"] == pytest.approx(70.0)


def test_spend_curve_tracks_cumulative_spend_for_the_selected_month() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -10.0, posted_at="2026-06-01"),
        _posting("p2", "t1", "uncategorized:expense", 10.0, posted_at="2026-06-01"),
        _posting("p3", "t2", "chase:checking:9579", -5.0, posted_at="2026-06-03"),
        _posting("p4", "t2", "uncategorized:expense", 5.0, posted_at="2026-06-03"),
    )
    curve = spend_curve_vs_average(postings, ACCOUNTS, date(2026, 6, 15), lookback_months=1)
    by_day = {row["day"]: row["current_month_cumulative"] for row in curve.iter_rows(named=True)}
    assert by_day[1] == pytest.approx(10.0)
    assert by_day[2] == pytest.approx(10.0)
    assert by_day[3] == pytest.approx(15.0)
