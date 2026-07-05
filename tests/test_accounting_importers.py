import polars as pl
import pytest

from accounting.importers.chase.checking import standardize_chase_checking
from accounting.importers.chase.credit_card import standardize_chase_credit_card
from accounting.importers.sofi.checking import standardize_sofi_checking
from accounting.importers.sofi.savings import standardize_sofi_savings
from accounting.store import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID

CHASE_CHECKING_CSV = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    "CREDIT,06/30/2026,SOME EMPLOYER PAYROLL PPD ID: 1234567890,1500.00,ACH_CREDIT,4000.00,,\n"
    "DEBIT,06/29/2026,Payment to Chase card ending in 1234 06/29,-70.00,LOAN_PMT,2500.00,,\n"
)

CHASE_CREDIT_CARD_CSV = (
    "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
    "06/30/2026,07/01/2026,SOME TOLL PLAZA,Travel,Sale,-33.70,\n"
    "06/29/2026,06/30/2026,SOME GROCERY STORE,Groceries,Sale,-44.87,\n"
)

SOFI_CHECKING_CSV = (
    "Date,Description,Type,Amount,Current balance,Status\n"
    "2026-04-30,Interest earned,INTEREST_EARNED,0.01,0.01,Posted\n"
    "2026-04-03,To Savings - 3680,WITHDRAWAL,-300.08,0,Posted\n"
)

SOFI_SAVINGS_CSV = (
    "Date,Description,Type,Amount,Current balance,Status\n"
    "2026-06-29,To Travel Vault,WITHDRAWAL,-250.00,2511.47,Posted\n"
    "2026-06-29,SOME EMPLOYER,DIRECT_DEPOSIT,2000,3261.47,Posted\n"
)


def test_standardize_chase_checking_produces_balanced_two_leg_postings() -> None:
    result = standardize_chase_checking(CHASE_CHECKING_CSV, "chase:checking:1234")
    assert len(result) == 4
    for transaction_id in result["transaction_id"].unique().to_list():
        assert result.filter(pl.col("transaction_id") == transaction_id)["amount"].sum() == pytest.approx(0.0)

    payroll = (
        result.filter(pl.col("account_id") == "chase:checking:1234").sort("amount", descending=True).row(0, named=True)
    )
    assert payroll["amount"] == pytest.approx(1500.00)
    assert payroll["description"] == "SOME EMPLOYER PAYROLL PPD ID: 1234567890"

    counterparties = set(result["account_id"].unique().to_list()) - {"chase:checking:1234"}
    assert counterparties == {UNCATEGORIZED_INCOME_ACCOUNT_ID, UNCATEGORIZED_EXPENSE_ACCOUNT_ID}


def test_standardize_chase_checking_is_deterministic_and_dedupable() -> None:
    first = standardize_chase_checking(CHASE_CHECKING_CSV, "chase:checking:1234")
    second = standardize_chase_checking(CHASE_CHECKING_CSV, "chase:checking:1234")
    assert first["posting_id"].to_list() == second["posting_id"].to_list()


def test_standardize_chase_credit_card_uses_post_date_and_keeps_source_category() -> None:
    result = standardize_chase_credit_card(CHASE_CREDIT_CARD_CSV, "chase:credit_card:1234")
    toll = result.filter(pl.col("description") == "SOME TOLL PLAZA").row(0, named=True)
    assert toll["posted_at"].isoformat() == "2026-07-01T00:00:00"
    assert toll["amount"] == pytest.approx(-33.70)
    assert toll["meta"]["source_category"] == "Travel"


def test_standardize_chase_credit_card_drops_payment_thank_you_rows() -> None:
    csv_text = (
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
        "06/30/2026,07/01/2026,SOME TOLL PLAZA,Travel,Sale,-33.70,\n"
        "06/27/2026,06/28/2026,Payment Thank You-Mobile,,Payment,70.00,\n"
    )
    result = standardize_chase_credit_card(csv_text, "chase:credit_card:1234")
    assert len(result) == 2  # only the toll's own pair — the payment row is dropped entirely
    assert "Payment Thank You-Mobile" not in result["description"].to_list()


def test_standardize_sofi_checking_maps_interest_and_withdrawal() -> None:
    result = standardize_sofi_checking(SOFI_CHECKING_CSV, "sofi:checking:9999")
    real_leg = result.filter(pl.col("account_id") == "sofi:checking:9999").sort("amount")
    assert real_leg["amount"].to_list() == pytest.approx([-300.08, 0.01])


def test_standardize_sofi_savings_leaves_vault_transfers_as_generic_placeholders_for_now() -> None:
    result = standardize_sofi_savings(SOFI_SAVINGS_CSV, "sofi:savings:9999")
    counterparties = set(result["account_id"].unique().to_list()) - {"sofi:savings:9999"}
    assert counterparties == {UNCATEGORIZED_INCOME_ACCOUNT_ID, UNCATEGORIZED_EXPENSE_ACCOUNT_ID}
