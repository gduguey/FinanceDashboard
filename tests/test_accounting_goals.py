from datetime import date, datetime

import polars as pl
import pytest

from accounting.dashboard.goals import all_goal_balances, contributions_to_frame, goal_balance, unallocated_balance
from accounting.models import Account, GoalContribution, Posting

SCHEMA = Posting.polars_schema

CHECKING = Account(
    account_id="chase:checking:9579", name="Chase Checking", kind="checking", institution="Chase", currency="USD"
)
UNCATEGORIZED_INCOME = Account(
    account_id="uncategorized:income",
    name="Uncategorized Income",
    kind="income_source",
    institution="internal",
    currency="USD",
)
UNCATEGORIZED_EXPENSE = Account(
    account_id="uncategorized:expense",
    name="Uncategorized Expense",
    kind="expense_payee",
    institution="internal",
    currency="USD",
)
ACCOUNTS = {
    CHECKING.account_id: CHECKING,
    UNCATEGORIZED_INCOME.account_id: UNCATEGORIZED_INCOME,
    UNCATEGORIZED_EXPENSE.account_id: UNCATEGORIZED_EXPENSE,
}


def _posting(posting_id: str, transaction_id: str, account_id: str, amount: float, posted_at: str) -> dict:
    return {
        "posting_id": posting_id,
        "transaction_id": transaction_id,
        "account_id": account_id,
        "posted_at": datetime.fromisoformat(posted_at),
        "amount": amount,
        "currency": "USD",
        "category_id": None,
        "subcategory_id": None,
        "budget_id": None,
        "tag_ids": [],
        "description": "",
        "meta": {},
    }


def _contribution(contribution_id: str, goal_id: str, amount: float, date_str: str) -> GoalContribution:
    return GoalContribution(
        contribution_id=contribution_id, goal_id=goal_id, date=datetime.fromisoformat(date_str), amount=amount
    )


def test_goal_balance_sums_contributions_up_to_and_including_the_date() -> None:
    contributions = contributions_to_frame({
        "c1": _contribution("c1", "emergency-fund", 500.0, "2026-06-01"),
        "c2": _contribution("c2", "emergency-fund", 300.0, "2026-06-15"),
        "c3": _contribution("c3", "emergency-fund", 200.0, "2026-06-25"),
    })
    assert goal_balance(contributions, "emergency-fund", date(2026, 6, 15)) == pytest.approx(800.0)
    assert goal_balance(contributions, "emergency-fund", date(2026, 6, 30)) == pytest.approx(1000.0)
    assert goal_balance(contributions, "emergency-fund", date(2026, 5, 31)) == pytest.approx(0.0)


def test_goal_balance_reflects_a_withdrawal_as_a_negative_contribution() -> None:
    contributions = contributions_to_frame({
        "c1": _contribution("c1", "emergency-fund", 500.0, "2026-06-01"),
        "c2": _contribution("c2", "emergency-fund", -100.0, "2026-06-10"),
    })
    assert goal_balance(contributions, "emergency-fund", date(2026, 6, 30)) == pytest.approx(400.0)


def test_goal_balance_ignores_other_goals_contributions() -> None:
    contributions = contributions_to_frame({
        "c1": _contribution("c1", "emergency-fund", 500.0, "2026-06-01"),
        "c2": _contribution("c2", "vacation", 1000.0, "2026-06-01"),
    })
    assert goal_balance(contributions, "emergency-fund", date(2026, 6, 30)) == pytest.approx(500.0)


def test_all_goal_balances_defaults_a_goal_with_no_contributions_to_zero() -> None:
    contributions = contributions_to_frame({"c1": _contribution("c1", "emergency-fund", 500.0, "2026-06-01")})
    balances = all_goal_balances(contributions, ["emergency-fund", "vacation"], date(2026, 6, 30))
    assert balances == {"emergency-fund": 500.0, "vacation": 0.0}


def test_unallocated_balance_nets_income_minus_expense_minus_goal_contributions() -> None:
    postings = pl.DataFrame(
        [
            _posting("p1", "t1", "chase:checking:9579", 3000.0, "2026-06-01"),
            _posting("p2", "t1", "uncategorized:income", -3000.0, "2026-06-01"),
            _posting("p3", "t2", "chase:checking:9579", -500.0, "2026-06-05"),
            _posting("p4", "t2", "uncategorized:expense", 500.0, "2026-06-05"),
        ],
        schema=SCHEMA,
    )
    contributions = contributions_to_frame({"c1": _contribution("c1", "emergency-fund", 1000.0, "2026-06-10")})
    # 3000 income - 500 expense - 1000 allocated to the goal = 1500 left unallocated
    assert unallocated_balance(postings, ACCOUNTS, contributions, date(2026, 6, 30)) == pytest.approx(1500.0)


def test_unallocated_balance_with_no_contributions_yet_equals_net_income() -> None:
    postings = pl.DataFrame(
        [
            _posting("p1", "t1", "chase:checking:9579", 3000.0, "2026-06-01"),
            _posting("p2", "t1", "uncategorized:income", -3000.0, "2026-06-01"),
        ],
        schema=SCHEMA,
    )
    contributions = contributions_to_frame({})
    assert unallocated_balance(postings, ACCOUNTS, contributions, date(2026, 6, 30)) == pytest.approx(3000.0)
