import polars as pl
import pytest

from accounting.importers.chase.checking import standardize_chase_checking
from accounting.importers.chase.credit_card import standardize_chase_credit_card
from accounting.importers.sofi.csv import (
    is_sofi_csv,
    parse_account_name,
    standardize_sofi_checking,
    standardize_sofi_savings,
)
from accounting.taxonomy import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID

# A real "Emergency Fund" vault export, SoFi's newer, wider CSV shape (also
# covers checking/savings accounts — see `importers.sofi.csv`).
SOFI_VAULT_CSV = (
    "Authorized Date,Posted Date,Status,Account Name,Description,Primary Category,Detailed Category,Amount\n"
    "2026-06-30,2026-06-30,Posted,Emergency Fund ***3680,Interest,Income,Interest,77.97\n"
    "2026-04-03,2026-04-03,Posted,Emergency Fund ***3680,Transfer From Savings,Transfers,Savings transfers,10000.00\n"
)

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


# The same $1500 deposit, in each importer's own file shape, with the Amount
# column left as a `{}` slot for the scale variants below. One per `row_hash`
# call site, so all four are covered rather than one Chase and one SoFi.
_AMOUNT_SCALE_TEMPLATES = {
    "chase-checking": (
        standardize_chase_checking,
        "chase:checking:1234",
        (
            "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
            "CREDIT,06/30/2026,SOME EMPLOYER PAYROLL,{},ACH_CREDIT,4000.00,,\n"
        ),
    ),
    "chase-credit-card": (
        standardize_chase_credit_card,
        "chase:credit_card:1234",
        (
            "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
            "06/29/2026,06/30/2026,Payment Thank You-Mobile,,Payment,{},\n"
        ),
    ),
    "sofi-legacy": (
        standardize_sofi_checking,
        "sofi:checking:9999",
        "Date,Description,Type,Amount,Current balance,Status\n2026-06-29,SOME EMPLOYER,DIRECT_DEPOSIT,{},4000.00,Posted\n",
    ),
    "sofi-wide": (
        standardize_sofi_checking,
        "sofi:checking:9999",
        (
            "Authorized Date,Posted Date,Status,Account Name,Description,Primary Category,Detailed Category,Amount\n"
            "2026-06-29,2026-06-29,Posted,Checking ***9999,SOME EMPLOYER,Income,Wages,{}\n"
        ),
    ),
}


@pytest.mark.parametrize("shape", list(_AMOUNT_SCALE_TEMPLATES))
def test_transaction_ids_ignore_the_source_files_decimal_scale(shape: str) -> None:
    """Re-importing the same statement must not mint a new natural key just because the scale changed.

    `row_hash` is fed the amount as a string, and used to be handed
    `str(row.amount)` while `amount` was a `float` — which normalized
    `1500.0`, `1500.00` and `1500` to one `'1500.0'`. `amount` is a `Money`
    (`Decimal`) now, and `str(Decimal)` preserves the source file's own
    trailing zeros, so those three spellings of a single transaction hashed
    to three *different* `transaction_id`s. Dedup would then break the
    first time a bank changed how it formats its exports, silently
    doubling every re-imported row.

    Every call site formats at `MONEY_SCALE` (`f"{...:.4f}"`) instead,
    agreeing with `importers.canonical.csv`, which already did this.

    `1,500.00` and `$1500.00` are deliberately *not* in here: those raise
    `ValidationError` in the row model, which is pre-existing behaviour and
    a separate question from this one.
    """
    standardize, account_id, template = _AMOUNT_SCALE_TEMPLATES[shape]
    ids = {
        standardize(template.format(amount), account_id)["transaction_id"].to_list()[0]
        for amount in ("1500.0", "1500.00", "1500")
    }
    assert len(ids) == 1


def test_standardize_chase_credit_card_uses_post_date_and_keeps_source_category() -> None:
    result = standardize_chase_credit_card(CHASE_CREDIT_CARD_CSV, "chase:credit_card:1234")
    toll = result.filter(pl.col("description") == "SOME TOLL PLAZA").row(0, named=True)
    assert toll["posted_at"].isoformat() == "2026-07-01T00:00:00"
    assert toll["amount"] == pytest.approx(-33.70)
    assert toll["meta"]["source_category"] == "Travel"


