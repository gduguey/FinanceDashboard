from accounting.importers.detect import detect_bank_account


def test_detects_chase_credit_card_from_its_header_and_filename() -> None:
    header = ["Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"]
    result = detect_bank_account(header, "Chase8235_Activity20240704_20260704_20260704.CSV")
    assert result is not None
    assert result.institution == "Chase"
    assert result.account_kind == "credit_card"


def test_detects_chase_checking_from_its_header_and_filename() -> None:
    header = ["Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #"]
    result = detect_bank_account(header, "Chase9579_Activity_20260704.CSV")
    assert result is not None
    assert result.institution == "Chase"
    assert result.account_kind == "checking"


def test_detects_sofi_checking_from_its_header_and_filename() -> None:
    header = ["Date", "Description", "Type", "Amount", "Current balance", "Status"]
    result = detect_bank_account(header, "SOFI-Checking•9169-2026-07-04T07_30_16.csv")
    assert result is not None
    assert result.institution == "SoFi"
    assert result.account_kind == "checking"


def test_detects_sofi_savings_from_its_header_and_filename() -> None:
    header = ["Date", "Description", "Type", "Amount", "Current balance", "Status"]
    result = detect_bank_account(header, "SOFI-Savings•3680-2026-07-04T07_29_19.csv")
    assert result is not None
    assert result.institution == "SoFi"
    assert result.account_kind == "savings"


_SOFI_CSV_HEADER = [
    "Authorized Date",
    "Posted Date",
    "Status",
    "Account Name",
    "Description",
    "Primary Category",
    "Detailed Category",
    "Amount",
]


def test_detects_a_sofi_csv_vault_from_its_first_data_row() -> None:
    result = detect_bank_account(
        _SOFI_CSV_HEADER, "Emergency Fund_2026-07-05.csv", {"Account Name": "Emergency Fund ***3680"}
    )
    assert result is not None
    assert result.institution == "SoFi"
    assert result.account_kind == "vault"


def test_detects_a_sofi_csv_savings_account_from_its_first_data_row() -> None:
    result = detect_bank_account(_SOFI_CSV_HEADER, "export.csv", {"Account Name": "SoFi HYSA ***3680"})
    assert result is not None
    assert result.account_kind == "savings"


def test_sofi_csv_without_a_first_data_row_returns_none() -> None:
    assert detect_bank_account(_SOFI_CSV_HEADER, "export.csv") is None


def test_unrecognized_header_returns_none() -> None:
    assert detect_bank_account(["Some", "Random", "Columns"], "whatever.csv") is None


def test_chase_header_without_an_account_number_in_the_filename_returns_none() -> None:
    header = ["Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #"]
    assert detect_bank_account(header, "checking.csv") is None
