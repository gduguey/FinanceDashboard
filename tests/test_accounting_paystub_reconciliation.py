from datetime import datetime

import polars as pl

from accounting.dashboard.paystub import reconcile_earnings_statement
from accounting.models import Account, EarningsDeposit, EarningsStatement, Posting

SCHEMA = Posting.polars_schema

CHECKING = Account(
    account_id="chase:checking:9579", name="Checking", kind="checking", institution="Chase", currency="USD"
)
UNCATEGORIZED_INCOME = Account(
    account_id="uncategorized:income",
    name="Uncategorized Income",
    kind="income_source",
    institution="internal",
    currency="USD",
)
ACCOUNTS = {CHECKING.account_id: CHECKING, UNCATEGORIZED_INCOME.account_id: UNCATEGORIZED_INCOME}


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


def _postings(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=SCHEMA)


def _statement(*deposits: EarningsDeposit) -> EarningsStatement:
    return EarningsStatement(
        pay_date=datetime(2026, 6, 30), gross_pay=4000.0, taxes_withheld=800.0, net_pay=3200.0, deposits=list(deposits)
    )


def test_reconcile_matches_a_deposit_by_amount_and_account_last4() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", 3200.0, "2026-06-30"),
        _posting("p2", "t1", "uncategorized:income", -3200.0, "2026-06-30"),
    )
    statement = _statement(EarningsDeposit(label="Direct Deposit", account_last4="9579", amount=3200.0))
    result = reconcile_earnings_statement(statement, postings, ACCOUNTS)
    assert result.is_fully_matched
    assert result.matches[0].posting_id == "p1"


def test_reconcile_matches_within_the_tolerance_window() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", 3200.0, "2026-07-02"),
        _posting("p2", "t1", "uncategorized:income", -3200.0, "2026-07-02"),
    )
    statement = _statement(EarningsDeposit(label="Direct Deposit", account_last4="9579", amount=3200.0))
    result = reconcile_earnings_statement(statement, postings, ACCOUNTS, tolerance_days=3)
    assert result.is_fully_matched


def test_reconcile_flags_a_deposit_with_no_matching_posting() -> None:
    statement = _statement(EarningsDeposit(label="Direct Deposit", account_last4="9579", amount=3200.0))
    result = reconcile_earnings_statement(statement, _postings(), ACCOUNTS)
    assert not result.is_fully_matched
    assert result.matches[0].posting_id is None


def test_reconcile_does_not_double_match_the_same_posting_to_two_deposits() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", 100.0, "2026-06-30"),
        _posting("p2", "t1", "uncategorized:income", -100.0, "2026-06-30"),
    )
    statement = _statement(
        EarningsDeposit(label="Wage", account_last4="9579", amount=100.0),
        EarningsDeposit(label="Reimbursement", account_last4="9579", amount=100.0),
    )
    result = reconcile_earnings_statement(statement, postings, ACCOUNTS)
    matched_ids = [m.posting_id for m in result.matches if m.posting_id is not None]
    assert len(matched_ids) == 1
    assert not result.is_fully_matched