def test_standardize_chase_credit_card_keeps_payment_thank_you_rows() -> None:
    csv_text = (
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
        "06/30/2026,07/01/2026,SOME TOLL PLAZA,Travel,Sale,-33.70,\n"
        "06/27/2026,06/28/2026,Payment Thank You-Mobile,,Payment,70.00,\n"
    )
    result = standardize_chase_credit_card(csv_text, "chase:credit_card:1234")
    assert len(result) == 4  # both rows' own pair — nothing is dropped at import time
    payment = result.filter(
        (pl.col("description") == "Payment Thank You-Mobile") & (pl.col("account_id") == "chase:credit_card:1234")
    ).row(0, named=True)
    assert payment["amount"] == pytest.approx(70.00)
    # Not yet repointed at the paying checking account — that's a `TransferRule`'s job, not this importer's.
    counterparty = result.filter(
        (pl.col("description") == "Payment Thank You-Mobile")
        & (pl.col("account_id") == UNCATEGORIZED_INCOME_ACCOUNT_ID)
    )
    assert len(counterparty) == 1


def test_standardize_sofi_checking_maps_interest_and_withdrawal() -> None:
    result = standardize_sofi_checking(SOFI_CHECKING_CSV, "sofi:checking:9999")
    real_leg = result.filter(pl.col("account_id") == "sofi:checking:9999").sort("amount")
    assert real_leg["amount"].to_list() == pytest.approx([-300.08, 0.01])


def test_standardize_sofi_savings_leaves_vault_transfers_as_generic_placeholders_for_now() -> None:
    result = standardize_sofi_savings(SOFI_SAVINGS_CSV, "sofi:savings:9999")
    counterparties = set(result["account_id"].unique().to_list()) - {"sofi:savings:9999"}
    assert counterparties == {UNCATEGORIZED_INCOME_ACCOUNT_ID, UNCATEGORIZED_EXPENSE_ACCOUNT_ID}


def test_is_sofi_csv_recognizes_the_header() -> None:
    assert is_sofi_csv(SOFI_VAULT_CSV)
    assert not is_sofi_csv(SOFI_SAVINGS_CSV)


def test_parse_account_name_recognizes_a_vault() -> None:
    parsed = parse_account_name("Emergency Fund ***3680")
    assert parsed is not None
    assert parsed.label == "Emergency Fund"
    assert parsed.last4 == "3680"
    assert parsed.kind is None


def test_parse_account_name_recognizes_checking_and_savings() -> None:
    assert parse_account_name("Checking ***9169").kind == "checking"
    assert parse_account_name("SoFi HYSA ***3680").kind == "savings"


def test_parse_account_name_returns_none_without_trailing_digits() -> None:
    assert parse_account_name("Some free text") is None


def test_standardize_sofi_wide_csv_leaves_interest_rows_uncategorized() -> None:
    # SoFi's own "Detailed Category" column says "Interest" here, but this
    # importer no longer trusts it to bypass categorization — a category
    # pattern (or manual edit) has to assign one, same as any other row.
    result = standardize_sofi_savings(SOFI_VAULT_CSV, "sofi:savings:3680:vault:emergency-fund")
    interest_leg = result.filter(pl.col("description") == "Interest").row(0, named=True)
    assert interest_leg["category_id"] is None
    assert interest_leg["amount"] == pytest.approx(77.97)


def test_standardize_sofi_wide_csv_leaves_a_savings_transfer_as_a_generic_placeholder_too() -> None:
    # A vault's own "Transfer From/To Savings" row used to be pointed
    # straight at `parent_account_id`, bypassing rules entirely — but the
    # parent (savings) account's own export independently records the same
    # transfer too, so this double-booked it. Left as a placeholder now,
    # same as every other transfer, so a `TransferRule` resolves it exactly
    # once.
    vault_id = "a1b2c3d4"
    result = standardize_sofi_savings(SOFI_VAULT_CSV, vault_id)
    counterparties = set(result["account_id"].unique().to_list()) - {vault_id}
    assert "sofi:savings:3680" not in counterparties
    # Both of SOFI_VAULT_CSV's rows are positive (interest earned, money
    # arriving from savings), so both land on the income placeholder.
    assert counterparties == {UNCATEGORIZED_INCOME_ACCOUNT_ID}
    transfer_leg = result.filter(
        (pl.col("account_id") == vault_id) & (pl.col("description") == "Transfer From Savings")
    ).row(0, named=True)
    assert transfer_leg["amount"] == pytest.approx(10000.0)


def test_standardize_sofi_wide_csv_used_via_the_checking_and_savings_dispatchers() -> None:
    vault_id = "a1b2c3d4"
    via_savings = standardize_sofi_savings(SOFI_VAULT_CSV, vault_id)
    via_checking = standardize_sofi_checking(SOFI_VAULT_CSV, vault_id)
    assert len(via_savings) == len(via_checking) == 4
