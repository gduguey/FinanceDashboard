import polars as pl
import pytest

from accounting.importers.sofi.statement_pdf import parse_sofi_statement_text, standardize_sofi_statement_text

# Modeled verbatim on a real SoFi monthly statement's extracted text (see
# `accounting.importers.sofi.statement_pdf`'s module docstring for why the
# double-booked "From ..." mirrors below must not survive parsing).
STATEMENT_TEXT = """\
Checking Account - 9169
Current Balance Monthly Interest Paid1 Annual Percentage Yield Earned1
$0.01 $0.01 0.61%
as of Apr 30, 2026
Beginning Balance Year-to-date Interest Paid
$300.08 $0.09
as of Apr 1, 2026
Current balances include the amount of interest paid.
Transaction Details
Balances below are the total funds resulting from the transaction(s) posted on that day.
Checking Account - 9169
DATE TYPE DESCRIPTION AMOUNT BALANCE
Apr 30, 2026 Interest Earned Interest earned $0.01 $0.01
Transaction ID: 18-1
Apr 3, 2026 Withdrawal To Savings - 3680 -$300.08 $0.00
Transaction ID: 17-353515001
Contact Information
Savings Account - 3680
Current Balance Monthly Interest Paid1 Annual Percentage Yield Earned1
$33,704.62 $102.18 4.02%
as of Apr 30, 2026
Beginning Balance Year-to-date Interest Paid
$33,841.36 $270.09
as of Apr 1, 2026
Current balances include the amount of interest paid.
Transaction Details
Balances below are the total funds resulting from the transaction(s) posted on that day.
Savings Account - 3680
DATE TYPE DESCRIPTION AMOUNT BALANCE
Apr 30, 2026 Interest Earned Interest earned $7.70 $2,507.71
Transaction ID: 50-1
Apr 29, 2026 Withdrawal To Travel Vault -$250.00 $2,500.01
Transaction ID: 49-628823001
Apr 29, 2026 Direct Deposit EQORE Inc. PAYROLL $2,000.00 $3,250.01
Transaction ID: 47-549815001
Apr 3, 2026 Deposit From Checking - 9169 $300.08 $4,539.01
Transaction ID: 39-353515002
Apr 3, 2026 Withdrawal To Emergency Fund Vault -$10,000.00 $8,738.93
Transaction ID: 36-351435001
Emergency Fund Vault
DATE TYPE DESCRIPTION AMOUNT BALANCE
Apr 30, 2026 Interest Earned Interest earned $79.30 $25,181.73
Transaction ID: 26-1
Apr 3, 2026 Deposit From savings balance $10,000.00 $25,102.43
Transaction ID: 17-351435002
Travel Vault
DATE TYPE DESCRIPTION AMOUNT BALANCE
Apr 30, 2026 Interest Earned Interest earned $5.06 $2,005.06
Transaction ID: 26-1
Apr 29, 2026 Deposit From savings balance $250.00 $2,000.00
Transaction ID: 25-628823002
"""


def test_parse_discovers_checking_savings_and_vault_accounts_with_apy() -> None:
    parsed = parse_sofi_statement_text(STATEMENT_TEXT)
    assert parsed.accounts["sofi:checking:9169"].meta["apy_pct"] == "0.61"
    assert parsed.accounts["sofi:savings:3680"].meta["apy_pct"] == "4.02"
    vault = parsed.accounts["sofi:savings:3680:vault:emergency-fund"]
    assert vault.kind == "vault"
    assert vault.parent_account_id == "sofi:savings:3680"
    assert vault.meta["apy_pct"] == "4.02"  # vaults share the parent savings account's rate


def test_parse_drops_the_mirrored_from_side_of_internal_transfers() -> None:
    parsed = parse_sofi_statement_text(STATEMENT_TEXT)
    descriptions = [row.description for row in parsed.rows]
    assert "From Checking - 9169" not in descriptions
    assert "From savings balance" not in descriptions
    assert "To Savings - 3680" in descriptions
    assert "To Emergency Fund Vault" in descriptions


def test_parse_scopes_the_shared_interest_batch_id_by_account() -> None:
    parsed = parse_sofi_statement_text(STATEMENT_TEXT)
    interest_rows = [row for row in parsed.rows if row.transaction_id == "26-1"]
    assert {row.account_id for row in interest_rows} == {
        "sofi:savings:3680:vault:emergency-fund",
        "sofi:savings:3680:vault:travel",
    }
    assert {row.amount for row in interest_rows} == {79.30, 5.06}


def test_parse_raises_if_no_savings_section_is_present() -> None:
    with pytest.raises(ValueError, match="savings"):
        parse_sofi_statement_text("Checking Account - 9169\nDATE TYPE DESCRIPTION AMOUNT BALANCE\n")


def test_standardize_produces_balanced_two_leg_transactions() -> None:
    postings, accounts = standardize_sofi_statement_text(STATEMENT_TEXT)
    assert "sofi:checking:9169" in accounts
    assert "sofi:savings:3680:vault:travel" in accounts
    totals = postings.group_by("transaction_id").agg(total=pl.col("amount").sum())["total"].to_list()
    for total in totals:
        assert total == pytest.approx(0.0)


def test_standardize_sets_interest_earned_category_directly() -> None:
    postings, _accounts = standardize_sofi_statement_text(STATEMENT_TEXT)
    interest_row = postings.filter(
        (postings["account_id"] == "sofi:savings:3680") & (postings["description"] == "Interest earned")
    ).row(0, named=True)
    assert interest_row["category_id"] == "income:interest-earned"
