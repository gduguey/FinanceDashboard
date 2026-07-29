import uuid
from datetime import date, datetime

import polars as pl
import pytest

from accounting.dashboard.net_worth import net_worth_summary
from accounting.ledger.currency import DisplayCurrency
from accounting.ledger.frame import LEDGER_FRAME_SCHEMA
from accounting.models import Account, OpeningBalance, OtherAsset

SCHEMA = LEDGER_FRAME_SCHEMA


def _posting(
    posting_id: str, transaction_id: str, account_id: str, amount: float, posted_at: str = "2026-06-01"
) -> dict:
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


def _postings(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=SCHEMA)


CHECKING = Account(
    account_id="chase:checking:9579", name="Chase Checking", kind="checking", institution="Chase", currency="USD"
)
CREDIT_CARD = Account(
    account_id="chase:credit_card:8235",
    name="Chase Credit Card",
    kind="credit_card",
    institution="Chase",
    currency="USD",
)
SAVINGS = Account(
    account_id="sofi:savings:3680", name="SoFi Savings", kind="savings", institution="SoFi", currency="USD"
)
EUR_ACCOUNT = Account(
    account_id="bnp:checking:0001", name="BNP Checking", kind="checking", institution="BNP", currency="EUR"
)
VAULT = Account(
    account_id="sofi:savings:3680:vault:travel",
    name="Travel Vault",
    kind="vault",
    institution="SoFi",
    currency="USD",
    parent_account_id="sofi:savings:3680",
)
UNCATEGORIZED_EXPENSE = Account(
    account_id="uncategorized:expense",
    name="Uncategorized Expense",
    kind="expense_payee",
    institution="internal",
    currency="USD",
)
BROKER_CONNECTION_ID = uuid.UUID("6f1d0d1e-2c8b-4f5a-9d2c-1a3b5c7d9e11")
"""A stand-in `trades.broker_connections.id` — this module never touches a database, so any fixed id will do."""

EXTERNAL_INVESTMENT = Account(
    account_id="external:interactive-brokers",
    name="Interactive Brokers",
    kind="external_investment",
    institution="external",
    currency="USD",
    broker_connection_id=BROKER_CONNECTION_ID,
)
MANUAL_EXTERNAL_INVESTMENT = Account(
    account_id="external:friends-fund",
    name="Friend's Fund",
    kind="external_investment",
    institution="external",
    currency="USD",
)


def test_net_worth_sums_checking_and_savings_as_assets() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", 1000.0),
        _posting("p2", "t1", "uncategorized:expense", -1000.0),
        _posting("p3", "t2", "sofi:savings:3680", 500.0),
        _posting("p4", "t2", "uncategorized:expense", -500.0),
    )
    accounts = {
        CHECKING.account_id: CHECKING,
        SAVINGS.account_id: SAVINGS,
        UNCATEGORIZED_EXPENSE.account_id: UNCATEGORIZED_EXPENSE,
    }
    summary = net_worth_summary(postings, accounts, [], date(2026, 6, 30))
    assert summary.assets == pytest.approx(1500.0)
    assert summary.net_worth == pytest.approx(1500.0)


def test_net_worth_subtracts_a_credit_card_balance_as_a_liability() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", 1000.0),
        _posting("p2", "t1", "uncategorized:expense", -1000.0),
        _posting("p3", "t2", "chase:credit_card:8235", -300.0),
        _posting("p4", "t2", "uncategorized:expense", 300.0),
    )
    accounts = {
        CHECKING.account_id: CHECKING,
        CREDIT_CARD.account_id: CREDIT_CARD,
        UNCATEGORIZED_EXPENSE.account_id: UNCATEGORIZED_EXPENSE,
    }
    summary = net_worth_summary(postings, accounts, [], date(2026, 6, 30))
    assert summary.assets == pytest.approx(1000.0)
    assert summary.liabilities == pytest.approx(300.0)
    assert summary.net_worth == pytest.approx(700.0)


