import pytest

from accounting.importers.canonical.csv import (
    CanonicalCsvError,
    CanonicalCsvSeparatorUnknownError,
    standardize_canonical_csv,
)
from accounting.models import Category
from accounting.store import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID

ACCOUNT_ID = "generic-bank:checking:0001"


def test_standardizes_a_basic_date_description_amount_csv() -> None:
    csv_text = "Date,Description,Amount\n2026-06-30,Grocery Store,-42.50\n2026-06-29,Paycheck,1500.00\n"
    result = standardize_canonical_csv(csv_text, ACCOUNT_ID, "USD", {})
    assert result.postings.height == 4  # two rows, two legs each
    real_legs = result.postings.filter(result.postings["account_id"] == ACCOUNT_ID)
    assert sorted(real_legs["amount"].to_list()) == pytest.approx([-42.50, 1500.00])
    assert set(real_legs["currency"].to_list()) == {"USD"}
    counterparties = set(result.postings["account_id"].to_list()) - {ACCOUNT_ID}
    assert counterparties == {UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID}


def test_standardizes_debit_credit_columns_instead_of_a_single_amount() -> None:
    csv_text = "Date,Description,Debit,Credit\n2026-06-30,Grocery Store,42.50,\n2026-06-29,Paycheck,,1500.00\n"
    result = standardize_canonical_csv(csv_text, ACCOUNT_ID, "USD", {})
    real_legs = result.postings.filter(result.postings["account_id"] == ACCOUNT_ID)
    assert sorted(real_legs["amount"].to_list()) == pytest.approx([-42.50, 1500.00])


def test_raises_a_clear_error_when_required_columns_are_missing() -> None:
    csv_text = "Foo,Bar\n1,2\n"
    with pytest.raises(CanonicalCsvError, match="Date"):
        standardize_canonical_csv(csv_text, ACCOUNT_ID, "USD", {})


def test_raises_when_too_many_rows_fail_to_parse() -> None:
    csv_text = "Date,Description,Amount\nnot-a-date,Grocery Store,not-a-number\n"
    with pytest.raises(CanonicalCsvError):
        standardize_canonical_csv(csv_text, ACCOUNT_ID, "USD", {})


def test_auto_creates_a_new_expense_category_with_trailing_space_ignored() -> None:
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Grocery Store,-42.50,Groceries \n"
    result = standardize_canonical_csv(csv_text, ACCOUNT_ID, "USD", {})
    assert len(result.new_categories) == 1
    category = next(iter(result.new_categories.values()))
    assert category.name == "Groceries"
    assert category.classification == "expense"
    assert category.parent_category_id is None
    real_leg = result.postings.filter(result.postings["account_id"] == ACCOUNT_ID).row(0, named=True)
    assert real_leg["category_id"] == category.category_id


def test_auto_creates_a_subcategory_under_its_category() -> None:
    csv_text = "Date,Description,Amount,Category,Subcategory\n2026-06-30,Store,-42.50,Food,Groceries\n"
    result = standardize_canonical_csv(csv_text, ACCOUNT_ID, "USD", {})
    assert len(result.new_categories) == 2
    parent = next(c for c in result.new_categories.values() if c.parent_category_id is None)
    child = next(c for c in result.new_categories.values() if c.parent_category_id is not None)
    assert child.parent_category_id == parent.category_id
    assert child.classification == parent.classification


def test_reuses_an_existing_category_matched_case_insensitively() -> None:
    existing = Category(category_id="expense:groceries", name="Groceries", classification="expense", color="#123456")
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,GROCERIES\n"
    result = standardize_canonical_csv(csv_text, ACCOUNT_ID, "USD", {existing.category_id: existing})
    assert result.new_categories == {}
    real_leg = result.postings.filter(result.postings["account_id"] == ACCOUNT_ID).row(0, named=True)
    assert real_leg["category_id"] == "expense:groceries"


def test_auto_detects_a_semicolon_separator() -> None:
    csv_text = "Date;Description;Amount\n2026-06-30;Grocery Store;-42.50\n"
    result = standardize_canonical_csv(csv_text, ACCOUNT_ID, "USD", {})
    assert result.postings.height == 2


def test_raises_a_separator_specific_error_when_the_delimiter_cant_be_auto_detected() -> None:
    csv_text = "Date~Description~Amount\n2026-06-30~Grocery Store~-42.50\n"
    with pytest.raises(CanonicalCsvSeparatorUnknownError):
        standardize_canonical_csv(csv_text, ACCOUNT_ID, "USD", {})


def test_an_explicit_separator_overrides_auto_detection() -> None:
    csv_text = "Date~Description~Amount\n2026-06-30~Grocery Store~-42.50\n"
    result = standardize_canonical_csv(csv_text, ACCOUNT_ID, "USD", {}, separator="~")
    assert result.postings.height == 2
