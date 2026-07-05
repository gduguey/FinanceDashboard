from datetime import datetime

import polars as pl
import pytest

from accounting.importers.common import RawLeg, posting_pair, postings_to_frame
from accounting.ledger.categorization import (
    apply_manual_overrides,
    apply_posting_splits,
    apply_rules,
    detect_sofi_internal_account_transfer,
    detect_vault_transfer,
)
from accounting.models import Account, ManualOverride, PostingSplit, PostingSplitLeg, Rule
from accounting.store import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID


def _leg(amount: float, description: str, currency: str = "USD") -> RawLeg:
    return RawLeg(posted_at=datetime(2026, 6, 29), amount=amount, currency=currency, description=description, meta={})


def _placeholder_pair(source: str, row_id: str, account_id: str, leg: RawLeg) -> list:
    counterparty = UNCATEGORIZED_INCOME_ACCOUNT_ID if leg.amount >= 0 else UNCATEGORIZED_EXPENSE_ACCOUNT_ID
    return posting_pair(
        source=source, row_id=row_id, account_id=account_id, counterparty_account_id=counterparty, leg=leg
    )


SOFI_SAVINGS = Account(
    account_id="sofi:savings:3680", name="SoFi Savings", kind="savings", institution="SoFi", currency="USD"
)
EQORE_RULE = Rule(
    rule_id="eqore-payroll",
    description_contains="EQORE Inc.",
    counterparty_account_id="employer:eqore",
    counterparty_account_name="EQORE Inc. (Employer)",
    counterparty_account_kind="income_source",
    category_id="income:salary",
)


def test_detect_vault_transfer_recognizes_to_and_from() -> None:
    assert detect_vault_transfer("To Travel Vault") == "Travel"
    assert detect_vault_transfer("From House Vault") == "House"


def test_detect_vault_transfer_ignores_unrelated_descriptions() -> None:
    assert detect_vault_transfer("MARKET BASKET 00000281") is None


def test_apply_rules_creates_a_vault_account_from_a_sofi_savings_transfer() -> None:
    postings = postings_to_frame(
        _placeholder_pair("sofi-savings", "1", "sofi:savings:3680", _leg(-250.0, "To Travel Vault"))
    )
    resolved, accounts = apply_rules(postings, [], {"sofi:savings:3680": SOFI_SAVINGS})

    vault_id = "sofi:savings:3680:vault:travel"
    assert vault_id in accounts
    assert accounts[vault_id].parent_account_id == "sofi:savings:3680"
    assert set(resolved["account_id"].unique().to_list()) == {"sofi:savings:3680", vault_id}
    assert resolved.filter(pl.col("account_id") == vault_id)["amount"].to_list() == pytest.approx([250.0])


def test_apply_rules_matches_a_rule_and_sets_category() -> None:
    postings = postings_to_frame(
        _placeholder_pair("sofi-savings", "1", "sofi:savings:3680", _leg(2000.0, "EQORE Inc."))
    )
    resolved, accounts = apply_rules(postings, [EQORE_RULE], {"sofi:savings:3680": SOFI_SAVINGS})

    assert "employer:eqore" in accounts
    real_leg = resolved.filter(pl.col("account_id") == "sofi:savings:3680").row(0, named=True)
    assert real_leg["category_id"] == "income:salary"
    counterparty_leg = resolved.filter(pl.col("account_id") == "employer:eqore").row(0, named=True)
    assert counterparty_leg["amount"] == pytest.approx(-2000.0)


def test_apply_rules_respects_a_rule_scoped_to_one_account() -> None:
    scoped_rule = Rule(
        rule_id="chase-card-payoff",
        description_contains="Payment to Chase card ending in 8235",
        account_id="chase:checking:9579",
        counterparty_account_id="chase:credit_card:8235",
        counterparty_account_name="Chase Credit Card",
        counterparty_account_kind="credit_card",
    )
    matching = postings_to_frame(
        _placeholder_pair(
            "chase-checking", "1", "chase:checking:9579", _leg(-70.0, "Payment to Chase card ending in 8235")
        )
    )
    _resolved, accounts = apply_rules(matching, [scoped_rule], {})
    assert "chase:credit_card:8235" in accounts

    elsewhere = postings_to_frame(
        _placeholder_pair(
            "chase-checking", "2", "some:other:account", _leg(-70.0, "Payment to Chase card ending in 8235")
        )
    )
    resolved_elsewhere, accounts_elsewhere = apply_rules(elsewhere, [scoped_rule], {})
    assert "chase:credit_card:8235" not in accounts_elsewhere
    counterparties = set(resolved_elsewhere["account_id"].unique().to_list()) - {"some:other:account"}
    assert counterparties == {UNCATEGORIZED_EXPENSE_ACCOUNT_ID}


