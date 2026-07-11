from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from accounting.config import AccountingConfig
from accounting.importers import ingest as ingest_module
from accounting.importers.ingest import (
    UnsupportedImportError,
    ingest_csv,
    load_ledger,
    rebuild_from_raw_statements,
)
from accounting.importers.sofi.statement_pdf import standardize_sofi_statement_text
from accounting.models import Account
from accounting.store import load_store, save_store

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

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


def _register_account(
    session: Session, user_id: uuid.UUID, account_id: str, institution: str, kind: str = "checking"
) -> None:
    """Register an account through the store first — exactly what `api.py`'s upload endpoint does before
    ever calling `ingest_csv`, since a posting can only reference an account that already exists.
    """
    store = load_store(session, user_id=user_id)
    account = Account(account_id=account_id, name=account_id, kind=kind, institution=institution, currency="USD")  # type: ignore[arg-type]
    save_store(store.model_copy(update={"accounts": {**store.accounts, account_id: account}}), session, user_id=user_id)


def test_ingest_csv_archives_the_raw_file_verbatim(tmp_path, db_session: Session, test_user_id: uuid.UUID) -> None:
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)

    archived = list(config.raw_statement_dir.glob("Chase/chase:checking:1234/*.csv"))
    assert len(archived) == 1
    assert archived[0].read_text() == CHASE_CHECKING_CSV


def test_ingest_csv_merges_into_the_ledger(tmp_path, db_session: Session, test_user_id: uuid.UUID) -> None:
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    result = ingest_csv(
        CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id
    )
    assert result.new_posting_count == 4
    assert result.total_posting_count == 4
    assert len(load_ledger(db_session, user_id=test_user_id)) == 4


def test_ingest_csv_reimporting_the_same_file_is_a_no_op(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)
    second = ingest_csv(
        CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id
    )
    assert second.new_posting_count == 0
    assert second.total_posting_count == 4


def test_ingest_csv_two_identical_same_day_purchases_both_survive(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    # Two real coffees, same account/day/amount/description — must not collapse into one.
    csv_text = (
        "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
        "DEBIT,06/29/2026,COFFEE SHOP,-5.00,DEBIT_CARD,2500.00,,\n"
        "DEBIT,06/29/2026,COFFEE SHOP,-5.00,DEBIT_CARD,2495.00,,\n"
    )
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    result = ingest_csv(csv_text, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)
    assert result.new_posting_count == 4  # 2 transactions x 2 postings, not 1x2
    ledger = load_ledger(db_session, user_id=test_user_id)
    assert ledger["transaction_id"].n_unique() == 2


def test_ingest_csv_reimporting_duplicate_purchases_stays_a_no_op(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    csv_text = (
        "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
        "DEBIT,06/29/2026,COFFEE SHOP,-5.00,DEBIT_CARD,2500.00,,\n"
        "DEBIT,06/29/2026,COFFEE SHOP,-5.00,DEBIT_CARD,2495.00,,\n"
    )
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    ingest_csv(csv_text, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)
    before = load_ledger(db_session, user_id=test_user_id).sort("posting_id")

    second = ingest_csv(csv_text, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)

    assert second.new_posting_count == 0
    assert second.total_posting_count == 4
    after = load_ledger(db_session, user_id=test_user_id).sort("posting_id")
    assert after["posting_id"].to_list() == before["posting_id"].to_list()


def test_ingest_csv_a_third_matching_purchase_adds_one_more_not_a_collision(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    two_coffees = (
        "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
        "DEBIT,06/29/2026,COFFEE SHOP,-5.00,DEBIT_CARD,2500.00,,\n"
        "DEBIT,06/29/2026,COFFEE SHOP,-5.00,DEBIT_CARD,2495.00,,\n"
    )
    three_coffees = two_coffees + "DEBIT,06/29/2026,COFFEE SHOP,-5.00,DEBIT_CARD,2490.00,,\n"

    ingest_csv(two_coffees, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)
    result = ingest_csv(
        three_coffees, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id
    )

    assert result.new_posting_count == 2  # only the third coffee is new
    ledger = load_ledger(db_session, user_id=test_user_id)
    assert ledger["transaction_id"].n_unique() == 3


def test_ingest_csv_two_different_accounts_both_land_in_the_ledger(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    _register_account(db_session, test_user_id, "sofi:savings:9999", "SoFi", kind="savings")
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)
    ingest_csv(SOFI_SAVINGS_CSV, "SoFi", "savings", "sofi:savings:9999", config, db_session, user_id=test_user_id)
    ledger = load_ledger(db_session, user_id=test_user_id)
    assert set(ledger["account_id"].unique().to_list()) >= {"chase:checking:1234", "sofi:savings:9999"}


def test_ingest_csv_unsupported_institution_raises(tmp_path, db_session: Session, test_user_id: uuid.UUID) -> None:
    with pytest.raises(UnsupportedImportError):
        ingest_csv(
            "a,b\n1,2\n",
            "BankOfAmerica",
            "checking",
            "boa:checking:0000",
            _config(tmp_path),
            db_session,
            user_id=test_user_id,
        )


def test_rebuild_from_raw_statements_reconstructs_the_same_ledger(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    _register_account(db_session, test_user_id, "sofi:savings:9999", "SoFi", kind="savings")
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)
    ingest_csv(SOFI_SAVINGS_CSV, "SoFi", "savings", "sofi:savings:9999", config, db_session, user_id=test_user_id)
    before = load_ledger(db_session, user_id=test_user_id).sort("posting_id")

    rebuilt = rebuild_from_raw_statements(config, db_session, user_id=test_user_id)
    after = load_ledger(db_session, user_id=test_user_id).sort("posting_id")

    assert rebuilt.sort("posting_id")["amount"].to_list() == pytest.approx(before["amount"].to_list())
    assert after["posting_id"].to_list() == before["posting_id"].to_list()


def test_rebuild_from_raw_statements_with_no_archives_raises(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    with pytest.raises(FileNotFoundError):
        rebuild_from_raw_statements(config, db_session, user_id=test_user_id)


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


def _archive_sofi_statement_pdf(config: AccountingConfig, pdf_bytes: bytes) -> None:
    """Write a raw PDF straight into the archive, bypassing the (now-retired) upload endpoint.

    Mirrors what `ingest_sofi_statement_pdf` used to do before new PDF
    imports were retired — `rebuild_from_raw_statements` still needs to
    find something under `SoFi/statement_pdf/*.pdf` to re-derive.
    """
    directory = config.raw_statement_dir / "SoFi" / "statement_pdf"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "statement.pdf").write_bytes(pdf_bytes)


def test_rebuild_from_raw_statements_replays_archived_sofi_pdfs(
    tmp_path, monkeypatch, db_session: Session, test_user_id: uuid.UUID
) -> None:
    _patch_sofi_statement_pdf(monkeypatch)
    config = _config(tmp_path)
    _archive_sofi_statement_pdf(config, b"%PDF-fake")

    rebuilt = rebuild_from_raw_statements(config, db_session, user_id=test_user_id)
    assert set(rebuilt["account_id"].unique().to_list()) >= {"sofi:checking:9169", "sofi:savings:3680"}
    assert "sofi:savings:3680:vault:emergency-fund" in load_store(db_session, user_id=test_user_id).accounts

    store = load_store(db_session, user_id=test_user_id)
    assert store.accounts["sofi:savings:3680"].meta["apy_pct"] == "4.02"
