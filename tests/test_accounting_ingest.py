import pytest

from accounting.config import AccountingConfig
from accounting.importers import ingest as ingest_module
from accounting.importers.ingest import (
    UnsupportedImportError,
    ingest_csv,
    ingest_sofi_statement_pdf,
    load_ledger,
    rebuild_from_raw_statements,
)
from accounting.importers.sofi.statement_pdf import standardize_sofi_statement_text
from accounting.store import load_store, save_store

CHASE_CHECKING_CSV = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    "CREDIT,06/30/2026,SOME EMPLOYER PAYROLL PPD ID: 1234567890,1500.00,ACH_CREDIT,4000.00,,\n"
    "DEBIT,06/29/2026,Payment to Chase card ending in 1234 06/29,-70.00,LOAN_PMT,2500.00,,\n"
)

SOFI_SAVINGS_CSV = (
    "Date,Description,Type,Amount,Current balance,Status\n"
    "2026-06-29,To Travel Vault,WITHDRAWAL,-250.00,2511.47,Posted\n"
    "2026-06-29,SOME EMPLOYER,DIRECT_DEPOSIT,2000,3261.47,Posted\n"
)


def _config(tmp_path) -> AccountingConfig:
    return AccountingConfig(data_dir=tmp_path)


def test_ingest_csv_archives_the_raw_file_verbatim(tmp_path) -> None:
    config = _config(tmp_path)
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config)

    archived = list(config.raw_statement_dir.glob("Chase/chase:checking:1234/*.csv"))
    assert len(archived) == 1
    assert archived[0].read_text() == CHASE_CHECKING_CSV


def test_ingest_csv_merges_into_the_ledger(tmp_path) -> None:
    config = _config(tmp_path)
    result = ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config)
    assert result.new_posting_count == 4
    assert result.total_posting_count == 4
    assert len(load_ledger(config)) == 4


def test_ingest_csv_reimporting_the_same_file_is_a_no_op(tmp_path) -> None:
    config = _config(tmp_path)
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config)
    second = ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config)
    assert second.new_posting_count == 0
    assert second.total_posting_count == 4


def test_ingest_csv_two_different_accounts_both_land_in_the_ledger(tmp_path) -> None:
    config = _config(tmp_path)
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config)
    ingest_csv(SOFI_SAVINGS_CSV, "SoFi", "savings", "sofi:savings:9999", config)
    ledger = load_ledger(config)
    assert set(ledger["account_id"].unique().to_list()) >= {"chase:checking:1234", "sofi:savings:9999"}


def test_ingest_csv_unsupported_institution_raises(tmp_path) -> None:
    with pytest.raises(UnsupportedImportError):
        ingest_csv("a,b\n1,2\n", "BankOfAmerica", "checking", "boa:checking:0000", _config(tmp_path))


def test_rebuild_from_raw_statements_reconstructs_the_same_ledger(tmp_path) -> None:
    config = _config(tmp_path)
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config)
    ingest_csv(SOFI_SAVINGS_CSV, "SoFi", "savings", "sofi:savings:9999", config)
    before = load_ledger(config).sort("posting_id")

    rebuilt = rebuild_from_raw_statements(config)
    after = load_ledger(config).sort("posting_id")

    assert rebuilt.sort("posting_id")["amount"].to_list() == pytest.approx(before["amount"].to_list())
    assert after["posting_id"].to_list() == before["posting_id"].to_list()


def test_rebuild_from_raw_statements_with_no_archives_raises(tmp_path) -> None:
    config = _config(tmp_path)
    with pytest.raises(FileNotFoundError):
        rebuild_from_raw_statements(config)


_SOFI_STATEMENT_TEXT = """\
Checking Account - 9169
Current Balance Monthly Interest Paid1 Annual Percentage Yield Earned1
$0.01 $0.01 0.61%
as of Apr 30, 2026
Transaction Details
Checking Account - 9169
DATE TYPE DESCRIPTION AMOUNT BALANCE
Apr 3, 2026 Withdrawal To Savings - 3680 -$300.08 $0.00
Transaction ID: 17-353515001
Savings Account - 3680
Current Balance Monthly Interest Paid1 Annual Percentage Yield Earned1
$4,539.01 $102.18 4.02%
as of Apr 30, 2026
Transaction Details
Savings Account - 3680
DATE TYPE DESCRIPTION AMOUNT BALANCE
Apr 3, 2026 Deposit From Checking - 9169 $300.08 $300.08
Transaction ID: 39-353515002
Apr 3, 2026 Withdrawal To Emergency Fund Vault -$10,000.00 $8,738.93
Transaction ID: 36-351435001
Emergency Fund Vault
DATE TYPE DESCRIPTION AMOUNT BALANCE
Apr 3, 2026 Deposit From savings balance $10,000.00 $10,000.00
Transaction ID: 17-351435002
"""


def _patch_sofi_statement_pdf(monkeypatch) -> None:
    monkeypatch.setattr(
        ingest_module,
        "standardize_sofi_statement_pdf",
        lambda _pdf_bytes: standardize_sofi_statement_text(_SOFI_STATEMENT_TEXT),
    )


def test_ingest_sofi_statement_pdf_archives_the_raw_pdf_verbatim(tmp_path, monkeypatch) -> None:
    _patch_sofi_statement_pdf(monkeypatch)
    config = _config(tmp_path)
    ingest_sofi_statement_pdf(b"%PDF-fake", config)

    archived = list(config.raw_statement_dir.glob("SoFi/statement_pdf/*.pdf"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == b"%PDF-fake"


def test_ingest_sofi_statement_pdf_registers_every_account_it_describes(tmp_path, monkeypatch) -> None:
    _patch_sofi_statement_pdf(monkeypatch)
    config = _config(tmp_path)
    result = ingest_sofi_statement_pdf(b"%PDF-fake", config)

    assert set(result.account_ids) == {
        "sofi:checking:9169",
        "sofi:savings:3680",
        "sofi:savings:3680:vault:emergency-fund",
    }
    store = load_store(config)
    assert store.accounts["sofi:savings:3680"].meta["apy_pct"] == "4.02"


def test_ingest_sofi_statement_pdf_refreshes_apy_without_touching_a_renamed_account(tmp_path, monkeypatch) -> None:
    _patch_sofi_statement_pdf(monkeypatch)
    config = _config(tmp_path)
    ingest_sofi_statement_pdf(b"%PDF-fake", config)

    store = load_store(config)
    renamed = store.accounts["sofi:savings:3680"].model_copy(update={"name": "My Savings"})
    save_store(store.model_copy(update={"accounts": {**store.accounts, "sofi:savings:3680": renamed}}), config)

    ingest_sofi_statement_pdf(b"%PDF-fake", config)
    refreshed = load_store(config).accounts["sofi:savings:3680"]
    assert refreshed.name == "My Savings"
    assert refreshed.meta["apy_pct"] == "4.02"


def test_rebuild_from_raw_statements_replays_archived_sofi_pdfs(tmp_path, monkeypatch) -> None:
    _patch_sofi_statement_pdf(monkeypatch)
    config = _config(tmp_path)
    ingest_sofi_statement_pdf(b"%PDF-fake", config)

    rebuilt = rebuild_from_raw_statements(config)
    assert set(rebuilt["account_id"].unique().to_list()) >= {"sofi:checking:9169", "sofi:savings:3680"}
    assert "sofi:savings:3680:vault:emergency-fund" in load_store(config).accounts
