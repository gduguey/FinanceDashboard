import pytest

from accounting.config import AccountingConfig
from accounting.importers.ingest import UnsupportedImportError, ingest_csv, load_ledger, rebuild_from_raw_statements

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