def test_net_worth_excludes_virtual_placeholder_accounts_entirely() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -50.0),
        _posting("p2", "t1", "uncategorized:expense", 50.0),
    )
    accounts = {CHECKING.account_id: CHECKING, UNCATEGORIZED_EXPENSE.account_id: UNCATEGORIZED_EXPENSE}
    summary = net_worth_summary(postings, accounts, [], date(2026, 6, 30))
    assert {row.account_id for row in summary.accounts} == {"chase:checking:9579"}


def test_net_worth_includes_a_vault_as_its_own_asset_row_without_double_counting() -> None:
    postings = _postings(
        _posting("p1", "t1", "sofi:savings:3680", 1000.0),
        _posting("p2", "t1", "uncategorized:expense", -1000.0),
        _posting("p3", "t2", "sofi:savings:3680", -250.0),
        _posting("p4", "t2", "sofi:savings:3680:vault:travel", 250.0),
    )
    accounts = {
        SAVINGS.account_id: SAVINGS,
        VAULT.account_id: VAULT,
        UNCATEGORIZED_EXPENSE.account_id: UNCATEGORIZED_EXPENSE,
    }
    summary = net_worth_summary(postings, accounts, [], date(2026, 6, 30))
    assert summary.assets == pytest.approx(1000.0)
    vault_row = next(row for row in summary.accounts if row.account_id == VAULT.account_id)
    assert vault_row.balance == pytest.approx(250.0)
    assert vault_row.parent_account_id == "sofi:savings:3680"


def test_net_worth_reads_the_external_investment_value_from_the_caller() -> None:
    accounts = {EXTERNAL_INVESTMENT.account_id: EXTERNAL_INVESTMENT}
    summary = net_worth_summary(_postings(), accounts, [], date(2026, 6, 30), external_investment_value=42000.0)
    assert summary.assets == pytest.approx(42000.0)


def test_net_worth_defaults_a_missing_external_investment_value_to_zero() -> None:
    accounts = {EXTERNAL_INVESTMENT.account_id: EXTERNAL_INVESTMENT}
    summary = net_worth_summary(_postings(), accounts, [], date(2026, 6, 30))
    assert summary.assets == pytest.approx(0.0)


def test_net_worth_converts_a_trades_linked_investment_using_the_accounts_own_currency() -> None:
    eur_external_investment = EXTERNAL_INVESTMENT.model_copy(update={"currency": "EUR"})
    accounts = {eur_external_investment.account_id: eur_external_investment}
    display = DisplayCurrency(code="USD", rates_to_base={"USD": 1.0, "EUR": 2.0})
    summary = net_worth_summary(
        _postings(), accounts, [], date(2026, 6, 30), display=display, external_investment_value=42000.0
    )
    assert summary.accounts[0].currency == "EUR"
    assert summary.assets == pytest.approx(84000.0)


def test_net_worth_ignores_the_trades_value_for_a_manual_external_investment_account() -> None:
    postings = _postings(
        _posting("p1", "t1", "external:friends-fund", 5000.0),
        _posting("p2", "t1", "uncategorized:expense", -5000.0),
    )
    accounts = {MANUAL_EXTERNAL_INVESTMENT.account_id: MANUAL_EXTERNAL_INVESTMENT}
    summary = net_worth_summary(postings, accounts, [], date(2026, 6, 30), external_investment_value=42000.0)
    assert summary.assets == pytest.approx(5000.0)


def test_net_worth_adds_an_opening_balance_for_a_manual_external_investment_account() -> None:
    accounts = {MANUAL_EXTERNAL_INVESTMENT.account_id: MANUAL_EXTERNAL_INVESTMENT}
    opening_balances = {
        MANUAL_EXTERNAL_INVESTMENT.account_id: OpeningBalance(
            account_id=MANUAL_EXTERNAL_INVESTMENT.account_id, amount=12000.0, as_of_date=datetime(2026, 1, 1)
        )
    }
    summary = net_worth_summary(
        _postings(),
        accounts,
        [],
        date(2026, 6, 30),
        external_investment_value=42000.0,
        opening_balances=opening_balances,
    )
    assert summary.assets == pytest.approx(12000.0)