def test_apply_rules_leaves_unmatched_postings_as_placeholders() -> None:
    postings = postings_to_frame(
        _placeholder_pair("chase-checking", "1", "chase:checking:9579", _leg(-9.79, "TELLO US"))
    )
    resolved, accounts = apply_rules(postings, [EQORE_RULE], {})
    counterparties = set(resolved["account_id"].unique().to_list()) - {"chase:checking:9579"}
    assert counterparties == {UNCATEGORIZED_EXPENSE_ACCOUNT_ID}
    assert accounts == {}


def test_detect_sofi_internal_account_transfer_recognizes_to_checking_and_savings() -> None:
    assert detect_sofi_internal_account_transfer("To Savings - 3680") == ("Savings", "3680")
    assert detect_sofi_internal_account_transfer("To Checking - 9169") == ("Checking", "9169")


def test_detect_sofi_internal_account_transfer_ignores_unrelated_descriptions() -> None:
    assert detect_sofi_internal_account_transfer("To Travel Vault") is None


def test_apply_rules_repoints_a_sofi_checking_to_savings_transfer() -> None:
    checking = Account(
        account_id="sofi:checking:9169", name="SoFi Checking", kind="checking", institution="SoFi", currency="USD"
    )
    postings = postings_to_frame(
        _placeholder_pair("sofi-statement-pdf", "1", "sofi:checking:9169", _leg(-300.08, "To Savings - 3680"))
    )
    resolved, accounts = apply_rules(postings, [], {"sofi:checking:9169": checking, "sofi:savings:3680": SOFI_SAVINGS})
    assert set(resolved["account_id"].unique().to_list()) == {"sofi:checking:9169", "sofi:savings:3680"}
    assert accounts == {"sofi:checking:9169": checking, "sofi:savings:3680": SOFI_SAVINGS}


def test_apply_manual_overrides_with_no_overrides_returns_the_same_data() -> None:
    postings = postings_to_frame(
        _placeholder_pair("chase-checking", "1", "chase:checking:9579", _leg(-9.79, "TELLO US"))
    )
    result = apply_manual_overrides(postings, {})
    assert result["amount"].to_list() == postings["amount"].to_list()


def test_apply_manual_overrides_sets_a_category_by_posting_id() -> None:
    postings = postings_to_frame(
        _placeholder_pair("chase-checking", "1", "chase:checking:9579", _leg(-9.79, "TELLO US"))
    )
    real_posting_id = postings.filter(pl.col("account_id") == "chase:checking:9579").row(0, named=True)["posting_id"]

    result = apply_manual_overrides(postings, {real_posting_id: ManualOverride(category_id="expense:subscriptions")})
    row = result.filter(pl.col("posting_id") == real_posting_id).row(0, named=True)
    assert row["category_id"] == "expense:subscriptions"


def test_apply_posting_splits_with_no_splits_returns_the_same_data() -> None:
    postings = postings_to_frame(
        _placeholder_pair("chase-checking", "1", "chase:checking:9579", _leg(3200.0, "PAYCHECK"))
    )
    result = apply_posting_splits(postings, {})
    assert result["amount"].to_list() == postings["amount"].to_list()


def test_apply_posting_splits_replaces_one_posting_with_its_legs() -> None:
    postings = postings_to_frame(
        _placeholder_pair("chase-checking", "1", "chase:checking:9579", _leg(3200.0, "PAYCHECK"))
    )
    real_posting_id = postings.filter(pl.col("account_id") == "chase:checking:9579").row(0, named=True)["posting_id"]
    split = PostingSplit(
        posting_id=real_posting_id,
        legs=[
            PostingSplitLeg(amount=3000.0, category_id="income:salary", description="Wage"),
            PostingSplitLeg(amount=200.0, category_id="income:reimbursement", description="Expense reimbursement"),
        ],
    )
    result = apply_posting_splits(postings, {real_posting_id: split})

    assert real_posting_id not in result["posting_id"].to_list()
    legs = result.filter(pl.col("account_id") == "chase:checking:9579").sort("amount")
    assert legs["amount"].to_list() == pytest.approx([200.0, 3000.0])
    assert legs["category_id"].to_list() == ["income:reimbursement", "income:salary"]
    # Every leg keeps the original transaction id, so the transaction as a
    # whole (this account's legs plus its counterparty) still sums to zero.
    assert legs["transaction_id"].n_unique() == 1


def test_apply_posting_splits_legs_are_independently_overridable() -> None:
    postings = postings_to_frame(
        _placeholder_pair("chase-checking", "1", "chase:checking:9579", _leg(3200.0, "PAYCHECK"))
    )
    real_posting_id = postings.filter(pl.col("account_id") == "chase:checking:9579").row(0, named=True)["posting_id"]
    split = PostingSplit(
        posting_id=real_posting_id,
        legs=[PostingSplitLeg(amount=3000.0), PostingSplitLeg(amount=200.0)],
    )
    split_postings = apply_posting_splits(postings, {real_posting_id: split})
    first_leg_id = f"{real_posting_id}:split:0"

    result = apply_manual_overrides(split_postings, {first_leg_id: ManualOverride(category_id="income:bonus")})
    row = result.filter(pl.col("posting_id") == first_leg_id).row(0, named=True)
    assert row["category_id"] == "income:bonus"
