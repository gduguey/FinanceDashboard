from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import polars as pl
import pytest

from accounting.config import AccountingConfig
from accounting.importers import ingest as ingest_module
from accounting.importers.canonical.csv import CanonicalImportResult
from accounting.importers.ingest import (
    UnsupportedImportError,
    ingest_canonical_csv,
    ingest_csv,
    load_ledger,
    rebuild_from_raw_statements,
)
from accounting.models import Account, Posting
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

# The newer, wide SoFi CSV shape (see `importers.sofi.csv`) — a vault export
# with one interest row and one transfer-from-savings row.
SOFI_VAULT_CSV = (
    "Authorized Date,Posted Date,Status,Account Name,Description,Primary Category,Detailed Category,Amount\n"
    "2026-06-30,2026-06-30,Posted,Emergency Fund ***3680,Interest,Income,Interest,77.97\n"
    "2026-04-03,2026-04-03,Posted,Emergency Fund ***3680,Transfer From Savings,Transfers,Savings transfers,10000.00\n"
)


def _config(tmp_path) -> AccountingConfig:
    return AccountingConfig(data_dir=tmp_path)


def _register_account(
    session: Session,
    user_id: uuid.UUID,
    account_id: str,
    institution: str,
    kind: str = "checking",
    parent_account_id: str | None = None,
) -> None:
    """Register an account through the store first — exactly what `api.py`'s upload endpoint does before
    ever calling `ingest_csv`, since a posting can only reference an account that already exists.
    """
    store = load_store(session, user_id=user_id)
    account = Account(
        account_id=account_id,
        name=account_id,
        kind=kind,  # type: ignore[arg-type]
        institution=institution,
        currency="USD",
        parent_account_id=parent_account_id,
    )
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


def test_load_ledger_with_since_excludes_postings_before_it(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    # CHASE_CHECKING_CSV has one transaction on 06/30 and one on 06/29 (2 postings each).
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)

    ledger = load_ledger(db_session, user_id=test_user_id, since=date(2026, 6, 30))

    assert len(ledger) == 2
    assert set(ledger["posted_at"].dt.date().to_list()) == {date(2026, 6, 30)}


def test_load_ledger_with_until_excludes_postings_after_it(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)

    ledger = load_ledger(db_session, user_id=test_user_id, until=date(2026, 6, 29))

    assert len(ledger) == 2
    assert set(ledger["posted_at"].dt.date().to_list()) == {date(2026, 6, 29)}


def test_load_ledger_since_and_until_are_both_inclusive(tmp_path, db_session: Session, test_user_id: uuid.UUID) -> None:
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)

    ledger = load_ledger(db_session, user_id=test_user_id, since=date(2026, 6, 29), until=date(2026, 6, 30))

    assert len(ledger) == 4


def test_load_ledger_with_no_range_still_returns_everything(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)

    assert len(load_ledger(db_session, user_id=test_user_id)) == 4


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


