from accounting.importers.detect import detect_bank_account


def test_detects_chase_credit_card_from_its_header_and_filename() -> None:
    header = ["Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"]
    result = detect_bank_account(header, "Chase8235_Activity20240704_20260704_20260704.CSV")
    assert result is not None
    assert result.institution == "Chase"
    assert result.account_kind == "credit_card"
    assert result.account_id == "chase:credit_card:8235"


def test_detects_chase_checking_from_its_header_and_filename() -> None:
    header = ["Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #"]
    result = detect_bank_account(header, "Chase9579_Activity_20260704.CSV")
    assert result is not None
    assert result.institution == "Chase"
    assert result.account_kind == "checking"
    assert result.account_id == "chase:checking:9579"


def test_detects_sofi_checking_from_its_header_and_filename() -> None:
    header = ["Date", "Description", "Type", "Amount", "Current balance", "Status"]
    result = detect_bank_account(header, "SOFI-Checking•9169-2026-07-04T07_30_16.csv")
    assert result is not None
    assert result.institution == "SoFi"
    assert result.account_kind == "checking"
    assert result.account_id == "sofi:checking:9169"


def test_detects_sofi_savings_from_its_header_and_filename() -> None:
    header = ["Date", "Description", "Type", "Amount", "Current balance", "Status"]
    result = detect_bank_account(header, "SOFI-Savings•3680-2026-07-04T07_29_19.csv")
    assert result is not None
    assert result.institution == "SoFi"
    assert result.account_kind == "savings"
    assert result.account_id == "sofi:savings:3680"


def test_unrecognized_header_returns_none() -> None:
    assert detect_bank_account(["Some", "Random", "Columns"], "whatever.csv") is None


def test_chase_header_without_an_account_number_in_the_filename_returns_none() -> None:
    header = ["Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #"]
    assert detect_bank_account(header, "checking.csv") is None
