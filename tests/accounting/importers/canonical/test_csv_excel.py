import io

import pytest
import xlsxwriter

from accounting.importers.canonical.csv import CanonicalCsvError, standardize_canonical_excel

ACCOUNT_ID = "generic-bank:checking:0001"


def _workbook(sheets: dict[str, list[list[str]]]) -> bytes:
    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
    for name, rows in sheets.items():
        worksheet = workbook.add_worksheet(name)
        for row_index, row in enumerate(rows):
            for col_index, value in enumerate(row):
                worksheet.write(row_index, col_index, value)
    workbook.close()
    return buffer.getvalue()


def test_standardizes_the_first_sheet_whose_header_matches() -> None:
    transactions = [
        ["Date", "Description", "Amount"],
        ["2026-06-30", "Grocery Store", "-42.50"],
        ["2026-06-29", "Paycheck", "1500.00"],
    ]
    summary = [["Row Labels", "Total"], ["2026", "1457.50"]]
    file_bytes = _workbook({"Summary": summary, "Transactions": transactions})

    result = standardize_canonical_excel(file_bytes, ACCOUNT_ID, "USD", {})
    assert result.postings.height == 4
    real_legs = result.postings.filter(result.postings["account_id"] == ACCOUNT_ID)
    assert sorted(real_legs["amount"].to_list()) == pytest.approx([-42.50, 1500.00])


def test_raises_when_no_sheet_has_the_expected_columns() -> None:
    summary = [["Row Labels", "Total"], ["2026", "1457.50"]]
    file_bytes = _workbook({"Summary": summary})
    with pytest.raises(CanonicalCsvError):
        standardize_canonical_excel(file_bytes, ACCOUNT_ID, "USD", {})