def test_net_worth_adds_manually_entered_other_assets() -> None:
    summary = net_worth_summary(
        _postings(), {}, [OtherAsset(asset_id="car", name="Car", value=15000.0)], date(2026, 6, 30)
    )
    assert summary.other_assets_total == pytest.approx(15000.0)
    assert summary.net_worth == pytest.approx(15000.0)


def test_net_worth_converts_a_eur_account_into_usd_display() -> None:
    postings = _postings(
        _posting("p1", "t1", "bnp:checking:0001", 1000.0), _posting("p2", "t1", "uncategorized:expense", -1000.0)
    )
    accounts = {EUR_ACCOUNT.account_id: EUR_ACCOUNT, UNCATEGORIZED_EXPENSE.account_id: UNCATEGORIZED_EXPENSE}
    summary = net_worth_summary(
        postings, accounts, [], date(2026, 6, 30), DisplayCurrency("USD", {"USD": 1.0, "EUR": 1.10})
    )
    assert summary.assets == pytest.approx(1100.0)
    row = summary.accounts[0]
    assert row.balance == pytest.approx(1000.0)  # native currency untouched on the row itself
    assert row.currency == "EUR"


def test_net_worth_converts_a_eur_other_asset_into_eur_display() -> None:
    summary = net_worth_summary(
        _postings(),
        {},
        [OtherAsset(asset_id="flat", name="Paris flat", value=1000.0, currency="EUR")],
        date(2026, 6, 30),
        DisplayCurrency("EUR", {"USD": 1.0, "EUR": 1.10}),
    )
    assert summary.other_assets_total == pytest.approx(1000.0)


def test_net_worth_adds_an_opening_balance_once_as_of_reaches_it() -> None:
    accounts = {SAVINGS.account_id: SAVINGS}
    opening_balances = {
        SAVINGS.account_id: OpeningBalance(account_id=SAVINGS.account_id, amount=500.0, as_of_date=datetime(2026, 6, 1))
    }
    before = net_worth_summary(_postings(), accounts, [], date(2026, 5, 31), opening_balances=opening_balances)
    after = net_worth_summary(_postings(), accounts, [], date(2026, 6, 1), opening_balances=opening_balances)
    assert before.assets == pytest.approx(0.0)
    assert after.assets == pytest.approx(500.0)


def test_net_worth_opening_balance_adds_on_top_of_posting_derived_balance() -> None:
    postings = _postings(
        _posting("p1", "t1", "sofi:savings:3680", 200.0, posted_at="2026-06-15"),
        _posting("p2", "t1", "uncategorized:expense", -200.0, posted_at="2026-06-15"),
    )
    accounts = {SAVINGS.account_id: SAVINGS, UNCATEGORIZED_EXPENSE.account_id: UNCATEGORIZED_EXPENSE}
    opening_balances = {
        SAVINGS.account_id: OpeningBalance(account_id=SAVINGS.account_id, amount=500.0, as_of_date=datetime(2026, 6, 1))
    }
    summary = net_worth_summary(postings, accounts, [], date(2026, 6, 30), opening_balances=opening_balances)
    assert summary.assets == pytest.approx(700.0)


def test_net_worth_converts_a_usd_other_asset_into_eur_display() -> None:
    summary = net_worth_summary(
        _postings(),
        {},
        [OtherAsset(asset_id="car", name="Car", value=1100.0, currency="USD")],
        date(2026, 6, 30),
        DisplayCurrency("EUR", {"USD": 1.0, "EUR": 1.10}),
    )
    assert summary.other_assets_total == pytest.approx(1000.0)
