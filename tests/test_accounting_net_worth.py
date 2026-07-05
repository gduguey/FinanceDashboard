from datetime import date, datetime

import polars as pl
import pytest

from accounting.dashboard.net_worth import net_worth_summary
from accounting.models import Account, OtherAsset, Posting

SCHEMA = Posting.polars_schema


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
EXTERNAL_INVESTMENT = Account(
    account_id="external:interactive-brokers",
    name="Interactive Brokers",
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
    assert summary.assets_usd == pytest.approx(1500.0)
    assert summary.net_worth_usd == pytest.approx(1500.0)


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
    assert summary.assets_usd == pytest.approx(1000.0)
    assert summary.liabilities_usd == pytest.approx(300.0)
    assert summary.net_worth_usd == pytest.approx(700.0)


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
    assert summary.assets_usd == pytest.approx(1000.0)
    vault_row = next(row for row in summary.accounts if row.account_id == VAULT.account_id)
    assert vault_row.balance_usd == pytest.approx(250.0)
    assert vault_row.parent_account_id == "sofi:savings:3680"


def test_net_worth_reads_the_external_investment_value_from_the_caller() -> None:
    accounts = {EXTERNAL_INVESTMENT.account_id: EXTERNAL_INVESTMENT}
    summary = net_worth_summary(_postings(), accounts, [], date(2026, 6, 30), external_investment_value_usd=42000.0)
    assert summary.assets_usd == pytest.approx(42000.0)


def test_net_worth_defaults_a_missing_external_investment_value_to_zero() -> None:
    accounts = {EXTERNAL_INVESTMENT.account_id: EXTERNAL_INVESTMENT}
    summary = net_worth_summary(_postings(), accounts, [], date(2026, 6, 30))
    assert summary.assets_usd == pytest.approx(0.0)


def test_net_worth_adds_manually_entered_other_assets() -> None:
    summary = net_worth_summary(
        _postings(), {}, [OtherAsset(asset_id="car", name="Car", value_usd=15000.0)], date(2026, 6, 30)
    )
    assert summary.other_assets_usd == pytest.approx(15000.0)
    assert summary.net_worth_usd == pytest.approx(15000.0)
