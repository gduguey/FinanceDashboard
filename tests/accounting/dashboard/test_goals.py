import uuid
from datetime import date, datetime
from decimal import Decimal

import polars as pl
import pytest

from accounting.dashboard.goals import all_goal_balances, contributions_to_frame, unallocated_balance
from accounting.ledger.currency import DisplayCurrency
from accounting.ledger.frame import LEDGER_FRAME_SCHEMA
from accounting.models import Account, GoalContribution, OpeningBalance

SCHEMA = LEDGER_FRAME_SCHEMA

CHECKING = Account(
    account_id="chase:checking:9579", name="Chase Checking", kind="checking", institution="Chase", currency="USD"
)
SAVINGS_EUR = Account(
    account_id="n26:savings:0001", name="N26 Savings", kind="savings", institution="N26", currency="EUR"
)
CREDIT_CARD = Account(
    account_id="amex:credit_card:1001", name="Amex", kind="credit_card", institution="Amex", currency="USD"
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


def _opening(account_id: str, amount: str, as_of: str) -> OpeningBalance:
    return OpeningBalance(account_id=account_id, amount=Decimal(amount), as_of_date=datetime.fromisoformat(as_of))


def _contribution(
    contribution_id: str, goal_id: str, amount: float, date_str: str, currency: str = "USD"
) -> GoalContribution:
    return GoalContribution(
        contribution_id=contribution_id,
        goal_id=goal_id,
        date=datetime.fromisoformat(date_str),
        amount=amount,
        currency=currency,
    )


def test_all_goal_balances_sums_contributions_up_to_and_including_the_date() -> None:
    contributions = contributions_to_frame({
        "c1": _contribution("c1", "emergency-fund", 500.0, "2026-06-01"),
        "c2": _contribution("c2", "emergency-fund", 300.0, "2026-06-15"),
        "c3": _contribution("c3", "emergency-fund", 200.0, "2026-06-25"),
    })
    assert all_goal_balances(contributions, ["emergency-fund"], date(2026, 6, 15)) == {
        "emergency-fund": pytest.approx(800.0)
    }
    assert all_goal_balances(contributions, ["emergency-fund"], date(2026, 6, 30)) == {
        "emergency-fund": pytest.approx(1000.0)
    }
    assert all_goal_balances(contributions, ["emergency-fund"], date(2026, 5, 31)) == {
        "emergency-fund": pytest.approx(0.0)
    }


def test_all_goal_balances_reflects_a_withdrawal_as_a_negative_contribution() -> None:
    contributions = contributions_to_frame({
        "c1": _contribution("c1", "emergency-fund", 500.0, "2026-06-01"),
        "c2": _contribution("c2", "emergency-fund", -100.0, "2026-06-10"),
    })
    assert all_goal_balances(contributions, ["emergency-fund"], date(2026, 6, 30)) == {
        "emergency-fund": pytest.approx(400.0)
    }


def test_all_goal_balances_ignores_other_goals_contributions() -> None:
    contributions = contributions_to_frame({
        "c1": _contribution("c1", "emergency-fund", 500.0, "2026-06-01"),
        "c2": _contribution("c2", "vacation", 1000.0, "2026-06-01"),
    })
    assert all_goal_balances(contributions, ["emergency-fund"], date(2026, 6, 30)) == {
        "emergency-fund": pytest.approx(500.0)
    }


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


_EUR_DISPLAY = DisplayCurrency(code="EUR", rates_to_base={"USD": 1.0, "EUR": 2.0})


def test_all_goal_balances_converts_contributions_into_the_display_currency() -> None:
    contributions = contributions_to_frame({
        "c1": _contribution("c1", "emergency-fund", 500.0, "2026-06-01", currency="USD"),
    })
    # 1 EUR = 2 USD, so 500 USD converts to 250 EUR.
    assert all_goal_balances(contributions, ["emergency-fund"], date(2026, 6, 30), display=_EUR_DISPLAY) == {
        "emergency-fund": pytest.approx(250.0)
    }


def test_all_goal_balances_defaults_to_usd_when_no_display_currency_given() -> None:
    contributions = contributions_to_frame({"c1": _contribution("c1", "emergency-fund", 500.0, "2026-06-01")})
    assert all_goal_balances(contributions, ["emergency-fund"], date(2026, 6, 30)) == {
        "emergency-fund": pytest.approx(500.0)
    }


def test_all_goal_balances_converts_every_goal_into_the_display_currency() -> None:
    contributions = contributions_to_frame({
        "c1": _contribution("c1", "emergency-fund", 500.0, "2026-06-01", currency="USD"),
        "c2": _contribution("c2", "vacation", 100.0, "2026-06-01", currency="EUR"),
    })
    balances = all_goal_balances(contributions, ["emergency-fund", "vacation"], date(2026, 6, 30), display=_EUR_DISPLAY)
    assert balances == {"emergency-fund": pytest.approx(250.0), "vacation": pytest.approx(100.0)}


def test_all_goal_balances_accepts_a_lazyframe() -> None:
    contributions = contributions_to_frame({"c1": _contribution("c1", "emergency-fund", 500.0, "2026-06-01")})
    balances = all_goal_balances(contributions.lazy(), ["emergency-fund", "vacation"], date(2026, 6, 30))
    assert balances == {"emergency-fund": 500.0, "vacation": 0.0}


def test_unallocated_balance_accepts_a_lazyframe_for_both_postings_and_contributions() -> None:
    postings = pl.DataFrame(
        [
            _posting("p1", "t1", "chase:checking:9579", 3000.0, "2026-06-01"),
            _posting("p2", "t1", "uncategorized:income", -3000.0, "2026-06-01"),
        ],
        schema=SCHEMA,
    )
    contributions = contributions_to_frame({"c1": _contribution("c1", "emergency-fund", 1000.0, "2026-06-10")})
    result = unallocated_balance(postings.lazy(), ACCOUNTS, contributions.lazy(), date(2026, 6, 30))
    assert result == pytest.approx(2000.0)


def test_unallocated_balance_converts_contributions_into_the_display_currency() -> None:
    postings = pl.DataFrame(
        [
            _posting("p1", "t1", "chase:checking:9579", 3000.0, "2026-06-01"),
            _posting("p2", "t1", "uncategorized:income", -3000.0, "2026-06-01"),
        ],
        schema=SCHEMA,
    )
    contributions = contributions_to_frame({
        "c1": _contribution("c1", "emergency-fund", 500.0, "2026-06-10", currency="USD")
    })
    # 3000 USD income -> 1500 EUR, minus 500 USD (-> 250 EUR) contributed = 1250 EUR left unallocated.
    result = unallocated_balance(postings, ACCOUNTS, contributions, date(2026, 6, 30), display=_EUR_DISPLAY)
    assert result == pytest.approx(1250.0)


_OPENING_ACCOUNTS = {
    **ACCOUNTS,
    SAVINGS_EUR.account_id: SAVINGS_EUR,
    CREDIT_CARD.account_id: CREDIT_CARD,
}


def _income_only_postings() -> pl.DataFrame:
    return pl.DataFrame(
        [
            _posting("p1", "t1", "chase:checking:9579", 3000.0, "2026-06-01"),
            _posting("p2", "t1", "uncategorized:income", -3000.0, "2026-06-01"),
        ],
        schema=SCHEMA,
    )


def test_unallocated_balance_counts_an_opening_balance_the_ledger_never_saw() -> None:
    # The whole point of A3a: someone who already had 10,000 in an account
    # when they started tracking has none of it in any posting.
    openings = {CHECKING.account_id: _opening(CHECKING.account_id, "10000.00", "2026-01-01")}
    result = unallocated_balance(
        _income_only_postings(), ACCOUNTS, contributions_to_frame({}), date(2026, 6, 30), opening_balances=openings
    )
    assert result == pytest.approx(13000.0)


def test_unallocated_balance_ignores_an_opening_balance_dated_after_the_as_of_date() -> None:
    openings = {CHECKING.account_id: _opening(CHECKING.account_id, "10000.00", "2026-07-01")}
    result = unallocated_balance(
        _income_only_postings(), ACCOUNTS, contributions_to_frame({}), date(2026, 6, 30), opening_balances=openings
    )
    assert result == pytest.approx(3000.0)


def test_unallocated_balance_subtracts_a_liability_opening_balance() -> None:
    openings = {
        CHECKING.account_id: _opening(CHECKING.account_id, "10000.00", "2026-01-01"),
        CREDIT_CARD.account_id: _opening(CREDIT_CARD.account_id, "-2000.00", "2026-01-01"),
    }
    result = unallocated_balance(
        _income_only_postings(),
        _OPENING_ACCOUNTS,
        contributions_to_frame({}),
        date(2026, 6, 30),
        opening_balances=openings,
    )
    assert result == pytest.approx(11000.0)


def test_unallocated_balance_converts_an_opening_balance_from_its_accounts_currency() -> None:
    openings = {SAVINGS_EUR.account_id: _opening(SAVINGS_EUR.account_id, "100.00", "2026-01-01")}
    # 1 EUR = 2 USD, so a 100 EUR opening balance is 200 USD on top of 3000 USD of income.
    result = unallocated_balance(
        _income_only_postings(),
        _OPENING_ACCOUNTS,
        contributions_to_frame({}),
        date(2026, 6, 30),
        display=DisplayCurrency(code="USD", rates_to_base={"USD": 1.0, "EUR": 2.0}),
        opening_balances=openings,
    )
    assert result == pytest.approx(3200.0)


def test_unallocated_balance_skips_an_opening_balance_on_a_virtual_account() -> None:
    # A counterparty placeholder's "balance" is never money that is anywhere,
    # so it is excluded here exactly as `net_worth_summary` excludes it.
    openings = {UNCATEGORIZED_INCOME.account_id: _opening(UNCATEGORIZED_INCOME.account_id, "500.00", "2026-01-01")}
    result = unallocated_balance(
        _income_only_postings(), ACCOUNTS, contributions_to_frame({}), date(2026, 6, 30), opening_balances=openings
    )
    assert result == pytest.approx(3000.0)


def test_unallocated_balance_skips_an_opening_balance_for_an_unknown_account() -> None:
    openings = {"gone:checking:0000": _opening("gone:checking:0000", "500.00", "2026-01-01")}
    result = unallocated_balance(
        _income_only_postings(), ACCOUNTS, contributions_to_frame({}), date(2026, 6, 30), opening_balances=openings
    )
    assert result == pytest.approx(3000.0)


def test_unallocated_balance_without_opening_balances_is_unchanged() -> None:
    assert unallocated_balance(
        _income_only_postings(), ACCOUNTS, contributions_to_frame({}), date(2026, 6, 30)
    ) == pytest.approx(3000.0)


BROKER_LINKED = Account(
    account_id="ibkr:external_investment:0001",
    name="IBKR",
    kind="external_investment",
    institution="IBKR",
    currency="USD",
    broker_connection_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
)


def test_unallocated_balance_skips_a_broker_linked_accounts_opening_balance() -> None:
    # `net_worth_summary.base_balance` takes a broker-linked account's whole
    # value from the live portfolio and never adds its opening balance, so
    # counting one here would put money into unallocated that appears in no
    # other total.
    accounts = {**ACCOUNTS, BROKER_LINKED.account_id: BROKER_LINKED}
    openings = {BROKER_LINKED.account_id: _opening(BROKER_LINKED.account_id, "5000.00", "2026-01-01")}
    result = unallocated_balance(
        _income_only_postings(), accounts, contributions_to_frame({}), date(2026, 6, 30), opening_balances=openings
    )
    assert result == pytest.approx(3000.0)
