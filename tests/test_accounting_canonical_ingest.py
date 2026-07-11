import io

import pytest
import xlsxwriter

from accounting.config import AccountingConfig
from accounting.importers.canonical.csv import CanonicalCsvError
from accounting.importers.ingest import (
    ingest_canonical_csv,
    ingest_canonical_excel,
    load_ledger,
    remap_ledger_category_ids,
)
from accounting.models import Account
from accounting.store import load_store, save_store

ACCOUNT_ID = "generic-bank:checking:0001"
CSV_TEXT = "Date,Description,Amount,Category\n2026-06-30,Grocery Store,-42.50,Groceries\n2026-06-29,Paycheck,1500.00,\n"


def _config(tmp_path) -> AccountingConfig:
    return AccountingConfig(data_dir=tmp_path)


def _register_account(config: AccountingConfig) -> None:
    store = load_store(config)
    account = Account(
        account_id=ACCOUNT_ID, name="Generic Checking", kind="checking", institution="Generic Bank", currency="USD"
    )
    save_store(store.model_copy(update={"accounts": {**store.accounts, ACCOUNT_ID: account}}), config)


def test_ingest_canonical_csv_archives_the_raw_file_verbatim(tmp_path) -> None:
    config = _config(tmp_path)
    _register_account(config)
    ingest_canonical_csv(CSV_TEXT, ACCOUNT_ID, config)

    archived = list(config.raw_statement_dir.glob("Generic Bank/*/*.csv"))
    assert len(archived) == 1
    assert archived[0].read_text() == CSV_TEXT


def test_ingest_canonical_csv_merges_into_the_ledger(tmp_path) -> None:
    config = _config(tmp_path)
    _register_account(config)
    result = ingest_canonical_csv(CSV_TEXT, ACCOUNT_ID, config)
    assert result.new_posting_count == 4
    assert len(load_ledger(config)) == 4


def test_ingest_canonical_csv_persists_newly_created_categories(tmp_path) -> None:
    config = _config(tmp_path)
    _register_account(config)
    result = ingest_canonical_csv(CSV_TEXT, ACCOUNT_ID, config)
    assert len(result.new_categories) == 1
    category = next(iter(result.new_categories.values()))
    assert category.name == "Groceries"

    store = load_store(config)
    assert category.category_id in store.categories


def test_remap_ledger_category_ids_repoints_a_postings_baked_in_category(tmp_path) -> None:
    config = _config(tmp_path)
    _register_account(config)
    result = ingest_canonical_csv(CSV_TEXT, ACCOUNT_ID, config)
    category = next(iter(result.new_categories.values()))

    remap_ledger_category_ids({category.category_id: "expense:food"}, config)

    ledger = load_ledger(config)
    category_ids = set(ledger["category_id"].to_list())
    assert category.category_id not in category_ids
    assert "expense:food" in category_ids


def test_remap_ledger_category_ids_is_a_no_op_with_an_empty_remap(tmp_path) -> None:
    config = _config(tmp_path)
    _register_account(config)
    ingest_canonical_csv(CSV_TEXT, ACCOUNT_ID, config)
    before = load_ledger(config).sort("posting_id")["category_id"].to_list()

    remap_ledger_category_ids({}, config)

    after = load_ledger(config).sort("posting_id")["category_id"].to_list()
    assert before == after


def test_ingest_canonical_csv_raises_when_the_account_isnt_registered(tmp_path) -> None:
    config = _config(tmp_path)
    with pytest.raises(KeyError):
        ingest_canonical_csv(CSV_TEXT, ACCOUNT_ID, config)


def test_ingest_canonical_csv_raises_a_clear_error_for_an_unparseable_file(tmp_path) -> None:
    config = _config(tmp_path)
    _register_account(config)
    with pytest.raises(CanonicalCsvError):
        ingest_canonical_csv("Foo,Bar\n1,2\n", ACCOUNT_ID, config)


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
    worksheet = workbook.add_worksheet("Transactions")
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            worksheet.write(row_index, col_index, value)
    workbook.close()
    return buffer.getvalue()


def test_ingest_canonical_excel_archives_and_merges_into_the_ledger(tmp_path) -> None:
    config = _config(tmp_path)
    _register_account(config)
    file_bytes = _xlsx_bytes([
        ["Date", "Description", "Amount"],
        ["2026-06-30", "Grocery Store", "-42.50"],
    ])
    result = ingest_canonical_excel(file_bytes, ACCOUNT_ID, config)
    assert result.new_posting_count == 2
    assert len(load_ledger(config)) == 2
    archived = list(config.raw_statement_dir.glob("Generic Bank/*/*.xlsx"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == file_bytes