def test_ingest_csv_handles_a_vault_transfer_with_opaque_parent_id(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    # Deliberately opaque, non-colon-shaped ids — proves ingestion doesn't
    # depend on `account_id`'s own shape to find the parent (see
    # `ingest.py:802`'s old `.split(":")[1]`), even though the vault's own
    # transfer row no longer auto-resolves its counterparty at the parent
    # account (see `importers.sofi.csv`) — the parent's own export records
    # the same transfer independently, so auto-resolving both sides
    # double-booked it. Landing on the placeholder instead is a
    # `TransferRule`'s job to resolve now, same as every other transfer.
    config = _config(tmp_path)
    savings_id = "a1b2c3d4savings"
    vault_id = "e5f6a7b8vault"
    _register_account(db_session, test_user_id, savings_id, "SoFi", kind="savings")
    _register_account(db_session, test_user_id, vault_id, "SoFi", kind="vault", parent_account_id=savings_id)

    result = ingest_csv(
        SOFI_VAULT_CSV,
        "SoFi",
        "vault",
        vault_id,
        config,
        db_session,
        user_id=test_user_id,
        parent_account_id=savings_id,
    )
    assert result.new_posting_count == 4

    ledger = load_ledger(db_session, user_id=test_user_id)
    transfer_leg = ledger.filter(
        (ledger["account_id"] == vault_id) & (ledger["description"] == "Transfer From Savings")
    ).row(0, named=True)
    assert transfer_leg["amount"] == pytest.approx(10000.0)
    assert savings_id not in ledger["account_id"].unique().to_list()


def test_rebuild_from_raw_statements_handles_a_vault_transfer_with_opaque_ids(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    # Regression test for `ingest.py:802`'s `account_id.split(":")[1]`, which
    # raises `IndexError` the moment `account_id` isn't colon-shaped — must
    # fail on the pre-fix code.
    config = _config(tmp_path)
    savings_id = "a1b2c3d4savings"
    vault_id = "e5f6a7b8vault"
    _register_account(db_session, test_user_id, savings_id, "SoFi", kind="savings")
    _register_account(db_session, test_user_id, vault_id, "SoFi", kind="vault", parent_account_id=savings_id)
    ingest_csv(
        SOFI_VAULT_CSV,
        "SoFi",
        "vault",
        vault_id,
        config,
        db_session,
        user_id=test_user_id,
        parent_account_id=savings_id,
    )

    rebuilt = rebuild_from_raw_statements(config, db_session, user_id=test_user_id)

    transfer_leg = rebuilt.filter(
        (rebuilt["account_id"] == vault_id) & (rebuilt["description"] == "Transfer From Savings")
    ).row(0, named=True)
    assert transfer_leg["amount"] == pytest.approx(10000.0)
    assert savings_id not in rebuilt["account_id"].unique().to_list()


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


def _archive_sofi_statement_pdf(config: AccountingConfig, pdf_bytes: bytes) -> None:
    """Write a raw PDF straight into the archive, exactly where an old (pre-retirement) upload would have.

    `rebuild_from_raw_statements` no longer reads anything under
    `SoFi/statement_pdf/*.pdf` — this only exists to prove a rebuild
    ignores it rather than erroring on it or re-deriving postings from it.
    """
    directory = config.raw_statement_dir / "SoFi" / "statement_pdf"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "statement.pdf").write_bytes(pdf_bytes)


def test_rebuild_from_raw_statements_ignores_archived_pdfs(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)
    _archive_sofi_statement_pdf(config, b"%PDF-fake")

    rebuilt = rebuild_from_raw_statements(config, db_session, user_id=test_user_id)

    assert "chase:checking:1234" in set(rebuilt["account_id"].unique().to_list())
    assert not any(account_id.startswith("sofi:") for account_id in rebuilt["account_id"].unique().to_list())
    assert "sofi:savings:3680" not in load_store(db_session, user_id=test_user_id).accounts


def test_rebuild_from_raw_statements_with_only_archived_pdfs_raises(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    _archive_sofi_statement_pdf(config, b"%PDF-fake")

    with pytest.raises(FileNotFoundError):
        rebuild_from_raw_statements(config, db_session, user_id=test_user_id)


def _unbalanced_posting_frame(account_id: str) -> pl.DataFrame:
    """One posting with no counterparty leg — its transaction can never sum to zero."""
    posting = Posting(
        posting_id="unbalanced-p1",
        transaction_id="unbalanced-t1",
        account_id=account_id,
        posted_at=datetime(2026, 1, 1, tzinfo=UTC),
        amount=50.0,
        currency="USD",
        description="test",
        meta={},
    )
    return pl.DataFrame([posting.model_dump(mode="python")], schema=Posting.polars_schema)


def test_ingest_csv_rejects_a_standardizer_result_that_doesnt_balance(
    tmp_path, monkeypatch, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    monkeypatch.setitem(
        ingest_module._STANDARDIZERS,
        ("Chase", "checking"),
        lambda *_args: _unbalanced_posting_frame("chase:checking:1234"),
    )

    with pytest.raises(ValueError, match="not zero"):
        ingest_csv(
            CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id
        )


def test_ingest_canonical_csv_rejects_a_result_that_doesnt_balance(
    tmp_path, monkeypatch, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    monkeypatch.setattr(
        ingest_module,
        "standardize_canonical_csv",
        lambda *_args, **_kwargs: CanonicalImportResult(
            postings=_unbalanced_posting_frame("chase:checking:1234"), new_categories={}
        ),
    )

    with pytest.raises(ValueError, match="not zero"):
        ingest_canonical_csv("a,b\n1,2\n", "chase:checking:1234", config, db_session, user_id=test_user_id)


def test_rebuild_from_raw_statements_rejects_a_result_that_doesnt_balance(
    tmp_path, monkeypatch, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    _register_account(db_session, test_user_id, "chase:checking:1234", "Chase")
    ingest_csv(CHASE_CHECKING_CSV, "Chase", "checking", "chase:checking:1234", config, db_session, user_id=test_user_id)
    monkeypatch.setitem(
        ingest_module._STANDARDIZERS,
        ("Chase", "checking"),
        lambda *_args: _unbalanced_posting_frame("chase:checking:1234"),
    )

    with pytest.raises(ValueError, match="not zero"):
        rebuild_from_raw_statements(config, db_session, user_id=test_user_id)
