from datetime import datetime

import polars as pl
import pytest

from accounting.dashboard.paystub import propose_posting_splits, reconcile_earnings_statement
from accounting.models import Account, EarningsDeposit, EarningsLineItem, EarningsStatement, Posting

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


def _statement(
    *deposits: EarningsDeposit, net_pay: float = 3200.0, reimbursement_lines: list[EarningsLineItem] | None = None
) -> EarningsStatement:
    return EarningsStatement(
        pay_date=datetime(2026, 6, 30),
        gross_pay=4000.0,
        taxes_withheld=800.0,
        net_pay=net_pay,
        deposits=list(deposits),
        reimbursement_lines=reimbursement_lines or [],
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


def test_propose_posting_splits_gives_a_single_salary_leg_with_no_reimbursements() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", 3200.0, "2026-06-30"),
        _posting("p2", "t1", "uncategorized:income", -3200.0, "2026-06-30"),
    )
    statement = _statement(EarningsDeposit(label="Direct Deposit", account_last4="9579", amount=3200.0))
    result = reconcile_earnings_statement(statement, postings, ACCOUNTS)
    proposals = propose_posting_splits(statement, result.matches)
    assert len(proposals) == 1
    assert len(proposals[0].legs) == 1
    assert proposals[0].legs[0].category_id == "income:salary"
    assert proposals[0].legs[0].amount == pytest.approx(3200.0)


def test_propose_posting_splits_separates_salary_from_reimbursements_on_one_deposit() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", 3400.0, "2026-06-30"),
        _posting("p2", "t1", "uncategorized:income", -3400.0, "2026-06-30"),
    )
    statement = _statement(
        EarningsDeposit(label="Direct Deposit", account_last4="9579", amount=3400.0),
        net_pay=3200.0,
        reimbursement_lines=[EarningsLineItem(label="QSEHRA", amount=200.0)],
    )
    result = reconcile_earnings_statement(statement, postings, ACCOUNTS)
    proposals = propose_posting_splits(statement, result.matches)
    assert len(proposals) == 1
    legs = proposals[0].legs
    assert sum(leg.amount for leg in legs) == pytest.approx(3400.0)
    reimbursement_leg = next(leg for leg in legs if leg.category_id == "income:reimbursement")
    assert reimbursement_leg.amount == pytest.approx(200.0)
    assert reimbursement_leg.description == "QSEHRA"
    salary_leg = next(leg for leg in legs if leg.category_id == "income:salary")
    assert salary_leg.amount == pytest.approx(3200.0)


def test_propose_posting_splits_assigns_reimbursements_to_the_largest_deposit_first() -> None:
    checking = Account(
        account_id="chase:checking:9579", name="Checking", kind="checking", institution="Chase", currency="USD"
    )
    savings = Account(
        account_id="sofi:savings:3680", name="Savings", kind="savings", institution="SoFi", currency="USD"
    )
    accounts = {**ACCOUNTS, checking.account_id: checking, savings.account_id: savings}
    postings = _postings(
        _posting("p1", "t1", "sofi:savings:3680", 2000.0, "2026-06-30"),
        _posting("p2", "t1", "uncategorized:income", -2000.0, "2026-06-30"),
        _posting("p3", "t2", "chase:checking:9579", 2715.82, "2026-06-30"),
        _posting("p4", "t2", "uncategorized:income", -2715.82, "2026-06-30"),
    )
    statement = _statement(
        EarningsDeposit(label="SoFi HYSA", account_last4="3680", amount=2000.0),
        EarningsDeposit(label="Chase Checking", account_last4="9579", amount=2715.82),
        net_pay=3527.06,
        reimbursement_lines=[
            EarningsLineItem(label="QSEHRA", amount=400.0),
            EarningsLineItem(label="Late-night meal", amount=18.76),
            EarningsLineItem(label="STEM OPT fees", amount=770.0),
        ],
    )
    result = reconcile_earnings_statement(statement, postings, accounts)
    proposals = propose_posting_splits(statement, result.matches)

    by_account = {proposal.account_id: proposal for proposal in proposals}
    chase_proposal = by_account["chase:checking:9579"]
    sofi_proposal = by_account["sofi:savings:3680"]

    reimbursement_total_chase = sum(
        leg.amount for leg in chase_proposal.legs if leg.category_id == "income:reimbursement"
    )
    assert reimbursement_total_chase == pytest.approx(1188.76)
    assert all(leg.category_id != "income:reimbursement" for leg in sofi_proposal.legs)
    assert sum(leg.amount for leg in sofi_proposal.legs) == pytest.approx(2000.0)
    assert sum(leg.amount for leg in chase_proposal.legs) == pytest.approx(2715.82)


def test_propose_posting_splits_skips_unmatched_deposits() -> None:
    statement = _statement(EarningsDeposit(label="Direct Deposit", account_last4="9579", amount=3200.0))
    result = reconcile_earnings_statement(statement, _postings(), ACCOUNTS)
    assert propose_posting_splits(statement, result.matches) == []
