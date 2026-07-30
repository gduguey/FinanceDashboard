import io
import operator
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest
import xlsxwriter
from fastapi.testclient import TestClient

import db.models as dbm
import trades.db as tdb
from accounting import api as accounting_api
from accounting.api.routers import postings as postings_router
from accounting.api.routers import imports as accounting_imports_router
from accounting.api.routers import llm as accounting_llm_router
from accounting.config import AccountingConfig
from accounting.db.llm import LLMUsage
from accounting.importers import ingest as ingest_module
from accounting.ledger.frame import LEDGER_FRAME_SCHEMA
from accounting.market_data import exchange_rates
from accounting.models import Posting
from accounting.market_data.exchange_rates import RATE_HISTORY_SCHEMA
from accounting.repositories.accounts import load_manual_transfers
from accounting.repositories.interpretation import load_posting_merges
from db.current_user import get_current_user_id
from db.session import get_db
from tests.conftest import DEFAULT_USER_ID
from trades import api as trades_api
from trades.config import AppConfig

CHASE_CHECKING_CSV = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    "CREDIT,06/30/2026,SOME EMPLOYER PAYROLL PPD ID: 1234567890,1500.00,ACH_CREDIT,4000.00,,\n"
    "DEBIT,06/29/2026,Payment to Chase card ending in 1234 06/29,-70.00,LOAN_PMT,2500.00,,\n"
)


@pytest.fixture(autouse=True)
def isolated_accounting_config(tmp_path, monkeypatch):
    monkeypatch.setattr(accounting_api.state, "config", AccountingConfig(data_dir=tmp_path))


@pytest.fixture(autouse=True)
def _db_for_api(db_session):
    """Route every request the `TestClient` makes through this test's own rolled-back session.

    `tests/conftest.py`'s `_bypass_clerk_auth_by_default` overrides
    `get_current_user_id` to `DEFAULT_USER_ID` for every test by default, so
    the one `User` row FK-satisfying every table has to exist under that
    exact id, not a random `test_user_id` (that fixture is for tests that
    call store/ledger functions directly with an explicit `user_id`). A test
    that needs a second, genuinely distinct user overrides
    `get_current_user_id` again locally — see
    `test_accounts_are_isolated_between_users`.
    """
    db_session.add(dbm.User(id=DEFAULT_USER_ID, email="default@example.com"))
    db_session.commit()

    def _override_get_db():
        yield db_session

    trades_api.app.dependency_overrides[get_db] = _override_get_db
    yield
    trades_api.app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client():
    return TestClient(trades_api.app)


@pytest.fixture
def broker_connection_id(db_session) -> uuid.UUID:
    """A real `trades.broker_connections` row for an account to link its value to.

    `accounts.broker_connection_id` is a genuine foreign key across the
    ledger seam now (DB-audit move #1), so "this account mirrors the
    tracked portfolio" can only be said about a connection that exists.
    A sync creates it in the real app; this creates it directly.
    """
    connection_id = uuid.uuid4()
    db_session.add(tdb.BrokerConnection(id=connection_id, user_id=DEFAULT_USER_ID, natural_key="ibkr", broker="ibkr"))
    db_session.commit()
    return connection_id


def _postings(client, **params) -> list[dict]:
    """The `items` of one page of `GET /postings` — see `api.api_models.PostingPage`.

    `limit` counts transactions and defaults to `PAGE_LIMIT_DEFAULT`, which
    every seed here stays well under, so an unparameterized call returns the
    whole ledger. Pass `limit=` explicitly for a test that seeds more.
    """
    response = client.get("/api/v1/accounting/postings", params=params)
    assert response.status_code == 200
    return response.json()["items"]


def _ledger_export(client, **params) -> list[dict]:
    """The `items` of one page of `GET /ledger/export` — see `api.api_models.LedgerExportPage`.

    Raw postings, so these rows carry none of `PostingRow`'s resolution
    fields; `limit` here counts postings, not transactions.
    """
    response = client.get("/api/v1/accounting/ledger/export", params=params)
    assert response.status_code == 200
    return response.json()["items"]


def test_get_store_seeds_default_categories_and_placeholder_accounts(client) -> None:
    body = client.get("/api/v1/accounting/store").json()
    assert "expense:food-drink" in body["categories"]
    assert "uncategorized:expense" in body["accounts"]
    assert body["transfer_rules"] == []


def test_detect_returns_a_guess_for_a_known_shape(client) -> None:
    body = client.post(
        "/api/v1/accounting/detect",
        json={
            "header": ["Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #"],
            "filename": "Chase9579_Activity_20260704.CSV",
        },
    ).json()
    assert body["institution"] == "Chase"
    assert body["account_kind"] == "checking"


def test_detect_returns_none_for_an_unknown_shape(client) -> None:
    body = client.post("/api/v1/accounting/detect", json={"header": ["A", "B"], "filename": "x.csv"}).json()
    assert body is None


def test_import_against_an_unknown_account_id_is_a_422(client) -> None:
    response = client.post(
        "/api/v1/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "does-not-exist",
            "account_name": "Chase Checking",
        },
    )
    assert response.status_code == 422


def test_import_ingests_postings_against_an_existing_account(client) -> None:
    account = _create_account(client, name="Chase Checking", kind="checking", institution="Chase")
    response = client.post(
        "/api/v1/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Chase Checking",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == account["account_id"]
    assert body["new_posting_count"] == 4

    store = client.get("/api/v1/accounting/store").json()
    assert account["account_id"] in store["accounts"]


def test_import_unsupported_institution_is_a_400(client) -> None:
    account = _create_account(client, name="BoA Checking", kind="checking", institution="BankOfAmerica")
    response = client.post(
        "/api/v1/accounting/import",
        files={"file": ("x.csv", "a,b\n1,2\n", "text/csv")},
        data={
            "institution": "BankOfAmerica",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "BoA Checking",
        },
    )
    assert response.status_code == 400


def test_canonical_import_against_an_unknown_account_id_is_a_422(client) -> None:
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Grocery Store,-42.50,Groceries\n"
    response = client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": "does-not-exist",
            "account_name": "Generic Checking",
        },
    )
    assert response.status_code == 422


def test_canonical_import_rejects_a_non_importable_account_kind(client) -> None:
    account = _create_account(client, name="Car Loan", kind="loan", institution="Generic Bank")
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Payment,-42.50,\n"
    response = client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "loan",
            "account_id": account["account_id"],
            "account_name": "Car Loan",
        },
    )
    assert response.status_code == 400


def test_canonical_import_registers_a_new_account_and_creates_a_category(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = (
        "Date,Description,Amount,Category\n2026-06-30,Grocery Store,-42.50,Groceries\n2026-06-29,Paycheck,1500.00,\n"
    )
    response = client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == account["account_id"]
    assert body["new_posting_count"] == 4
    assert [category["name"] for category in body["new_categories"]] == ["Groceries"]

    store = client.get("/api/v1/accounting/store").json()
    assert account["account_id"] in store["accounts"]
    assert any(category["name"] == "Groceries" for category in store["categories"].values())


def test_canonical_import_handles_a_utf8_bom_prefixed_file(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = "﻿Date,Description,Amount\n2026-06-30,Grocery Store,-42.50\n"
    response = client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text.encode("utf-8"), "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    assert response.status_code == 200
    assert response.json()["new_posting_count"] == 2


def test_canonical_import_accepts_an_xlsx_file(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
    worksheet = workbook.add_worksheet("Transactions")
    for row_index, row in enumerate([["Date", "Description", "Amount"], ["2026-06-30", "Grocery Store", "-42.50"]]):
        for col_index, value in enumerate(row):
            worksheet.write(row_index, col_index, value)
    workbook.close()

    response = client.post(
        "/api/v1/accounting/import/canonical",
        files={
            "file": (
                "generic.xlsx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    assert response.status_code == 200
    assert response.json()["new_posting_count"] == 2


def test_canonical_import_date_order_dmy_reads_day_first(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = "Date,Description,Amount\n01/12/2026,Grocery Store,-42.50\n"
    response = client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
            "date_order": "DMY",
        },
    )
    assert response.status_code == 200
    postings = _postings(client)
    real_leg = next(p for p in postings if p["account_id"] == account["account_id"])
    assert real_leg["posted_at"].startswith("2026-12-01")


def test_canonical_import_preview_does_not_persist_anything(client) -> None:
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Grocery Store,-42.50,Groceries\n"
    response = client.post(
        "/api/v1/accounting/import/canonical/preview",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={"account_id": "generic-bank:checking:0005"},
    )
    assert response.status_code == 200
    assert [category["name"] for category in response.json()["new_categories"]] == ["Groceries"]

    store = client.get("/api/v1/accounting/store").json()
    assert "generic-bank:checking:0005" not in store["accounts"]
    # A subcategory named "Groceries" is seeded by default on every fresh
    # install (see `store._EXPENSE_TAXONOMY`) — what must NOT exist is a
    # *top-level* one, which is what this preview would have created.
    assert not any(
        category["name"] == "Groceries" and category["parent_category_id"] is None
        for category in store["categories"].values()
    )


def test_canonical_import_applies_category_overrides_to_merge_two_categories(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = (
        "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,Groceries\n2026-06-29,Restaurant,-20.00,Dining\n"
    )
    response = client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
            "category_overrides": '{"categories": {"Groceries": "Food", "Dining": "Food"}}',
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert [category["name"] for category in body["new_categories"]] == ["Food"]

    store = client.get("/api/v1/accounting/store").json()
    food_categories = [category for category in store["categories"].values() if category["name"] == "Food"]
    assert len(food_categories) == 1


def test_canonical_import_returns_a_422_for_an_unparseable_file(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    response = client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("bad.csv", "Foo,Bar\n1,2\n", "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    assert response.status_code == 422
    assert "Date" in response.json()["detail"]


def _create_account(client, **overrides) -> dict:
    payload = {"name": "Test Account", "kind": "checking", "institution": "Chase", "currency": "USD", **overrides}
    response = client.post("/api/v1/accounting/accounts", json=payload)
    assert response.status_code == 201
    return response.json()


def _import_chase_checking(client, account_id: str | None = None, csv_text: str = CHASE_CHECKING_CSV) -> str:
    if account_id is None:
        account_id = _create_account(
            client, name="Chase Checking", kind="checking", institution="Chase", last_four="9579"
        )["account_id"]
    response = client.post(
        "/api/v1/accounting/import",
        files={"file": ("Chase9579.csv", csv_text, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": account_id,
            "account_name": "Chase Checking",
        },
    )
    assert response.status_code == 200
    return account_id


def _import_chase_credit_card(client, csv_text: str) -> str:
    account_id = _create_account(client, name="Chase Credit Card", kind="credit_card", institution="Chase")[
        "account_id"
    ]
    response = client.post(
        "/api/v1/accounting/import",
        files={"file": ("Chase1234.csv", csv_text, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "credit_card",
            "account_id": account_id,
            "account_name": "Chase Credit Card",
        },
    )
    assert response.status_code == 200
    return account_id


def test_categorize_from_file_preview_matches_an_existing_posting_and_proposes_its_category(client) -> None:
    _import_chase_checking(client)
    # Same real-world transaction as CHASE_CHECKING_CSV's payroll row, hand-categorized in a personal sheet.
    sheet_csv = "Date,Description,Amount,Category\n06/30/2026,Payroll,1500.00,Salary\n"
    response = client.post(
        "/api/v1/accounting/import/categorize-from-file/preview",
        files={"file": ("my-sheet.csv", sheet_csv, "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["matches"]) == 1
    match = body["matches"][0]
    assert match["posting_id"] is not None
    assert match["proposed_category_name"] == "Salary"
    assert match["confidence"] > 0


def test_categorize_from_file_preview_does_not_persist_anything(client) -> None:
    account_id = _import_chase_checking(client)
    # "Freelance Gig Income" isn't one of the default-seeded category names (unlike "Salary"),
    # so its absence afterward actually proves the preview created nothing.
    sheet_csv = "Date,Description,Amount,Category\n06/30/2026,Payroll,1500.00,Freelance Gig Income\n"
    client.post(
        "/api/v1/accounting/import/categorize-from-file/preview",
        files={"file": ("my-sheet.csv", sheet_csv, "text/csv")},
    )
    postings = _postings(client)
    real_leg = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    assert real_leg["category_id"] is None

    store = client.get("/api/v1/accounting/store").json()
    assert not any(category["name"] == "Freelance Gig Income" for category in store["categories"].values())


def test_categorize_from_file_preview_reports_an_unmatched_row(client) -> None:
    _import_chase_checking(client)
    sheet_csv = "Date,Description,Amount,Category\n01/15/2026,Some Unrelated Purchase,-999.99,Shopping\n"
    response = client.post(
        "/api/v1/accounting/import/categorize-from-file/preview",
        files={"file": ("my-sheet.csv", sheet_csv, "text/csv")},
    )
    assert response.status_code == 200
    match = response.json()["matches"][0]
    assert match["posting_id"] is None
    assert match["confidence"] is None


def test_categorize_from_file_apply_sets_the_category_on_the_matched_posting(client) -> None:
    account_id = _import_chase_checking(client)
    sheet_csv = "Date,Description,Amount,Category\n06/30/2026,Payroll,1500.00,Salary\n"
    preview = client.post(
        "/api/v1/accounting/import/categorize-from-file/preview",
        files={"file": ("my-sheet.csv", sheet_csv, "text/csv")},
    ).json()
    row_number = preview["matches"][0]["row_number"]

    response = client.post(
        "/api/v1/accounting/import/categorize-from-file/apply",
        files={"file": ("my-sheet.csv", sheet_csv, "text/csv")},
        data={"confirmed_row_numbers": f"[{row_number}]"},
    )
    assert response.status_code == 200
    assert response.json()["updated_posting_count"] == 1

    postings = _postings(client)
    real_leg = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    assert real_leg["category_id"] is not None
    store = client.get("/api/v1/accounting/store").json()
    assert store["categories"][real_leg["category_id"]]["name"] == "Salary"

    # Never creates a new transaction — same two postings as right after the original import.
    assert len(postings) == 4


def test_categorize_from_file_apply_rejects_a_malformed_confirmed_row_numbers_without_writing(client) -> None:
    """A rejected request must not leave the file's new categories behind.

    `confirmed_row_numbers` used to be parsed *after* the commit that
    persists the categories the uploaded file introduced, so a malformed
    value committed those categories and then raised `json.JSONDecodeError`
    into a 500 — a write the caller was told had failed. Parsed up front now,
    so the 422 happens before anything is written.
    """
    _import_chase_checking(client)
    before = set(client.get("/api/v1/accounting/store").json()["categories"])
    sheet_csv = "Date,Description,Amount,Category\n06/30/2026,Payroll,1500.00,A Brand New Category\n"

    response = client.post(
        "/api/v1/accounting/import/categorize-from-file/apply",
        files={"file": ("my-sheet.csv", sheet_csv, "text/csv")},
        data={"confirmed_row_numbers": "not json at all"},
    )

    assert response.status_code == 422
    after = set(client.get("/api/v1/accounting/store").json()["categories"])
    assert after == before, "a rejected apply committed the file's new categories anyway"


def test_categorize_from_file_apply_skips_rows_not_confirmed(client) -> None:
    account_id = _import_chase_checking(client)
    sheet_csv = (
        "Date,Description,Amount,Category\n"
        "06/30/2026,Payroll,1500.00,Salary\n"
        "06/29/2026,Payment to Chase card ending in 1234 06/29,-70.00,Credit Card Payment\n"
    )
    client.post(
        "/api/v1/accounting/import/categorize-from-file/apply",
        files={"file": ("my-sheet.csv", sheet_csv, "text/csv")},
        data={"confirmed_row_numbers": "[2]"},  # only the payroll row (header is row 1, so first data row is row 2)
    )
    postings = _postings(client)
    payroll_leg = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    payment_leg = next(p for p in postings if p["account_id"] == account_id and p["amount"] < 0)
    assert payroll_leg["category_id"] is not None
    assert payment_leg["category_id"] is None


def test_categorize_from_file_apply_never_matches_the_same_posting_twice(client) -> None:
    # Two real, distinct $5 coffees on the same day — matching must not collapse them onto one posting.
    account_id = _import_chase_checking(client)
    two_coffees_csv = (
        "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
        "DEBIT,06/28/2026,COFFEE SHOP,-5.00,DEBIT_CARD,2500.00,,\n"
        "DEBIT,06/28/2026,COFFEE SHOP,-5.00,DEBIT_CARD,2495.00,,\n"
    )
    _import_chase_checking(client, account_id=account_id, csv_text=two_coffees_csv)
    sheet_csv = (
        "Date,Description,Amount,Category\n"
        "06/28/2026,Coffee Shop,-5.00,Dining Out\n"
        "06/28/2026,Coffee Shop,-5.00,Dining Out\n"
    )
    response = client.post(
        "/api/v1/accounting/import/categorize-from-file/preview",
        files={"file": ("my-sheet.csv", sheet_csv, "text/csv")},
    )
    matches = response.json()["matches"]
    coffee_matches = [m for m in matches if m["amount"] == pytest.approx(-5.0)]
    assert len(coffee_matches) == 2
    matched_posting_ids = {m["posting_id"] for m in coffee_matches}
    assert None not in matched_posting_ids
    assert len(matched_posting_ids) == 2  # each row claimed a different posting, not the same one twice


def test_postings_leaves_category_none_when_no_seed_rule_matches(client) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    assert payroll["category_id"] is None  # generic payroll text doesn't match the EQORE-specific seed rule


def test_manual_override_wins_over_no_rule_match(client) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    response = client.put(
        f"/api/v1/accounting/postings/{payroll['posting_id']}/override", json={"category_id": "income:salary"}
    )
    assert response.status_code == 200

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"


def test_setting_a_subcategory_after_a_category_preserves_the_category(client) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    posting_id = payroll["posting_id"]

    client.put(f"/api/v1/accounting/postings/{posting_id}/override", json={"category_id": "income:reimbursement"})
    response = client.put(
        f"/api/v1/accounting/postings/{posting_id}/override",
        json={"subcategory_id": "income:reimbursement:employer"},
    )
    assert response.status_code == 200
    assert response.json()["category_id"] == "income:reimbursement"
    assert response.json()["subcategory_id"] == "income:reimbursement:employer"

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["posting_id"] == posting_id)
    assert updated_payroll["category_id"] == "income:reimbursement"
    assert updated_payroll["subcategory_id"] == "income:reimbursement:employer"


def test_put_posting_split_replaces_one_posting_with_categorized_legs(client) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    response = client.put(
        f"/api/v1/accounting/postings/{payroll['posting_id']}/split",
        json=[
            {"amount": 1400.0, "category_id": "income:salary", "description": "Wage"},
            {"amount": 100.0, "category_id": "income:reimbursement", "description": "Expense reimbursement"},
        ],
    )
    assert response.status_code == 200

    updated = _postings(client)
    assert not any(p["posting_id"] == payroll["posting_id"] for p in updated)
    legs = [p for p in updated if p["posting_id"].startswith(f"{payroll['posting_id']}:split:")]
    assert sorted(leg["amount"] for leg in legs) == pytest.approx([100.0, 1400.0])
    assert {leg["category_id"] for leg in legs} == {"income:salary", "income:reimbursement"}


def test_put_posting_split_rejects_legs_that_dont_sum_correctly(client) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    response = client.put(
        f"/api/v1/accounting/postings/{payroll['posting_id']}/split",
        json=[{"amount": 100.0}, {"amount": 100.0}],
    )
    assert response.status_code == 400


def test_delete_posting_split_restores_the_original_posting(client) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    client.put(
        f"/api/v1/accounting/postings/{payroll['posting_id']}/split",
        json=[{"amount": 1000.0}, {"amount": 500.0}],
    )
    client.delete(f"/api/v1/accounting/postings/{payroll['posting_id']}/split")

    updated = _postings(client)
    assert any(p["posting_id"] == payroll["posting_id"] for p in updated)


def test_import_paystub_reconciles_against_a_matching_bank_posting(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    paystub_text = (
        "Pay Date: 06/30/2026\nGross Pay: $2,000.00\nTotal Taxes: $500.00\nNet Pay: $1,500.00\n"
        "Direct Deposit\nChecking ending in 9579 $1,500.00\n"
    )
    monkeypatch.setattr(accounting_imports_router, "extract_paystub_pdf_text", lambda _pdf_bytes: paystub_text)

    response = client.post(
        "/api/v1/accounting/import/paystub", files={"file": ("paystub.pdf", b"%PDF-fake", "application/pdf")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["statement"]["gross_pay"] == pytest.approx(2000.0)

    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    assert body["is_fully_matched"]
    assert body["matches"][0]["posting_id"] == payroll["posting_id"]

    assert len(body["proposed_splits"]) == 1
    proposal = body["proposed_splits"][0]
    assert proposal["posting_id"] == payroll["posting_id"]
    assert proposal["legs"] == [
        {
            "amount": pytest.approx(1500.0),
            "category_id": "income:salary",
            "subcategory_id": None,
            "description": "Salary",
        }
    ]


def test_import_paystub_400s_on_unrecognized_text(client, monkeypatch) -> None:
    monkeypatch.setattr(
        accounting_imports_router, "extract_paystub_pdf_text", lambda _pdf_bytes: "not a paystub at all"
    )
    response = client.post(
        "/api/v1/accounting/import/paystub", files={"file": ("paystub.pdf", b"%PDF-fake", "application/pdf")}
    )
    assert response.status_code == 400


class _FakeLLMProvider:
    def __init__(self, response: str) -> None:
        self._response = response

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return self._response


def test_ai_suggest_category_applies_a_valid_suggestion(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    fake_response = '{"category_id": "income:salary", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )

    response = client.post(f"/api/v1/accounting/postings/{payroll['posting_id']}/ai-suggest-category")
    assert response.status_code == 200
    body = response.json()
    assert body == {"category_id": "income:salary", "subcategory_id": None, "applied": True}

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"


def test_ai_suggest_category_does_not_apply_a_hallucinated_category(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    fake_response = '{"category_id": "not-a-real-category", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )

    response = client.post(f"/api/v1/accounting/postings/{payroll['posting_id']}/ai-suggest-category")
    assert response.status_code == 200
    assert response.json() == {"category_id": None, "subcategory_id": None, "applied": False}

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] is None


def test_ai_suggest_category_with_lock_category_id_only_fills_the_subcategory(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    client.put(
        f"/api/v1/accounting/postings/{payroll['posting_id']}/override", json={"category_id": "income:reimbursement"}
    )

    fake_response = '{"category_id": "income:reimbursement", "subcategory_id": "income:reimbursement:employer"}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )

    response = client.post(
        f"/api/v1/accounting/postings/{payroll['posting_id']}/ai-suggest-category",
        params={"lock_category_id": "income:reimbursement"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "category_id": "income:reimbursement",
        "subcategory_id": "income:reimbursement:employer",
        "applied": True,
    }


def test_ai_suggest_category_with_lock_category_id_discards_a_disagreeing_guess(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    client.put(f"/api/v1/accounting/postings/{payroll['posting_id']}/override", json={"category_id": "income:salary"})

    fake_response = '{"category_id": "income:reimbursement", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )

    response = client.post(
        f"/api/v1/accounting/postings/{payroll['posting_id']}/ai-suggest-category",
        params={"lock_category_id": "income:salary"},
    )
    assert response.status_code == 200
    assert response.json() == {"category_id": None, "subcategory_id": None, "applied": False}

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"  # untouched, never overwritten


def test_ai_suggest_category_503s_when_no_provider_is_configured(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    monkeypatch.setattr(accounting_llm_router, "_llm_providers", lambda session, user_id: [])
    response = client.post(f"/api/v1/accounting/postings/{payroll['posting_id']}/ai-suggest-category")
    assert response.status_code == 503


def test_ai_suggest_category_404s_for_an_unknown_posting(client, monkeypatch) -> None:
    monkeypatch.setattr(accounting_llm_router, "_llm_providers", lambda session, user_id: [])
    response = client.post("/api/v1/accounting/postings/does-not-exist/ai-suggest-category")
    assert response.status_code == 404


def test_ai_suggest_category_marks_the_posting_pending_until_validated(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    fake_response = '{"category_id": "income:salary", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )
    client.post(f"/api/v1/accounting/postings/{payroll['posting_id']}/ai-suggest-category")

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"
    assert updated_payroll["pending_source"] == "ai"
    assert updated_payroll["pending_selected"] is True


def test_validate_pending_accepts_a_selected_suggestion(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    fake_response = '{"category_id": "income:salary", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )
    client.post(f"/api/v1/accounting/postings/{payroll['posting_id']}/ai-suggest-category")

    response = client.post(
        "/api/v1/accounting/postings/validate-pending", json={"posting_ids": [payroll["posting_id"]]}
    )
    assert response.status_code == 200
    assert response.json() == {"accepted": 1, "reverted": 0}

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"
    assert updated_payroll["pending_source"] is None


def test_validate_pending_reverts_an_unselected_suggestion(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    assert payroll["category_id"] is None

    fake_response = '{"category_id": "income:salary", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )
    client.post(f"/api/v1/accounting/postings/{payroll['posting_id']}/ai-suggest-category")
    client.put(f"/api/v1/accounting/postings/{payroll['posting_id']}/override", json={"pending_selected": False})

    response = client.post(
        "/api/v1/accounting/postings/validate-pending", json={"posting_ids": [payroll["posting_id"]]}
    )
    assert response.status_code == 200
    assert response.json() == {"accepted": 0, "reverted": 1}

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] is None
    assert updated_payroll["pending_source"] is None


def test_validate_pending_ignores_postings_outside_the_given_list(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    fake_response = '{"category_id": "income:salary", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )
    client.post(f"/api/v1/accounting/postings/{payroll['posting_id']}/ai-suggest-category")

    response = client.post("/api/v1/accounting/postings/validate-pending", json={"posting_ids": ["some-other-posting"]})
    assert response.json() == {"accepted": 0, "reverted": 0}

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["pending_source"] == "ai"


def test_pattern_suggest_category_stages_a_pending_suggestion(client) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    client.post(
        "/api/v1/accounting/category-patterns",
        json={"description_contains": "PAYROLL", "category_id": "income:salary"},
    )

    response = client.post(f"/api/v1/accounting/postings/{payroll['posting_id']}/pattern-suggest-category")
    assert response.status_code == 200
    assert response.json() == {"category_id": "income:salary", "subcategory_id": None, "applied": True}

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"
    assert updated_payroll["pending_source"] == "pattern"


def test_pattern_suggest_category_returns_unapplied_when_nothing_matches(client) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    response = client.post(f"/api/v1/accounting/postings/{payroll['posting_id']}/pattern-suggest-category")
    assert response.json() == {"category_id": None, "subcategory_id": None, "applied": False}


def test_pattern_suggest_category_bulk_stages_suggestions_for_many_postings_in_one_call(client) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    card_payment = next(p for p in postings if p["account_id"] == account_id and p["amount"] < 0)

    for pattern in (
        {"description_contains": "PAYROLL", "category_id": "income:salary"},
        {"description_contains": "Chase card", "category_id": "expense:admin-fees"},
    ):
        client.post("/api/v1/accounting/category-patterns", json=pattern)

    response = client.post(
        "/api/v1/accounting/postings/pattern-suggest-category/bulk",
        json={"posting_ids": [payroll["posting_id"], card_payment["posting_id"]]},
    )
    assert response.status_code == 200
    assert response.json() == {"applied": 2}

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    updated_card = next(p for p in updated if p["posting_id"] == card_payment["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"
    assert updated_payroll["pending_source"] == "pattern"
    assert updated_card["category_id"] == "expense:admin-fees"
    assert updated_card["pending_source"] == "pattern"


def test_pattern_suggest_category_bulk_skips_postings_whose_existing_category_disagrees(client) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    client.put(
        f"/api/v1/accounting/postings/{payroll['posting_id']}/override",
        json={"category_id": "income:bonus"},
    )

    client.post(
        "/api/v1/accounting/category-patterns",
        json={"description_contains": "PAYROLL", "category_id": "income:salary"},
    )

    response = client.post(
        "/api/v1/accounting/postings/pattern-suggest-category/bulk", json={"posting_ids": [payroll["posting_id"]]}
    )
    assert response.json() == {"applied": 0}

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:bonus"
    assert updated_payroll["pending_source"] is None


def test_post_category_pattern_mints_a_content_derived_id(client) -> None:
    response = client.post(
        "/api/v1/accounting/category-patterns",
        json={"description_contains": "NETFLIX", "category_id": "expense:subscriptions"},
    )
    assert response.status_code == 201
    pattern = response.json()
    assert pattern["pattern_id"]
    assert pattern["description_contains"] == "NETFLIX"
    assert pattern["priority"] == 100
    assert pattern["active"] is True
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json() == pattern


def test_post_category_pattern_twice_with_the_same_criteria_replaces_rather_than_duplicates(client) -> None:
    first = client.post(
        "/api/v1/accounting/category-patterns",
        json={"description_contains": "NETFLIX", "category_id": "expense:subscriptions"},
    ).json()
    second = client.post(
        "/api/v1/accounting/category-patterns",
        json={"description_contains": "NETFLIX", "category_id": "expense:subscriptions", "priority": 5},
    ).json()

    assert second["pattern_id"] == first["pattern_id"]
    patterns = client.get("/api/v1/accounting/store").json()["category_patterns"]
    assert list(patterns.keys()) == [first["pattern_id"]]
    assert patterns[first["pattern_id"]]["priority"] == 5


def test_re_posting_a_category_pattern_keeps_the_state_a_create_body_cannot_express(client) -> None:
    """A create body carries no `active` and no `version`, so a replace must not reset either.

    Resetting `active` would silently re-enable a pattern the user had switched
    off, and answering the 200 with `version: 1` would hand the client a
    version `_upsert_rule` never wrote, so its next `PATCH` would 409.
    """
    created = client.post(
        "/api/v1/accounting/category-patterns",
        json={"description_contains": "NETFLIX", "category_id": "expense:subscriptions"},
    ).json()
    disabled = client.patch(
        f"/api/v1/accounting/category-patterns/{created['pattern_id']}",
        json={
            "description_contains": "NETFLIX",
            "category_id": "expense:subscriptions",
            "priority": 100,
            "active": False,
            "expected_version": 1,
        },
    ).json()
    assert disabled["active"] is False
    assert disabled["version"] == 2

    replaced = client.post(
        "/api/v1/accounting/category-patterns",
        json={"description_contains": "NETFLIX", "category_id": "expense:subscriptions", "priority": 5},
    )
    assert replaced.status_code == 200
    assert replaced.json()["priority"] == 5
    assert replaced.json()["active"] is False
    assert replaced.json()["version"] == 2

    persisted = client.get("/api/v1/accounting/store").json()["category_patterns"][created["pattern_id"]]
    assert persisted["active"] is False
    assert persisted["version"] == 2


def test_re_posting_a_transfer_rule_reports_the_version_a_later_patch_must_send(client) -> None:
    """The 200 on a replace used to say `version: 1` however many times the rule had been patched."""
    payee = _create_account(client, name="Venmo", kind="expense_payee", institution="internal")
    created = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "VENMO", "counterparty_account_id": payee["account_id"]},
    ).json()
    patched = client.patch(
        f"/api/v1/accounting/transfer-rules/{created['rule_id']}",
        json={
            "description_contains": "VENMO",
            "counterparty_account_id": payee["account_id"],
            "priority": 50,
            "active": True,
            "excluded_transaction_ids": [],
            "expected_version": 1,
        },
    ).json()
    assert patched["version"] == 2

    replaced = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "VENMO", "counterparty_account_id": payee["account_id"], "priority": 7},
    )
    assert replaced.status_code == 200
    assert replaced.json()["version"] == 2

    accepted = client.patch(
        f"/api/v1/accounting/transfer-rules/{created['rule_id']}",
        json={
            "description_contains": "VENMO",
            "counterparty_account_id": payee["account_id"],
            "priority": 9,
            "active": True,
            "excluded_transaction_ids": [],
            "expected_version": replaced.json()["version"],
        },
    )
    assert accepted.status_code == 200


def test_patch_category_pattern_updates_fields_and_increments_version(client) -> None:
    pattern = client.post(
        "/api/v1/accounting/category-patterns",
        json={"description_contains": "NETFLIX", "category_id": "expense:subscriptions"},
    ).json()
    assert pattern["version"] == 1

    response = client.patch(
        f"/api/v1/accounting/category-patterns/{pattern['pattern_id']}",
        json={
            "description_contains": "NETFLIX",
            "category_id": "expense:subscriptions",
            "priority": 3,
            "active": False,
            "expected_version": 1,
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["priority"] == 3
    assert updated["active"] is False
    assert updated["version"] == 2

    persisted = client.get("/api/v1/accounting/store").json()["category_patterns"][pattern["pattern_id"]]
    assert persisted["version"] == 2


def test_patch_category_pattern_with_a_stale_expected_version_gets_409(client) -> None:
    pattern = client.post(
        "/api/v1/accounting/category-patterns",
        json={"description_contains": "NETFLIX", "category_id": "expense:subscriptions"},
    ).json()
    response = client.patch(
        f"/api/v1/accounting/category-patterns/{pattern['pattern_id']}",
        json={
            "description_contains": "NETFLIX",
            "category_id": "expense:subscriptions",
            "priority": 3,
            "expected_version": 2,
        },
    )
    assert response.status_code == 409


def test_patch_category_pattern_that_does_not_exist_gets_404(client) -> None:
    response = client.patch(
        "/api/v1/accounting/category-patterns/does-not-exist",
        json={
            "description_contains": "X",
            "category_id": "expense:subscriptions",
            "priority": 0,
            "expected_version": 1,
        },
    )
    assert response.status_code == 404


def test_delete_category_pattern_removes_it(client) -> None:
    pattern = client.post(
        "/api/v1/accounting/category-patterns",
        json={"description_contains": "NETFLIX", "category_id": "expense:subscriptions"},
    ).json()
    response = client.delete(f"/api/v1/accounting/category-patterns/{pattern['pattern_id']}")
    assert response.status_code == 204
    assert client.get("/api/v1/accounting/store").json()["category_patterns"] == {}


def test_delete_category_pattern_that_is_already_gone_gets_404(client) -> None:
    response = client.delete("/api/v1/accounting/category-patterns/does-not-exist")
    assert response.status_code == 404


def test_creating_an_unrelated_pattern_does_not_reset_another_patterns_version(client) -> None:
    pattern_a = client.post(
        "/api/v1/accounting/category-patterns",
        json={"description_contains": "NETFLIX", "category_id": "expense:subscriptions"},
    ).json()
    pattern_a = client.patch(
        f"/api/v1/accounting/category-patterns/{pattern_a['pattern_id']}",
        json={
            "description_contains": "NETFLIX",
            "category_id": "expense:subscriptions",
            "priority": 1,
            "expected_version": 1,
        },
    ).json()
    assert pattern_a["version"] == 2

    client.post(
        "/api/v1/accounting/category-patterns",
        json={"description_contains": "SPOTIFY", "category_id": "expense:subscriptions"},
    )

    persisted = client.get("/api/v1/accounting/store").json()["category_patterns"][pattern_a["pattern_id"]]
    assert persisted["version"] == 2
    response = client.patch(
        f"/api/v1/accounting/category-patterns/{pattern_a['pattern_id']}",
        json={
            "description_contains": "NETFLIX",
            "category_id": "expense:subscriptions",
            "priority": 2,
            "expected_version": 2,
        },
    )
    assert response.status_code == 200


def test_llm_usage_starts_unconfigured_and_unused(client, monkeypatch) -> None:
    class _NoCredentials:
        gemini_api_key = None
        mistral_api_key = None

    monkeypatch.setattr(accounting_llm_router, "resolve_llm_credentials", lambda session, user_id: _NoCredentials())

    body = client.get("/api/v1/accounting/llm-usage").json()
    assert body["gemini"] == {
        "configured": False,
        "used_count": 0,
        "period": "daily",
        "is_limited": False,
        "last_error": None,
    }
    assert body["mistral"]["period"] == "monthly"


def test_llm_usage_reflects_a_configured_key_and_a_tracked_failure(client, db_session, monkeypatch) -> None:
    class _FakeCredentials:
        gemini_api_key = object()
        mistral_api_key = None

    monkeypatch.setattr(accounting_llm_router, "resolve_llm_credentials", lambda session, user_id: _FakeCredentials())

    # Seeded directly as a row, not via `accounting.llm.usage.record_call` — this
    # test is checking that GET /llm-usage reports whatever's persisted, not
    # exercising record_call's own increment/freeze logic (see
    # tests/accounting/llm/test_usage.py for that).
    today_utc_midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    db_session.add(
        LLMUsage(
            user_id=DEFAULT_USER_ID,
            provider="gemini",
            period_start=today_utc_midnight,
            used_count=0,
            is_limited=True,
            last_error="429 RESOURCE_EXHAUSTED",
        )
    )
    db_session.commit()

    body = client.get("/api/v1/accounting/llm-usage").json()
    assert body["gemini"] == {
        "configured": True,
        "used_count": 0,
        "period": "daily",
        "is_limited": True,
        "last_error": "429 RESOURCE_EXHAUSTED",
    }


def test_llm_settings_default_to_no_override(client) -> None:
    body = client.get("/api/v1/accounting/settings/llm").json()
    assert body == {"gemini_key_set": False, "mistral_key_set": False}


def test_llm_settings_put_then_get_round_trips(client) -> None:
    put_response = client.put("/api/v1/accounting/settings/llm", json={"gemini_api_key": "gem-key"})
    assert put_response.status_code == 200
    assert put_response.json() == {"gemini_key_set": True, "mistral_key_set": False}
    assert client.get("/api/v1/accounting/settings/llm").json() == {"gemini_key_set": True, "mistral_key_set": False}


def test_llm_settings_put_merges_a_partial_update(client) -> None:
    client.put("/api/v1/accounting/settings/llm", json={"gemini_api_key": "gem-key"})
    client.put("/api/v1/accounting/settings/llm", json={"mistral_api_key": "mis-key"})
    body = client.get("/api/v1/accounting/settings/llm").json()
    assert body == {"gemini_key_set": True, "mistral_key_set": True}


def test_llm_settings_delete_clears_the_override(client) -> None:
    client.put("/api/v1/accounting/settings/llm", json={"gemini_api_key": "gem-key"})
    delete_response = client.delete("/api/v1/accounting/settings/llm")
    assert delete_response.status_code == 200
    assert client.get("/api/v1/accounting/settings/llm").json() == {"gemini_key_set": False, "mistral_key_set": False}


def test_net_worth_reports_the_checking_balance_as_an_asset(client) -> None:
    account_id = _import_chase_checking(client)
    body = client.get("/api/v1/accounting/net-worth").json()
    assert body["assets"] == pytest.approx(1430.0)
    checking_row = next(row for row in body["accounts"] if row["account_id"] == account_id)
    assert checking_row["balance"] == pytest.approx(1430.0)


def test_net_worth_degrades_gracefully_when_trades_has_never_been_synced(
    client, broker_connection_id, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(trades_api.app.state, "config", AppConfig(ibkr={"cache_dir": tmp_path / "empty-ibkr"}))
    investment = _create_account(
        client,
        name="Interactive Brokers",
        kind="external_investment",
        institution="external",
        currency="USD",
        parent_account_id=None,
        broker_connection_id=str(broker_connection_id),
        meta={},
    )
    client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "INTERACTIVE BROK", "counterparty_account_id": investment["account_id"]},
    )
    sofi_savings = _create_account(client, name="SoFi Savings", kind="savings", institution="SoFi")
    sofi_savings_csv = (
        "Date,Description,Type,Amount,Current balance,Status\n"
        "2026-07-01,INTERACTIVE BROK,DIRECT_PAY,-2500,14.21,Posted\n"
    )
    client.post(
        "/api/v1/accounting/import",
        files={"file": ("SOFI-Savings.csv", sofi_savings_csv, "text/csv")},
        data={
            "institution": "SoFi",
            "account_kind": "savings",
            "account_id": sofi_savings["account_id"],
            "account_name": "SoFi Savings",
        },
    )
    response = client.get("/api/v1/accounting/net-worth")
    assert response.status_code == 200
    body = response.json()
    investment_row = next(row for row in body["accounts"] if row["account_id"] == investment["account_id"])
    assert investment_row["balance"] == pytest.approx(0.0)


def test_transfer_suggestions_finds_the_chase_card_payoff(client) -> None:
    account_id = _import_chase_checking(client)
    credit_card_csv = (
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
        "06/29/2026,06/29/2026,Something else entirely,Other,Sale,70.00,\n"
    )
    credit_card_account_id = _import_chase_credit_card(client, credit_card_csv)
    suggestions = client.get("/api/v1/accounting/transfer-suggestions").json()
    assert len(suggestions) == 1
    assert {suggestions[0]["account_id"], suggestions[0]["other_account_id"]} == {
        account_id,
        credit_card_account_id,
    }
    assert suggestions[0]["description"]
    assert suggestions[0]["other_description"]


def test_transfer_suggestions_respects_a_wider_window_days(client) -> None:
    _import_chase_checking(client)
    credit_card_csv = (
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
        "06/24/2026,06/24/2026,Something else entirely,Other,Sale,70.00,\n"
    )
    _import_chase_credit_card(client, credit_card_csv)
    # The two postings are 5 days apart (06/24 vs 06/29) — outside the
    # default 3-day window, but within a wider one.
    assert client.get("/api/v1/accounting/transfer-suggestions").json() == []
    wider = client.get("/api/v1/accounting/transfer-suggestions", params={"window_days": 7}).json()
    assert len(wider) == 1


def test_duplicate_suggestions_finds_the_same_purchase_imported_from_two_sources(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic")
    client.post(
        "/api/v1/accounting/import/canonical",
        files={
            "file": (
                "a.csv",
                "Date,Description,Amount\n2026-06-30,WHOLE FOODS #123,-42.50\n",
                "text/csv",
            )
        },
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    client.post(
        "/api/v1/accounting/import/canonical",
        files={
            "file": (
                "b.csv",
                "Date,Description,Amount\n2026-06-30,Whole Foods Market,-42.50\n",
                "text/csv",
            )
        },
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
            "separator": ",",
        },
    )
    suggestions = client.get("/api/v1/accounting/duplicate-suggestions").json()
    assert len(suggestions) == 1
    group = suggestions[0]
    assert group["account_id"] == account["account_id"]
    assert len(group["postings"]) == 2

    transaction_ids = [posting["transaction_id"] for posting in group["postings"]]
    merge_response = client.post(
        "/api/v1/accounting/posting-merges",
        json={
            "kept_transaction_id": transaction_ids[0],
            "duplicate_transaction_ids": [transaction_ids[1]],
            "description": "Whole Foods Market",
        },
    )
    assert merge_response.status_code == 201
    assert client.get("/api/v1/accounting/duplicate-suggestions").json() == []

    postings = _postings(client)
    remaining_transaction_ids = {
        posting["transaction_id"] for posting in postings if posting["account_id"] == account["account_id"]
    }
    assert remaining_transaction_ids == {transaction_ids[0]}


def test_monthly_income_expense_correctly_drops_a_duplicate_that_straddles_the_query_window(client) -> None:
    """A dashboard endpoint scoped to one month must still resolve a merge whose two sides are in different months.

    `_resolved_postings` now passes `since`/`until` straight
    through to `load_ledger`'s own SQL filter — safe even for a merge like
    this one (kept side dated the last day of June, duplicate dated the
    first day of July) because `apply_posting_merges` drops a duplicate
    purely by transaction id, read by `api.dependencies`' own
    `load_posting_merges` in full (never date-filtered), regardless of whether the transaction it was
    merged into even appears in this same date-limited frame. So querying
    "July only" still correctly drops the July-dated duplicate, even
    though the June-dated kept transaction was never loaded at all.
    """
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic")
    client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("a.csv", "Date,Description,Amount\n2026-06-30,WHOLE FOODS #123,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("b.csv", "Date,Description,Amount\n2026-07-01,Whole Foods Market,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
            "separator": ",",
        },
    )
    suggestions = client.get("/api/v1/accounting/duplicate-suggestions").json()
    assert len(suggestions) == 1
    postings_in_group = suggestions[0]["postings"]
    kept_id = next(p for p in postings_in_group if p["posted_at"].startswith("2026-06-30"))["transaction_id"]
    duplicate_id = next(p for p in postings_in_group if p["posted_at"].startswith("2026-07-01"))["transaction_id"]
    merge_response = client.post(
        "/api/v1/accounting/posting-merges",
        json={"kept_transaction_id": kept_id, "duplicate_transaction_ids": [duplicate_id]},
    )
    assert merge_response.status_code == 201

    july = client.get(
        "/api/v1/accounting/income-statement/monthly", params={"start": "2026-07-01", "end": "2026-07-31"}
    ).json()
    july_row = next((row for row in july if row["month"] == "2026-07"), None)
    assert july_row is None or july_row["expense"] == pytest.approx(0.0)

    june = client.get(
        "/api/v1/accounting/income-statement/monthly", params={"start": "2026-06-01", "end": "2026-06-30"}
    ).json()
    june_row = next(row for row in june if row["month"] == "2026-06")
    assert june_row["expense"] == pytest.approx(42.50)


def _two_duplicate_transaction_ids(client) -> tuple[str, str]:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic")
    client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("a.csv", "Date,Description,Amount\n2026-06-30,WHOLE FOODS #123,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("b.csv", "Date,Description,Amount\n2026-06-30,Whole Foods Market,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
            "separator": ",",
        },
    )
    suggestions = client.get("/api/v1/accounting/duplicate-suggestions").json()
    transaction_ids = [posting["transaction_id"] for posting in suggestions[0]["postings"]]
    return transaction_ids[0], transaction_ids[1]


def test_post_posting_merge_creates_one_and_resolves_the_duplicate(client) -> None:
    kept_id, duplicate_id = _two_duplicate_transaction_ids(client)

    response = client.post(
        "/api/v1/accounting/posting-merges",
        json={
            "kept_transaction_id": kept_id,
            "duplicate_transaction_ids": [duplicate_id],
            "description": "Whole Foods",
        },
    )

    assert response.status_code == 201
    merge = response.json()
    assert merge["merge_id"] == f"merge:{kept_id}"
    assert merge["kept_transaction_id"] == kept_id
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json() == merge
    assert client.get("/api/v1/accounting/duplicate-suggestions").json() == []


def test_post_posting_merge_twice_for_the_same_kept_transaction_replaces_rather_than_duplicates(
    client, db_session
) -> None:
    """Merges have no read endpoint, so this reads the stored rows directly.

    `GET /store` deliberately doesn't carry `posting_merges` — the client
    only ever sees a merge already folded into `GET /postings` — so the
    repository is the only place "exactly one merge row, with the second
    call's description" can be observed.
    """
    kept_id, duplicate_id = _two_duplicate_transaction_ids(client)
    client.post(
        "/api/v1/accounting/posting-merges",
        json={"kept_transaction_id": kept_id, "duplicate_transaction_ids": [duplicate_id]},
    )

    response = client.post(
        "/api/v1/accounting/posting-merges",
        json={"kept_transaction_id": kept_id, "duplicate_transaction_ids": [duplicate_id], "description": "updated"},
    )

    assert response.status_code == 200
    assert "Location" not in response.headers
    merges = load_posting_merges(db_session, DEFAULT_USER_ID)
    assert list(merges.keys()) == [f"merge:{kept_id}"]
    assert merges[f"merge:{kept_id}"].description == "updated"


def test_delete_posting_merge_undoes_it(client, db_session) -> None:
    kept_id, duplicate_id = _two_duplicate_transaction_ids(client)
    client.post(
        "/api/v1/accounting/posting-merges",
        json={"kept_transaction_id": kept_id, "duplicate_transaction_ids": [duplicate_id]},
    )

    response = client.delete(f"/api/v1/accounting/posting-merges/merge:{kept_id}")

    assert response.status_code == 204
    assert load_posting_merges(db_session, DEFAULT_USER_ID) == {}
    assert len(client.get("/api/v1/accounting/duplicate-suggestions").json()) == 1


def test_delete_posting_merge_404s_for_an_unknown_id(client) -> None:
    response = client.delete("/api/v1/accounting/posting-merges/does-not-exist")
    assert response.status_code == 404


def _seed_chase_transfer_suggestion(client) -> None:
    _import_chase_checking(client)
    credit_card_csv = (
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
        "06/29/2026,06/29/2026,Something else entirely,Other,Sale,70.00,\n"
    )
    _import_chase_credit_card(client, credit_card_csv)


def test_transfer_suggestions_include_a_stable_suggestion_id(client) -> None:
    _seed_chase_transfer_suggestion(client)
    suggestions = client.get("/api/v1/accounting/transfer-suggestions").json()
    assert len(suggestions) == 1
    assert suggestions[0]["suggestion_id"]
    # Recomputing (no state changed) yields the exact same id.
    again = client.get("/api/v1/accounting/transfer-suggestions").json()
    assert again[0]["suggestion_id"] == suggestions[0]["suggestion_id"]


def test_dismissing_a_transfer_suggestion_removes_it_from_the_proposed_list(client) -> None:
    _seed_chase_transfer_suggestion(client)
    suggestion_id = client.get("/api/v1/accounting/transfer-suggestions").json()[0]["suggestion_id"]

    response = client.put(
        f"/api/v1/accounting/dismissed-suggestions/{suggestion_id}",
        json={"kind": "transfer", "description": "Chase checking <-> credit card"},
    )
    assert response.status_code == 201
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json()["suggestion_id"] == suggestion_id
    assert client.get("/api/v1/accounting/transfer-suggestions").json() == []


def test_dismissed_suggestion_shows_up_in_the_archive(client) -> None:
    _seed_chase_transfer_suggestion(client)
    suggestion_id = client.get("/api/v1/accounting/transfer-suggestions").json()[0]["suggestion_id"]
    client.put(
        f"/api/v1/accounting/dismissed-suggestions/{suggestion_id}",
        json={"kind": "transfer", "description": "Chase checking <-> credit card"},
    )

    archive = client.get("/api/v1/accounting/dismissed-suggestions").json()
    assert len(archive) == 1
    assert archive[0]["suggestion_id"] == suggestion_id
    assert archive[0]["kind"] == "transfer"
    assert archive[0]["description"] == "Chase checking <-> credit card"


def test_restoring_a_dismissed_suggestion_brings_it_back(client) -> None:
    _seed_chase_transfer_suggestion(client)
    suggestion_id = client.get("/api/v1/accounting/transfer-suggestions").json()[0]["suggestion_id"]
    client.put(
        f"/api/v1/accounting/dismissed-suggestions/{suggestion_id}",
        json={"kind": "transfer", "description": "desc"},
    )
    assert client.get("/api/v1/accounting/transfer-suggestions").json() == []

    restore_response = client.delete(f"/api/v1/accounting/dismissed-suggestions/{suggestion_id}")
    assert restore_response.status_code == 204
    assert len(client.get("/api/v1/accounting/transfer-suggestions").json()) == 1
    assert client.get("/api/v1/accounting/dismissed-suggestions").json() == []


def test_restoring_an_unknown_suggestion_is_a_404(client) -> None:
    response = client.delete("/api/v1/accounting/dismissed-suggestions/does-not-exist")
    assert response.status_code == 404


def _real_leg(postings: list[dict], account_id: str) -> dict:
    """The oldest real leg on this account, by date then posting id.

    Sorted rather than taking whatever arrives first, so a caller picks the
    same posting regardless of the order `GET /postings` returns its page in
    — that endpoint answers newest first (see `api.api_models.PostingPage`)
    while the underlying frame is built oldest first.
    """
    on_account = sorted(
        (posting for posting in postings if posting["account_id"] == account_id),
        key=operator.itemgetter("posted_at", "posting_id"),
    )
    return on_account[0]


def _real_leg_transaction_id(postings: list[dict], account_id: str) -> str:
    """The transaction of `_real_leg` — the two must agree, or a test splits one row and links another."""
    return _real_leg(postings, account_id)["transaction_id"]


def test_post_transfer_link_confirms_a_pair_and_excludes_it_from_income_statement(client) -> None:
    checking_id = _import_chase_checking(client)
    card_id = _import_chase_credit_card(
        client,
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n06/29/2026,06/29/2026,Payment Thank You,,Payment,70.00,\n",
    )
    postings = _postings(client)
    checking_transaction_id = _real_leg_transaction_id(postings, checking_id)
    card_transaction_id = _real_leg_transaction_id(postings, card_id)

    response = client.post(
        "/api/v1/accounting/transfer-links",
        json={"transaction_id_a": checking_transaction_id, "transaction_id_b": card_transaction_id},
    )
    assert response.status_code == 201
    link = response.json()
    assert {link["transaction_id_a"], link["transaction_id_b"]} == {checking_transaction_id, card_transaction_id}
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json() == link
    assert link["source"] == "manual"

    updated = _postings(client)
    checking_row = _real_leg(updated, checking_id)
    card_row = _real_leg(updated, card_id)
    assert checking_row["is_linked_transfer"] is True
    assert checking_row["linked_transaction_id"] == card_transaction_id
    assert card_row["is_linked_transfer"] is True
    assert card_row["linked_transaction_id"] == checking_transaction_id

    totals = client.get(
        "/api/v1/accounting/income-statement/category-totals", params={"start": "2026-06-01", "end": "2026-06-30"}
    ).json()
    uncategorized_expense = [row for row in totals if row["category_id"] == "uncategorized:expense-category"]
    assert uncategorized_expense == [] or uncategorized_expense[0]["amount"] == pytest.approx(0.0)


def test_post_transfer_link_rejects_a_transaction_already_in_another_link(client) -> None:
    checking_id = _import_chase_checking(client)
    card_id = _import_chase_credit_card(
        client,
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n06/29/2026,06/29/2026,Payment Thank You,,Payment,70.00,\n",
    )
    other_card_id = _create_account(client, name="Other Card", kind="credit_card", institution="Chase")["account_id"]
    client.post(
        "/api/v1/accounting/import",
        files={
            "file": (
                "other.csv",
                "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n06/29/2026,06/29/2026,Also Payment Thank You,,Payment,70.00,\n",
                "text/csv",
            )
        },
        data={
            "institution": "Chase",
            "account_kind": "credit_card",
            "account_id": other_card_id,
            "account_name": "Other Card",
        },
    )
    postings = _postings(client)
    checking_transaction_id = _real_leg_transaction_id(postings, checking_id)
    card_transaction_id = _real_leg_transaction_id(postings, card_id)
    other_card_transaction_id = _real_leg_transaction_id(postings, other_card_id)
    client.post(
        "/api/v1/accounting/transfer-links",
        json={"transaction_id_a": checking_transaction_id, "transaction_id_b": card_transaction_id},
    )

    response = client.post(
        "/api/v1/accounting/transfer-links",
        json={"transaction_id_a": checking_transaction_id, "transaction_id_b": other_card_transaction_id},
    )
    assert response.status_code == 409


def test_post_transfer_link_409s_when_the_transaction_was_linked_concurrently(client, monkeypatch) -> None:
    """The loser of a link race gets the same 409 the sequential path gives, not an unhandled `IntegrityError`.

    Every check in `post_transfer_link` reads before it writes, so two
    requests linking one transaction to two *different* partners both pass
    them; the membership insert then collides on
    `uq_transfer_linked_transactions_user_transaction`. Blinding
    `load_transfer_links` reproduces that deterministically — returning
    nothing is exactly what the loser saw, having read before the winner
    committed.
    """
    checking_id = _import_chase_checking(client)
    card_id = _import_chase_credit_card(
        client,
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n06/29/2026,06/29/2026,Payment Thank You,,Payment,70.00,\n",
    )
    other_card_id = _create_account(client, name="Other Card", kind="credit_card", institution="Chase")["account_id"]
    client.post(
        "/api/v1/accounting/import",
        files={
            "file": (
                "other.csv",
                "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n06/29/2026,06/29/2026,Also Payment Thank You,,Payment,70.00,\n",
                "text/csv",
            )
        },
        data={
            "institution": "Chase",
            "account_kind": "credit_card",
            "account_id": other_card_id,
            "account_name": "Other Card",
        },
    )
    postings = _postings(client)
    checking_transaction_id = _real_leg_transaction_id(postings, checking_id)
    card_transaction_id = _real_leg_transaction_id(postings, card_id)
    other_card_transaction_id = _real_leg_transaction_id(postings, other_card_id)
    assert (
        client.post(
            "/api/v1/accounting/transfer-links",
            json={"transaction_id_a": checking_transaction_id, "transaction_id_b": card_transaction_id},
        ).status_code
        == 201
    )

    monkeypatch.setattr(postings_router, "load_transfer_links", lambda *_args, **_kwargs: [])
    response = client.post(
        "/api/v1/accounting/transfer-links",
        json={"transaction_id_a": checking_transaction_id, "transaction_id_b": other_card_transaction_id},
    )

    assert response.status_code == 409


def test_post_transfer_link_is_idempotent_for_the_same_pair(client) -> None:
    checking_id = _import_chase_checking(client)
    card_id = _import_chase_credit_card(
        client,
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n06/29/2026,06/29/2026,Payment Thank You,,Payment,70.00,\n",
    )
    postings = _postings(client)
    checking_transaction_id = _real_leg_transaction_id(postings, checking_id)
    card_transaction_id = _real_leg_transaction_id(postings, card_id)
    first = client.post(
        "/api/v1/accounting/transfer-links",
        json={"transaction_id_a": checking_transaction_id, "transaction_id_b": card_transaction_id},
    )
    second = client.post(
        "/api/v1/accounting/transfer-links",
        json={"transaction_id_a": card_transaction_id, "transaction_id_b": checking_transaction_id},
    )
    assert (first.status_code, second.status_code) == (201, 200)
    assert "Location" not in second.headers
    assert second.json()["link_id"] == first.json()["link_id"]
    assert len(client.get("/api/v1/accounting/store").json()["transfer_links"]) == 1


def test_delete_transfer_link_unlinks_it(client) -> None:
    checking_id = _import_chase_checking(client)
    card_id = _import_chase_credit_card(
        client,
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n06/29/2026,06/29/2026,Payment Thank You,,Payment,70.00,\n",
    )
    postings = _postings(client)
    checking_transaction_id = _real_leg_transaction_id(postings, checking_id)
    card_transaction_id = _real_leg_transaction_id(postings, card_id)
    link = client.post(
        "/api/v1/accounting/transfer-links",
        json={"transaction_id_a": checking_transaction_id, "transaction_id_b": card_transaction_id},
    ).json()

    response = client.delete(f"/api/v1/accounting/transfer-links/{link['link_id']}")

    assert response.status_code == 204
    assert client.get("/api/v1/accounting/store").json()["transfer_links"] == []
    updated = _postings(client)
    assert next(p for p in updated if p["account_id"] == checking_id)["is_linked_transfer"] is False


def test_delete_transfer_link_404s_for_an_unknown_id(client) -> None:
    response = client.delete("/api/v1/accounting/transfer-links/does-not-exist")
    assert response.status_code == 404


def test_post_transfer_link_rejects_an_already_split_transaction(client) -> None:
    checking_id = _import_chase_checking(client)
    card_id = _import_chase_credit_card(
        client,
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n06/29/2026,06/29/2026,Payment Thank You,,Payment,70.00,\n",
    )
    postings = _postings(client)
    checking_transaction_id = _real_leg_transaction_id(postings, checking_id)
    card_transaction_id = _real_leg_transaction_id(postings, card_id)
    checking_posting_id = _real_leg(postings, checking_id)["posting_id"]
    client.put(
        f"/api/v1/accounting/postings/{checking_posting_id}/split",
        json=[{"amount": -50.0}, {"amount": -20.0}],
    )

    response = client.post(
        "/api/v1/accounting/transfer-links",
        json={"transaction_id_a": checking_transaction_id, "transaction_id_b": card_transaction_id},
    )
    assert response.status_code == 400


def test_dismissing_a_duplicate_suggestion_removes_it_from_the_proposed_list(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic")
    client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("a.csv", "Date,Description,Amount\n2026-06-30,WHOLE FOODS #123,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("b.csv", "Date,Description,Amount\n2026-06-30,Whole Foods Market,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
            "separator": ",",
        },
    )
    suggestions = client.get("/api/v1/accounting/duplicate-suggestions").json()
    assert len(suggestions) == 1
    suggestion_id = suggestions[0]["suggestion_id"]

    response = client.put(
        f"/api/v1/accounting/dismissed-suggestions/{suggestion_id}",
        json={"kind": "duplicate", "description": "Whole Foods x2"},
    )
    assert response.status_code == 201
    assert client.get("/api/v1/accounting/duplicate-suggestions").json() == []


def test_postings_report_which_rule_resolved_them(client) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    assert payroll["resolved_by_transfer_rule_id"] is None

    # `counterparty_account_id` is now a real foreign key into `accounts` (see
    # `accounting.db.automation.TransferRule`) — the account it names must already
    # exist, unlike before when a rule could forward-reference one created later.
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    rule = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL", "counterparty_account_id": employer["account_id"]},
    ).json()

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["account_id"] == account_id and p["amount"] > 0)
    assert updated_payroll["resolved_by_transfer_rule_id"] == rule["rule_id"]


def test_postings_report_a_manual_transfer_override(client) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    assert payroll["manual_transfer_override_posting_id"] is None
    placeholder = next(p for p in postings if p["account_id"] == "uncategorized:income")

    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    response = client.put(
        f"/api/v1/accounting/postings/{placeholder['posting_id']}/override", json={"account_id": employer["account_id"]}
    )
    assert response.status_code == 200

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["account_id"] == account_id and p["amount"] > 0)
    updated_counterparty = next(p for p in updated if p["account_id"] == employer["account_id"])
    assert updated_payroll["manual_transfer_override_posting_id"] == placeholder["posting_id"]
    assert updated_counterparty["manual_transfer_override_posting_id"] == placeholder["posting_id"]


def test_postings_suppress_via_rule_badge_when_a_manual_override_also_applies(client) -> None:
    """A manual override is applied after rules (see `_resolved_postings_and_store`), so it always wins if both

    somehow apply to the same transaction — `resolved_by_transfer_rule_id` must not claim "via rule" when a
    manual override is what actually decided the account shown.
    """
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    placeholder = next(p for p in postings if p["account_id"] == "uncategorized:income")

    manual_employer = _create_account(client, name="Manual Employer", kind="income_source", institution="internal")
    client.put(
        f"/api/v1/accounting/postings/{placeholder['posting_id']}/override",
        json={"account_id": manual_employer["account_id"]},
    )

    rule_employer = _create_account(client, name="Rule Employer", kind="income_source", institution="internal")
    client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL", "counterparty_account_id": rule_employer["account_id"]},
    )

    updated = _postings(client)
    updated_payroll = next(p for p in updated if p["account_id"] == account_id and p["amount"] > 0)
    assert updated_payroll["resolved_by_transfer_rule_id"] is None
    assert updated_payroll["manual_transfer_override_posting_id"] == placeholder["posting_id"]
    assert any(p["account_id"] == manual_employer["account_id"] for p in updated)
    assert not any(p["account_id"] == rule_employer["account_id"] for p in updated)


def test_post_transfer_rule_mints_a_content_derived_id(client) -> None:
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    response = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL", "counterparty_account_id": employer["account_id"]},
    )
    assert response.status_code == 201
    rule = response.json()
    assert rule["rule_id"]
    assert rule["description_contains"] == "PAYROLL"
    assert rule["active"] is True
    assert rule["priority"] == 100
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json() == rule


def test_post_transfer_rule_twice_with_the_same_criteria_replaces_rather_than_duplicates(client) -> None:
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    first = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL", "counterparty_account_id": employer["account_id"]},
    ).json()
    second = client.post(
        "/api/v1/accounting/transfer-rules",
        json={
            "description_contains": "PAYROLL",
            "counterparty_account_id": employer["account_id"],
            "priority": 5,
        },
    ).json()

    assert second["rule_id"] == first["rule_id"]
    rules = client.get("/api/v1/accounting/store").json()["transfer_rules"]
    matching = [r for r in rules if r["rule_id"] == first["rule_id"]]
    assert len(matching) == 1
    assert matching[0]["priority"] == 5


def test_post_transfer_rule_replacing_an_existing_one_preserves_active_and_exclusions(client) -> None:
    _import_chase_checking(client)
    postings = _postings(client)
    transaction_id = next(p["transaction_id"] for p in postings if p["transaction_id"])
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    rule = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL", "counterparty_account_id": employer["account_id"]},
    ).json()
    client.patch(
        f"/api/v1/accounting/transfer-rules/{rule['rule_id']}",
        json={
            "description_contains": "PAYROLL",
            "counterparty_account_id": employer["account_id"],
            "priority": rule["priority"],
            "active": False,
            "excluded_transaction_ids": [transaction_id],
            "expected_version": 1,
        },
    )

    replaced = client.post(
        "/api/v1/accounting/transfer-rules",
        json={
            "description_contains": "PAYROLL",
            "counterparty_account_id": employer["account_id"],
            "priority": 5,
        },
    ).json()

    assert replaced["rule_id"] == rule["rule_id"]
    assert replaced["priority"] == 5
    assert replaced["active"] is False
    assert replaced["excluded_transaction_ids"] == [transaction_id]


def test_post_transfer_rule_with_different_criteria_gets_a_different_id(client) -> None:
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    other_employer = _create_account(client, name="Other Co", kind="income_source", institution="internal")
    first = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL", "counterparty_account_id": employer["account_id"]},
    ).json()
    second = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL", "counterparty_account_id": other_employer["account_id"]},
    ).json()
    assert first["rule_id"] != second["rule_id"]
    assert len(client.get("/api/v1/accounting/store").json()["transfer_rules"]) == 2


def test_patch_transfer_rule_updates_fields_and_increments_version(client) -> None:
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    rule = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL", "counterparty_account_id": employer["account_id"]},
    ).json()
    assert rule["version"] == 1

    response = client.patch(
        f"/api/v1/accounting/transfer-rules/{rule['rule_id']}",
        json={
            "description_contains": "PAYROLL",
            "counterparty_account_id": employer["account_id"],
            "priority": 7,
            "description": "my new note",
            "active": False,
            "expected_version": 1,
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["priority"] == 7
    assert updated["description"] == "my new note"
    assert updated["active"] is False
    assert updated["version"] == 2

    rules = client.get("/api/v1/accounting/store").json()["transfer_rules"]
    assert len(rules) == 1
    assert rules[0]["version"] == 2


def test_patch_transfer_rule_with_a_stale_expected_version_gets_409(client) -> None:
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    rule = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL", "counterparty_account_id": employer["account_id"]},
    ).json()

    response = client.patch(
        f"/api/v1/accounting/transfer-rules/{rule['rule_id']}",
        json={
            "description_contains": "PAYROLL",
            "counterparty_account_id": employer["account_id"],
            "priority": 3,
            "expected_version": rule["version"] + 1,
        },
    )
    assert response.status_code == 409

    unchanged = client.get("/api/v1/accounting/store").json()["transfer_rules"][0]
    assert unchanged["priority"] == 100
    assert unchanged["version"] == 1


def test_patch_transfer_rule_that_does_not_exist_gets_404(client) -> None:
    response = client.patch(
        "/api/v1/accounting/transfer-rules/does-not-exist",
        json={"description_contains": "PAYROLL", "priority": 0, "expected_version": 1},
    )
    assert response.status_code == 404


def test_patch_transfer_rule_referencing_a_nonexistent_account_fails() -> None:
    """Same 500-not-400 convention as `test_put_transfer_rules_referencing_a_nonexistent_account_fails` —
    `counterparty_account_id` is a real foreign key, and the database itself is the source of truth,
    not a duplicated API-level existence check.
    """
    client = TestClient(trades_api.app, raise_server_exceptions=False)
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    rule = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL", "counterparty_account_id": employer["account_id"]},
    ).json()

    response = client.patch(
        f"/api/v1/accounting/transfer-rules/{rule['rule_id']}",
        json={
            "description_contains": "PAYROLL",
            "counterparty_account_id": "does-not-exist",
            "priority": 0,
            "expected_version": rule["version"],
        },
    )
    assert response.status_code == 500


def test_patch_transfer_rule_with_a_newly_resolvable_link_does_not_409(client) -> None:
    """Same scenario as `test_put_transfer_rules_with_a_newly_resolvable_link_does_not_409`, but for `PATCH`:
    editing a rule so it newly matches an existing transaction runs `reconcile_and_persist_rule_links`
    (a second commit in the same request) right after the row-scoped update commit — both must succeed
    without the row-version check on the first spuriously rejecting the second.
    """
    checking_id = _import_chase_checking(client)
    credit_card_csv = (
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
        "06/29/2026,06/29/2026,Automatic Payment - Thank You,Payment,Payment,70.00,\n"
    )
    credit_card_id = _import_chase_credit_card(client, credit_card_csv)

    rule = client.post(
        "/api/v1/accounting/transfer-rules",
        json={
            "description_contains": "not a real match yet",
            "account_id": checking_id,
            "counterparty_account_id": credit_card_id,
        },
    ).json()

    response = client.patch(
        f"/api/v1/accounting/transfer-rules/{rule['rule_id']}",
        json={
            "description_contains": "Payment to Chase card",
            "account_id": checking_id,
            "counterparty_account_id": credit_card_id,
            "priority": 0,
            "expected_version": rule["version"],
        },
    )
    assert response.status_code == 200
    assert len(client.get("/api/v1/accounting/store").json()["transfer_links"]) == 1


def test_delete_transfer_rule_removes_it(client) -> None:
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    rule = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL", "counterparty_account_id": employer["account_id"]},
    ).json()

    response = client.delete(f"/api/v1/accounting/transfer-rules/{rule['rule_id']}")
    assert response.status_code == 204

    assert client.get("/api/v1/accounting/store").json()["transfer_rules"] == []


def test_delete_transfer_rule_that_is_already_gone_gets_404(client) -> None:
    response = client.delete("/api/v1/accounting/transfer-rules/does-not-exist")
    assert response.status_code == 404


def test_two_patches_fired_with_the_same_expected_version_the_second_gets_409(client) -> None:
    """Reproduces the actual bug this feature fixes: two edits fired off the same stale client-side
    snapshot (e.g. two rapid clicks before the first response lands) must not both silently apply —
    the second has to see that the row moved out from under it.
    """
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    rule = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL", "counterparty_account_id": employer["account_id"]},
    ).json()

    first = client.patch(
        f"/api/v1/accounting/transfer-rules/{rule['rule_id']}",
        json={
            "description_contains": "PAYROLL",
            "counterparty_account_id": employer["account_id"],
            "priority": 1,
            "expected_version": rule["version"],
        },
    )
    second = client.patch(
        f"/api/v1/accounting/transfer-rules/{rule['rule_id']}",
        json={
            "description_contains": "PAYROLL",
            "counterparty_account_id": employer["account_id"],
            "priority": 2,
            "expected_version": rule["version"],
        },
    )
    assert first.status_code == 200
    assert second.status_code == 409

    final = client.get("/api/v1/accounting/store").json()["transfer_rules"][0]
    assert final["priority"] == 1


def test_patch_transfer_rule_without_expected_version_is_last_write_wins(client) -> None:
    """The `active`-toggle path omits `expected_version` (sends null) so fast on/off/on flipping settles

    on the last click instead of 409-ing against its own in-flight earlier click — two PATCHes off the
    same stale snapshot both succeed, and the last one's value wins.
    """
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    rule = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL", "counterparty_account_id": employer["account_id"]},
    ).json()

    def toggle(active: bool) -> int:
        return client.patch(
            f"/api/v1/accounting/transfer-rules/{rule['rule_id']}",
            json={
                "description_contains": "PAYROLL",
                "counterparty_account_id": employer["account_id"],
                "priority": rule["priority"],
                "active": active,
                "expected_version": None,  # opt out of the check — last-write-wins
            },
        ).status_code

    # Both fired from the same original snapshot (version 1); neither 409s.
    assert toggle(active=False) == 200
    assert toggle(active=True) == 200
    assert toggle(active=False) == 200

    final = client.get("/api/v1/accounting/store").json()["transfer_rules"][0]
    assert final["active"] is False  # the last write won
    assert final["version"] == 4  # still bumped on every write, just never checked


def test_patch_transfer_rule_updates_excluded_transaction_ids(client) -> None:
    checking_id = _import_chase_checking(client)
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    rule = client.post(
        "/api/v1/accounting/transfer-rules",
        json={
            "description_contains": "PAYROLL",
            "account_id": checking_id,
            "counterparty_account_id": employer["account_id"],
        },
    ).json()
    assert rule["excluded_transaction_ids"] == []
    payroll_posting = next(p for p in _postings(client) if p["account_id"] == checking_id and p["amount"] > 0)

    response = client.patch(
        f"/api/v1/accounting/transfer-rules/{rule['rule_id']}",
        json={
            "description_contains": "PAYROLL",
            "account_id": checking_id,
            "counterparty_account_id": employer["account_id"],
            "priority": 100,
            "excluded_transaction_ids": [payroll_posting["transaction_id"]],
            "expected_version": rule["version"],
        },
    )
    assert response.status_code == 200
    updated_rule = response.json()
    assert updated_rule["excluded_transaction_ids"] == [payroll_posting["transaction_id"]]

    persisted = client.get("/api/v1/accounting/store").json()["transfer_rules"][0]
    assert persisted["excluded_transaction_ids"] == [payroll_posting["transaction_id"]]

    # And removing it again clears the exclusion.
    response = client.patch(
        f"/api/v1/accounting/transfer-rules/{rule['rule_id']}",
        json={
            "description_contains": "PAYROLL",
            "account_id": checking_id,
            "counterparty_account_id": employer["account_id"],
            "priority": 100,
            "excluded_transaction_ids": [],
            "expected_version": updated_rule["version"],
        },
    )
    assert response.status_code == 200
    assert response.json()["excluded_transaction_ids"] == []


def test_creating_an_unrelated_rule_does_not_reset_another_rules_version(client) -> None:
    """`POST /transfer-rules` writes through `repositories.interpretation.upsert_transfer_rule`, which
    reuses `replace_transfer_rules`' `INSERT ... ON CONFLICT (id) DO UPDATE` — if that upsert ever
    reset `version` back to its column default, a client holding an already-bumped version for some
    *other*, untouched rule would get a spurious 409 on its very next `PATCH`. Omitting `version` from
    that statement's `SET` clause exists specifically to prevent that.
    """
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    other_employer = _create_account(client, name="Other Co", kind="income_source", institution="internal")
    rule_a = client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL A", "counterparty_account_id": employer["account_id"]},
    ).json()
    rule_a = client.patch(
        f"/api/v1/accounting/transfer-rules/{rule_a['rule_id']}",
        json={
            "description_contains": "PAYROLL A",
            "counterparty_account_id": employer["account_id"],
            "priority": 1,
            "expected_version": rule_a["version"],
        },
    ).json()
    assert rule_a["version"] == 2

    client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "PAYROLL B", "counterparty_account_id": other_employer["account_id"]},
    )

    rules = client.get("/api/v1/accounting/store").json()["transfer_rules"]
    persisted_a = next(r for r in rules if r["rule_id"] == rule_a["rule_id"])
    assert persisted_a["version"] == 2

    response = client.patch(
        f"/api/v1/accounting/transfer-rules/{rule_a['rule_id']}",
        json={
            "description_contains": "PAYROLL A",
            "counterparty_account_id": employer["account_id"],
            "priority": 2,
            "expected_version": 2,
        },
    )
    assert response.status_code == 200


def test_post_subcategory_auto_creates_other_for_a_categorys_first_subcategory(client) -> None:
    # The "Other" catch-all is an invariant of the tree, not of any one
    # request: creating the first real subcategory mints it as a side effect,
    # so a caller never has to remember to add one (see
    # `taxonomy.normalize_categories`).
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    response = client.post(
        "/api/v1/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#000000"}
    )
    assert response.status_code == 201
    store = client.get("/api/v1/accounting/store").json()
    assert "expense:custom:other" in store["categories"]


def test_category_rename_without_a_collision_just_renames(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    response = client.post("/api/v1/accounting/categories/expense:custom/rename", json={"name": "Renamed"})
    assert response.status_code == 200
    body = response.json()
    assert body["merged"] is False
    assert body["categories"]["expense:custom"]["name"] == "Renamed"


def test_category_rename_404s_for_an_unknown_category(client) -> None:
    response = client.post("/api/v1/accounting/categories/expense:nope/rename", json={"name": "Anything"})
    assert response.status_code == 404


def test_category_rename_merges_into_an_existing_category_and_repoints_postings(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,Nourriture\n"
    client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    store = client.get("/api/v1/accounting/store").json()
    nourriture = next(c for c in store["categories"].values() if c["name"] == "Nourriture")
    client.post("/api/v1/accounting/categories", json={"name": "Food", "classification": "expense", "color": "#111111"})

    response = client.post(f"/api/v1/accounting/categories/{nourriture['category_id']}/rename", json={"name": "Food"})
    assert response.status_code == 200
    body = response.json()
    assert body["merged"] is True
    assert nourriture["category_id"] not in body["categories"]
    assert "expense:food" in body["categories"]

    postings = _postings(client)
    grocery_leg = next(p for p in postings if p["account_id"] == account["account_id"])
    assert grocery_leg["category_id"] == "expense:food"


def test_category_rename_merge_leaves_the_raw_ledger_carrying_the_imported_category(client) -> None:
    """DB-audit D14: the merge is resolved on read, never written back onto the posting.

    `GET /ledger/export` is the raw ledger, so it still names the category
    the file itself did; `GET /postings` — the same rows with the taxonomy's
    redirects applied — names the survivor.
    """
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,Nourriture\n"
    client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    store = client.get("/api/v1/accounting/store").json()
    nourriture = next(c for c in store["categories"].values() if c["name"] == "Nourriture")
    client.post("/api/v1/accounting/categories", json={"name": "Food", "classification": "expense", "color": "#111111"})

    client.post(f"/api/v1/accounting/categories/{nourriture['category_id']}/rename", json={"name": "Food"})

    raw = _ledger_export(client)
    raw_leg = next(p for p in raw if p["account_id"] == account["account_id"])
    assert raw_leg["category_id"] == nourriture["category_id"]

    postings = _postings(client)
    resolved_leg = next(p for p in postings if p["account_id"] == account["account_id"])
    assert resolved_leg["category_id"] == "expense:food"


def test_category_rename_merge_repoints_a_manual_override(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,Nourriture\n"
    client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    store = client.get("/api/v1/accounting/store").json()
    nourriture = next(c for c in store["categories"].values() if c["name"] == "Nourriture")
    postings = _postings(client)
    other_posting = next(p for p in postings if p["account_id"] != account["account_id"])
    client.put(
        f"/api/v1/accounting/postings/{other_posting['posting_id']}/override",
        json={"category_id": nourriture["category_id"]},
    )
    client.post("/api/v1/accounting/categories", json={"name": "Food", "classification": "expense", "color": "#111111"})

    client.post(f"/api/v1/accounting/categories/{nourriture['category_id']}/rename", json={"name": "Food"})

    postings = _postings(client)
    overridden = next(p for p in postings if p["posting_id"] == other_posting["posting_id"])
    assert overridden["category_id"] == "expense:food"


def test_category_delete_preview_counts_postings_pointing_at_the_category(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,Nourriture\n"
    client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    store = client.get("/api/v1/accounting/store").json()
    nourriture = next(c for c in store["categories"].values() if c["name"] == "Nourriture")

    response = client.get(f"/api/v1/accounting/categories/{nourriture['category_id']}/delete-preview")
    assert response.status_code == 200
    assert response.json()["posting_count"] == 1


def test_category_delete_preview_is_zero_for_an_unused_category(client) -> None:
    response = client.get("/api/v1/accounting/categories/expense:food-drink/delete-preview")
    assert response.status_code == 200
    assert response.json()["posting_count"] == 0


def test_category_delete_preview_404s_for_an_unknown_category(client) -> None:
    response = client.get("/api/v1/accounting/categories/expense:nope/delete-preview")
    assert response.status_code == 404


def test_category_delete_removes_the_category_and_uncategorizes_its_postings(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,Nourriture\n"
    client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    store = client.get("/api/v1/accounting/store").json()
    nourriture = next(c for c in store["categories"].values() if c["name"] == "Nourriture")

    response = client.delete(f"/api/v1/accounting/categories/{nourriture['category_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["uncategorized_posting_count"] == 1
    assert nourriture["category_id"] not in body["categories"]

    postings = _postings(client)
    grocery_leg = next(p for p in postings if p["account_id"] == account["account_id"])
    assert grocery_leg["category_id"] is None


def test_category_delete_cascades_to_subcategories(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    client.post(
        "/api/v1/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#222222"}
    )

    response = client.delete("/api/v1/accounting/categories/expense:custom")
    assert response.status_code == 200
    body = response.json()
    assert "expense:custom" not in body["categories"]
    assert "expense:custom:gadgets" not in body["categories"]


def test_category_delete_404s_for_an_unknown_category(client) -> None:
    response = client.delete("/api/v1/accounting/categories/expense:nope")
    assert response.status_code == 404


def test_post_category_creates_a_new_top_level_category(client) -> None:
    response = client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["category_id"] == "expense:custom"
    # The address the 201 advertises has to answer, not just be well-formed:
    # a `Location` pointing at a 404 is worse than no `Location` at all.
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json() == body
    store = client.get("/api/v1/accounting/store").json()
    assert "expense:custom" in store["categories"]


def test_post_category_409s_on_a_same_classification_duplicate_name(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    response = client.post(
        "/api/v1/accounting/categories", json={"name": "custom", "classification": "expense", "color": "#111111"}
    )
    assert response.status_code == 409


def test_post_category_allows_the_same_name_under_a_different_classification(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    response = client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "income", "color": "#111111"}
    )
    assert response.status_code == 201


def test_post_subcategory_creates_a_new_subcategory(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    response = client.post(
        "/api/v1/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#222222"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["category_id"] == "expense:custom:gadgets"
    assert body["parent_category_id"] == "expense:custom"
    # A subcategory's `Location` is a plain `/categories/{id}`, not a route
    # nested under the parent — one flat address space for the whole tree.
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json() == body


def test_post_subcategory_404s_for_an_unknown_parent(client) -> None:
    response = client.post(
        "/api/v1/accounting/categories/expense:nope/subcategories", json={"name": "Gadgets", "color": "#222222"}
    )
    assert response.status_code == 404


def test_post_subcategory_409s_on_a_same_name_sibling(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    client.post(
        "/api/v1/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#222222"}
    )
    response = client.post(
        "/api/v1/accounting/categories/expense:custom/subcategories", json={"name": "gadgets", "color": "#333333"}
    )
    assert response.status_code == 409


def test_post_subcategory_allows_the_same_name_under_a_different_parent(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    client.post(
        "/api/v1/accounting/categories", json={"name": "Other Top", "classification": "expense", "color": "#444444"}
    )
    client.post(
        "/api/v1/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#222222"}
    )
    response = client.post(
        "/api/v1/accounting/categories/expense:other-top/subcategories", json={"name": "Gadgets", "color": "#555555"}
    )
    assert response.status_code == 201


def test_category_rename_preview_reports_no_merge_for_a_plain_rename(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    response = client.get("/api/v1/accounting/categories/expense:custom/rename-preview", params={"name": "Renamed"})
    assert response.status_code == 200
    body = response.json()
    assert body["will_merge"] is False
    assert body["target_name"] is None


def test_category_rename_preview_404s_for_an_unknown_category(client) -> None:
    response = client.get("/api/v1/accounting/categories/expense:nope/rename-preview", params={"name": "Anything"})
    assert response.status_code == 404


def test_category_rename_preview_reports_merge_for_a_top_level_same_classification_collision(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    client.post("/api/v1/accounting/categories", json={"name": "Food", "classification": "expense", "color": "#111111"})
    response = client.get("/api/v1/accounting/categories/expense:custom/rename-preview", params={"name": "Food"})
    assert response.status_code == 200
    body = response.json()
    assert body["will_merge"] is True
    assert body["target_name"] == "Food"


def test_category_rename_preview_reports_no_merge_across_different_classifications(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    client.post("/api/v1/accounting/categories", json={"name": "Food", "classification": "income", "color": "#111111"})
    response = client.get("/api/v1/accounting/categories/expense:custom/rename-preview", params={"name": "Food"})
    assert response.status_code == 200
    assert response.json()["will_merge"] is False


def test_category_rename_preview_reports_merge_for_a_subcategory_same_parent_collision(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    client.post(
        "/api/v1/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#222222"}
    )
    client.post(
        "/api/v1/accounting/categories/expense:custom/subcategories", json={"name": "Books", "color": "#333333"}
    )
    response = client.get(
        "/api/v1/accounting/categories/expense:custom:gadgets/rename-preview", params={"name": "Books"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["will_merge"] is True
    assert body["target_name"] == "Books"


def test_category_rename_preview_reports_no_merge_across_different_parents(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    client.post(
        "/api/v1/accounting/categories", json={"name": "Other Top", "classification": "expense", "color": "#444444"}
    )
    client.post(
        "/api/v1/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#222222"}
    )
    client.post(
        "/api/v1/accounting/categories/expense:other-top/subcategories", json={"name": "Books", "color": "#333333"}
    )
    response = client.get(
        "/api/v1/accounting/categories/expense:custom:gadgets/rename-preview", params={"name": "Books"}
    )
    assert response.status_code == 200
    assert response.json()["will_merge"] is False


def test_category_rename_preview_reports_a_budget_the_merge_would_delete(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    client.post("/api/v1/accounting/categories", json={"name": "Food", "classification": "expense", "color": "#111111"})
    client.post(
        "/api/v1/accounting/budgets", json={"month": "2026-06", "category_id": "expense:custom", "amount": 100.0}
    )
    client.post("/api/v1/accounting/budgets", json={"month": "2026-06", "category_id": "expense:food", "amount": 200.0})
    response = client.get("/api/v1/accounting/categories/expense:custom/rename-preview", params={"name": "Food"})
    assert response.status_code == 200
    body = response.json()
    assert body["will_merge"] is True
    assert body["budgets_to_delete"] == [{"month": "2026-06", "amount": pytest.approx(100.0), "currency": "USD"}]


def test_category_rename_preview_reports_no_budgets_to_delete_without_a_collision(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    client.post("/api/v1/accounting/categories", json={"name": "Food", "classification": "expense", "color": "#111111"})
    client.post(
        "/api/v1/accounting/budgets", json={"month": "2026-06", "category_id": "expense:custom", "amount": 100.0}
    )
    response = client.get("/api/v1/accounting/categories/expense:custom/rename-preview", params={"name": "Food"})
    assert response.status_code == 200
    assert response.json()["budgets_to_delete"] == []


def test_category_rename_merge_drops_the_merged_away_budget_instead_of_failing(client) -> None:
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    client.post("/api/v1/accounting/categories", json={"name": "Food", "classification": "expense", "color": "#111111"})
    client.post(
        "/api/v1/accounting/budgets", json={"month": "2026-06", "category_id": "expense:custom", "amount": 100.0}
    )
    client.post("/api/v1/accounting/budgets", json={"month": "2026-06", "category_id": "expense:food", "amount": 200.0})
    response = client.post("/api/v1/accounting/categories/expense:custom/rename", json={"name": "Food"})
    assert response.status_code == 200
    budgets = client.get("/api/v1/accounting/store").json()["budgets"]
    assert len(budgets) == 1
    assert budgets[0]["category_id"] == "expense:food"
    assert budgets[0]["amount"] == pytest.approx(200.0)


def _write_posting_with_tags(
    db_session, account_id: str, posting_id: str, transaction_id: str, tag_ids: list[str]
) -> None:
    """Give a posting a real `posting_tags` row, bypassing the store's own tag-rename endpoints.

    Nothing exposed over HTTP today writes `posting_tags` directly (see
    `accounting.importers.ingest._write_ledger`, the only place that ever
    does) — no importer format has a Tags column, and
    `PUT /postings/{id}/override` only ever writes the separate
    `posting_override_tags` join table. Calling `_write_ledger` straight
    from the test is the only way to get a real `posting_tags` row to
    merge/repoint.
    """
    posting = Posting(
        posting_id=posting_id,
        transaction_id=transaction_id,
        account_id=account_id,
        posted_at=datetime(2026, 1, 1, tzinfo=UTC),
        amount=10.0,
        currency="USD",
        tag_ids=tag_ids,
        description="test",
    )
    frame = pl.DataFrame([posting.model_dump(mode="python")], schema=LEDGER_FRAME_SCHEMA)
    ingest_module._write_ledger(frame, db_session, user_id=DEFAULT_USER_ID)


def test_tag_rename_without_a_collision_just_renames(client) -> None:
    trip = client.post("/api/v1/accounting/tags", json={"name": "Trip"}).json()
    response = client.post(f"/api/v1/accounting/tags/{trip['tag_id']}/rename", json={"name": "Vacation"})
    assert response.status_code == 200
    body = response.json()
    assert body["merged"] is False
    assert body["tags"][trip["tag_id"]]["name"] == "Vacation"


def test_tag_rename_404s_for_an_unknown_tag(client) -> None:
    response = client.post("/api/v1/accounting/tags/tag:nope/rename", json={"name": "Anything"})
    assert response.status_code == 404


def test_tag_rename_merges_into_an_existing_tag_and_repoints_posting_tags(client, db_session) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    trip = client.post("/api/v1/accounting/tags", json={"name": "Trip"}).json()
    vacation = client.post("/api/v1/accounting/tags", json={"name": "Vacation"}).json()
    _write_posting_with_tags(db_session, account["account_id"], "p1", "t1", [trip["tag_id"]])

    response = client.post(f"/api/v1/accounting/tags/{trip['tag_id']}/rename", json={"name": "Vacation"})
    assert response.status_code == 200
    body = response.json()
    assert body["merged"] is True
    assert trip["tag_id"] not in body["tags"]
    assert vacation["tag_id"] in body["tags"]

    postings = _postings(client)
    p1 = next(p for p in postings if p["posting_id"] == "p1")
    assert p1["tag_ids"] == [vacation["tag_id"]]


def test_tag_rename_merge_handles_a_posting_already_tagged_with_both(client, db_session) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    trip = client.post("/api/v1/accounting/tags", json={"name": "Trip"}).json()
    vacation = client.post("/api/v1/accounting/tags", json={"name": "Vacation"}).json()
    _write_posting_with_tags(db_session, account["account_id"], "p1", "t1", [trip["tag_id"], vacation["tag_id"]])

    response = client.post(f"/api/v1/accounting/tags/{trip['tag_id']}/rename", json={"name": "Vacation"})
    assert response.status_code == 200

    postings = _postings(client)
    p1 = next(p for p in postings if p["posting_id"] == "p1")
    assert p1["tag_ids"] == [vacation["tag_id"]]


def test_tag_rename_merge_repoints_a_tag_ids_override(client, db_session) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    trip = client.post("/api/v1/accounting/tags", json={"name": "Trip"}).json()
    vacation = client.post("/api/v1/accounting/tags", json={"name": "Vacation"}).json()
    _write_posting_with_tags(db_session, account["account_id"], "p1", "t1", [])
    client.put("/api/v1/accounting/postings/p1/override", json={"tag_ids": [trip["tag_id"]]})

    client.post(f"/api/v1/accounting/tags/{trip['tag_id']}/rename", json={"name": "Vacation"})

    postings = _postings(client)
    p1 = next(p for p in postings if p["posting_id"] == "p1")
    assert p1["tag_ids"] == [vacation["tag_id"]]


def test_post_tag_creates_a_new_tag(client) -> None:
    response = client.post("/api/v1/accounting/tags", json={"name": "Trip"})
    assert response.status_code == 201
    body = response.json()
    assert body["tag_id"] == "tag:trip"
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json() == body
    store = client.get("/api/v1/accounting/store").json()
    assert "tag:trip" in store["tags"]


def test_getting_one_category_or_tag_404s_when_it_does_not_exist(client) -> None:
    """The item GETs the creates point at, on the path a `Location` must never take.

    Asserted for its own sake: `location_of` guarantees the *route* exists,
    never that the row does, so these two have to distinguish a missing row
    from a missing route rather than answering 200 with a null body.
    """
    assert client.get("/api/v1/accounting/categories/expense:nope").status_code == 404
    assert client.get("/api/v1/accounting/tags/tag:nope").status_code == 404


def test_post_tag_409s_on_a_duplicate_name_case_insensitive(client) -> None:
    client.post("/api/v1/accounting/tags", json={"name": "Trip"})
    response = client.post("/api/v1/accounting/tags", json={"name": "trip"})
    assert response.status_code == 409


def test_post_tag_allows_a_distinct_name(client) -> None:
    client.post("/api/v1/accounting/tags", json={"name": "Trip"})
    response = client.post("/api/v1/accounting/tags", json={"name": "Move"})
    assert response.status_code == 201


def test_delete_tag_removes_only_that_tag(client) -> None:
    client.post("/api/v1/accounting/tags", json={"name": "Trip"})
    client.post("/api/v1/accounting/tags", json={"name": "Move"})
    response = client.delete("/api/v1/accounting/tags/tag:trip")
    assert response.status_code == 204
    tags = client.get("/api/v1/accounting/store").json()["tags"]
    assert "tag:trip" not in tags
    assert "tag:move" in tags  # a delete of one tag never disturbs another


def test_delete_tag_that_is_already_gone_gets_404(client) -> None:
    assert client.delete("/api/v1/accounting/tags/tag:nope").status_code == 404


def test_tag_rename_preview_reports_no_merge_for_a_plain_rename(client) -> None:
    client.post("/api/v1/accounting/tags", json={"name": "Trip"})
    response = client.get("/api/v1/accounting/tags/tag:trip/rename-preview", params={"name": "Renamed"})
    assert response.status_code == 200
    body = response.json()
    assert body["will_merge"] is False
    assert body["target_name"] is None


def test_tag_rename_preview_404s_for_an_unknown_tag(client) -> None:
    response = client.get("/api/v1/accounting/tags/tag:nope/rename-preview", params={"name": "Anything"})
    assert response.status_code == 404


def test_tag_rename_preview_reports_merge_for_a_name_collision(client) -> None:
    client.post("/api/v1/accounting/tags", json={"name": "Trip"})
    client.post("/api/v1/accounting/tags", json={"name": "Vacation"})
    response = client.get("/api/v1/accounting/tags/tag:trip/rename-preview", params={"name": "Vacation"})
    assert response.status_code == 200
    body = response.json()
    assert body["will_merge"] is True
    assert body["target_name"] == "Vacation"


def test_deleting_the_last_real_subcategory_removes_the_other_catch_all_with_it(client) -> None:
    # The other half of the "Other" invariant: alone under its parent it means
    # nothing, so `normalize_categories` collapses it away — reached here by
    # deleting the only real sibling it was minted alongside.
    client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    client.post(
        "/api/v1/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#000000"}
    )
    assert "expense:custom:other" in client.get("/api/v1/accounting/store").json()["categories"]

    response = client.delete("/api/v1/accounting/categories/expense:custom:gadgets")

    assert response.status_code == 200
    assert "expense:custom:other" not in response.json()["categories"]
    assert "expense:custom:other" not in client.get("/api/v1/accounting/store").json()["categories"]


def test_post_other_asset_creates_one_with_a_server_generated_id(client) -> None:
    response = client.post("/api/v1/accounting/other-assets", json={"name": "Car", "value": 15000.0})
    assert response.status_code == 201
    asset = response.json()
    assert asset["asset_id"]
    assert asset["name"] == "Car"
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json() == asset
    body = client.get("/api/v1/accounting/net-worth").json()
    assert body["other_assets_total"] == pytest.approx(15000.0)


def test_delete_other_asset_removes_only_that_asset(client) -> None:
    car = client.post("/api/v1/accounting/other-assets", json={"name": "Car", "value": 15000.0}).json()
    boat = client.post("/api/v1/accounting/other-assets", json={"name": "Boat", "value": 5000.0}).json()
    response = client.delete(f"/api/v1/accounting/other-assets/{car['asset_id']}")
    assert response.status_code == 204
    remaining = {a["asset_id"] for a in client.get("/api/v1/accounting/store").json()["other_assets"]}
    assert remaining == {boat["asset_id"]}


def test_delete_other_asset_that_is_already_gone_gets_404(client) -> None:
    assert client.delete("/api/v1/accounting/other-assets/nope").status_code == 404


def test_post_other_asset_twice_with_identical_fields_creates_two_distinct_rows(client) -> None:
    first = client.post("/api/v1/accounting/other-assets", json={"name": "Car", "value": 15000.0}).json()
    second = client.post("/api/v1/accounting/other-assets", json={"name": "Car", "value": 15000.0}).json()
    assert first["asset_id"] != second["asset_id"]
    assert len(client.get("/api/v1/accounting/store").json()["other_assets"]) == 2


def test_budget_comparison_reflects_actual_spend(client) -> None:
    _import_chase_checking(client)
    postings = _postings(client)
    # By amount, not "the first negative leg": the +1500 payroll row's
    # placeholder counterparty leg is -1500 and also negative.
    payment = next(p for p in postings if p["amount"] == pytest.approx(-70.0))
    client.put(
        f"/api/v1/accounting/postings/{payment['posting_id']}/override", json={"category_id": "expense:admin-fees"}
    )

    response = client.post(
        "/api/v1/accounting/budgets", json={"month": "2026-06", "category_id": "expense:admin-fees", "amount": 100.0}
    )
    assert response.status_code == 201
    assert "expense:admin-fees" in [b["category_id"] for b in client.get("/api/v1/accounting/store").json()["budgets"]]

    comparison = client.get("/api/v1/accounting/budgets/comparison", params={"month": "2026-06"}).json()
    assert len(comparison) == 1
    assert comparison[0]["budgeted"] == pytest.approx(100.0)
    assert comparison[0]["actual"] == pytest.approx(70.0)


def test_get_budget_comparison_rejects_a_malformed_month(client) -> None:
    response = client.get("/api/v1/accounting/budgets/comparison", params={"month": "not-a-month"})
    assert response.status_code == 400


def test_post_budget_upserts_one_budget_without_touching_others(client) -> None:
    client.post(
        "/api/v1/accounting/budgets", json={"month": "2026-05", "category_id": "expense:transport", "amount": 40.0}
    )

    response = client.post(
        "/api/v1/accounting/budgets",
        json={"month": "2026-06", "category_id": "expense:food-drink", "amount": 300.0, "currency": "USD"},
    )

    assert response.status_code == 201
    created = response.json()
    assert created["budget_id"] == "2026-06:expense:food-drink"
    assert created["amount"] == pytest.approx(300.0)
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json() == created

    budgets = client.get("/api/v1/accounting/store").json()["budgets"]
    assert {b["budget_id"] for b in budgets} == {"2026-05:expense:transport", "2026-06:expense:food-drink"}


def test_post_budget_with_a_subcategory_includes_it_in_the_derived_id(client) -> None:
    response = client.post(
        "/api/v1/accounting/budgets",
        json={
            "month": "2026-06",
            "category_id": "expense:food-drink",
            "subcategory_id": "expense:food-drink:groceries",
            "amount": 200.0,
        },
    )
    assert response.json()["budget_id"] == "2026-06:expense:food-drink:expense:food-drink:groceries"


def test_post_budget_twice_for_the_same_key_replaces_rather_than_duplicates(client) -> None:
    client.post(
        "/api/v1/accounting/budgets", json={"month": "2026-06", "category_id": "expense:food-drink", "amount": 100.0}
    )
    client.post(
        "/api/v1/accounting/budgets", json={"month": "2026-06", "category_id": "expense:food-drink", "amount": 250.0}
    )

    budgets = client.get("/api/v1/accounting/store").json()["budgets"]
    matching = [b for b in budgets if b["budget_id"] == "2026-06:expense:food-drink"]
    assert len(matching) == 1
    assert matching[0]["amount"] == pytest.approx(250.0)


def test_delete_budget_removes_only_that_one(client) -> None:
    client.post(
        "/api/v1/accounting/budgets", json={"month": "2026-06", "category_id": "expense:food-drink", "amount": 100.0}
    )
    client.post(
        "/api/v1/accounting/budgets", json={"month": "2026-06", "category_id": "expense:transport", "amount": 50.0}
    )

    response = client.delete("/api/v1/accounting/budgets/2026-06:expense:food-drink")

    assert response.status_code == 204
    budgets = client.get("/api/v1/accounting/store").json()["budgets"]
    assert {b["budget_id"] for b in budgets} == {"2026-06:expense:transport"}


def test_delete_budget_404s_for_an_unknown_id(client) -> None:
    response = client.delete("/api/v1/accounting/budgets/does-not-exist")
    assert response.status_code == 404


def test_post_general_budget_upserts_one_without_touching_others(client) -> None:
    client.post("/api/v1/accounting/budgets", json={"category_id": "expense:transport", "amount": 40.0})

    response = client.post("/api/v1/accounting/budgets", json={"category_id": "expense:food-drink", "amount": 500.0})

    assert response.status_code == 201
    assert response.json()["amount"] == pytest.approx(500.0)
    assert response.json()["month"] is None
    budgets = client.get("/api/v1/accounting/store").json()["budgets"]
    assert {b["budget_id"] for b in budgets} == {":expense:transport", ":expense:food-drink"}


def test_post_general_budget_with_a_subcategory_includes_it_in_the_derived_id(client) -> None:
    response = client.post(
        "/api/v1/accounting/budgets",
        json={"category_id": "expense:food-drink", "subcategory_id": "expense:food-drink:groceries", "amount": 200.0},
    )
    assert response.status_code == 201
    assert response.json()["budget_id"] == ":expense:food-drink:expense:food-drink:groceries"


def test_post_general_budget_does_not_overwrite_the_same_categorys_month_budget(client) -> None:
    # One table, one list — `month: null` is the general target and coexists
    # with the month one for the same category rather than replacing it, which
    # is exactly what `budget_row_key` putting the month first encodes.
    client.post(
        "/api/v1/accounting/budgets", json={"month": "2026-06", "category_id": "expense:food-drink", "amount": 100.0}
    )
    client.post("/api/v1/accounting/budgets", json={"category_id": "expense:food-drink", "amount": 500.0})

    budgets = client.get("/api/v1/accounting/store").json()["budgets"]
    assert {b["budget_id"] for b in budgets} == {"2026-06:expense:food-drink", ":expense:food-drink"}


def test_delete_general_budget_removes_only_that_one(client) -> None:
    client.post("/api/v1/accounting/budgets", json={"category_id": "expense:food-drink", "amount": 500.0})
    client.post("/api/v1/accounting/budgets", json={"category_id": "expense:transport", "amount": 40.0})

    response = client.delete("/api/v1/accounting/budgets/:expense:food-drink")

    assert response.status_code == 204
    budgets = client.get("/api/v1/accounting/store").json()["budgets"]
    assert {b["budget_id"] for b in budgets} == {":expense:transport"}


def test_get_suggested_budget_amount_returns_zero_with_no_history(client) -> None:
    response = client.get(
        "/api/v1/accounting/budgets/suggested-amount", params={"category_id": "expense:food-drink", "month": "2026-06"}
    )
    assert response.status_code == 200
    assert response.json()["suggested_amount"] == pytest.approx(0.0)


def test_rebuild_with_nothing_imported_is_a_404(client) -> None:
    assert client.post("/api/v1/accounting/rebuild").status_code == 404


def test_rebuild_reconstructs_from_raw_after_import(client) -> None:
    _import_chase_checking(client)
    response = client.post("/api/v1/accounting/rebuild")
    assert response.status_code == 200


def test_get_currencies_lists_usd_and_eur(client) -> None:
    body = client.get("/api/v1/accounting/currencies").json()
    assert {currency["code"] for currency in body} == {"USD", "EUR"}


def _fake_rate_history():
    today = date.today()
    return pl.DataFrame(
        {
            "date": [today - timedelta(days=1), today],
            "currency": ["EUR", "EUR"],
            "rate_to_base": [1.9, 2.1],
        },
        schema=RATE_HISTORY_SCHEMA,
    )


def _mock_fetch(monkeypatch) -> None:
    monkeypatch.setattr(
        exchange_rates, "fetch_rate_history", lambda config, history_years=2, session=None: _fake_rate_history()
    )


def test_net_worth_succeeds_for_a_eur_account_once_rates_are_synced(client, monkeypatch) -> None:
    _mock_fetch(monkeypatch)
    exchange_rates.update_rate_history_cache(accounting_api.state.config)
    _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
    response = client.get("/api/v1/accounting/net-worth")
    assert response.status_code == 200


def test_net_worth_400s_when_a_needed_currency_was_never_synced(client) -> None:
    _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
    response = client.get("/api/v1/accounting/net-worth")
    assert response.status_code == 400


def test_get_current_exchange_rate_after_sync(client, monkeypatch) -> None:
    _mock_fetch(monkeypatch)
    exchange_rates.update_rate_history_cache(accounting_api.state.config)
    response = client.get("/api/v1/accounting/exchange-rates/current", params={"currency": "EUR"})
    assert response.status_code == 200
    body = response.json()
    assert body["rate_to_base"] == pytest.approx(2.0)
    assert body["base_currency"] == "USD"


def test_get_exchange_rate_history_after_sync(client, monkeypatch) -> None:
    _mock_fetch(monkeypatch)
    exchange_rates.update_rate_history_cache(accounting_api.state.config)
    response = client.get("/api/v1/accounting/exchange-rates/history", params={"currency": "EUR"})
    assert response.status_code == 200
    body = response.json()
    assert [row["rate"] for row in body] == pytest.approx([1.9, 2.1])


def test_post_account_creates_a_new_account(client) -> None:
    response = client.post(
        "/api/v1/accounting/accounts",
        json={"name": "BNP Checking", "kind": "checking", "institution": "BNP", "currency": "EUR"},
    )
    assert response.status_code == 201
    account_id = response.json()["account_id"]
    assert account_id
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json()["account_id"] == account_id
    assert account_id in client.get("/api/v1/accounting/store").json()["accounts"]


def test_post_account_twice_with_no_last_four_creates_two_distinct_accounts(client) -> None:
    first = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
    second = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
    assert first["account_id"] != second["account_id"]

    accounts = client.get("/api/v1/accounting/store").json()["accounts"]
    assert first["account_id"] in accounts
    assert second["account_id"] in accounts


def test_accounts_are_isolated_between_users(client, db_session) -> None:
    """Proof that `accounts.py`'s account CRUD is genuinely per-user, not a shared global store.

    Regression test for the FK-violation/cross-user-leak sweep: before every
    endpoint threaded a real `user_id` through its own repository calls,
    this router had no way to keep two users' accounts apart at all.
    """
    other_user_id = uuid.uuid4()
    db_session.add(dbm.User(id=other_user_id, email="other@example.com"))
    db_session.commit()

    account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
    assert account["account_id"] in client.get("/api/v1/accounting/store").json()["accounts"]

    trades_api.app.dependency_overrides[get_current_user_id] = lambda: other_user_id
    try:
        # The first user's account must not be visible to the second user...
        other_store = client.get("/api/v1/accounting/store").json()
        assert account["account_id"] not in other_store["accounts"]

        # ...and the second user is free to register their own account too,
        # getting back its own distinct, server-generated id.
        other_account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
        assert other_account["account_id"] != account["account_id"]
        assert other_account["account_id"] in client.get("/api/v1/accounting/store").json()["accounts"]
    finally:
        trades_api.app.dependency_overrides[get_current_user_id] = lambda: DEFAULT_USER_ID

    # Back as the first user, their own account is unaffected by the second
    # user's account, and still the only one they can see.
    assert account["account_id"] in client.get("/api/v1/accounting/store").json()["accounts"]


def test_put_account_fully_edits_an_account_with_no_postings(client) -> None:
    account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
    response = client.put(
        f"/api/v1/accounting/accounts/{account['account_id']}",
        json={"name": "BNP Main", "institution": "BNP Paribas", "kind": "savings", "currency": "USD"},
    )
    assert response.status_code == 200
    updated = client.get("/api/v1/accounting/store").json()["accounts"][account["account_id"]]
    assert updated["institution"] == "BNP Paribas"
    assert updated["kind"] == "savings"
    assert updated["currency"] == "USD"


def test_put_account_updates_last_four_independently_and_it_round_trips_through_store(client) -> None:
    account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="USD")
    assert account["last_four"] is None

    response = client.put(
        f"/api/v1/accounting/accounts/{account['account_id']}",
        json={"name": "BNP Checking", "institution": "BNP", "kind": "checking", "currency": "USD", "last_four": "4321"},
    )
    assert response.status_code == 200
    assert response.json()["last_four"] == "4321"

    store = client.get("/api/v1/accounting/store").json()
    assert store["accounts"][account["account_id"]]["last_four"] == "4321"


def test_put_account_blocks_locked_field_changes_once_it_has_postings(client) -> None:
    account_id = _import_chase_checking(client)
    response = client.put(
        f"/api/v1/accounting/accounts/{account_id}",
        json={"name": "Chase Checking", "institution": "Chase", "kind": "savings", "currency": "USD"},
    )
    assert response.status_code == 400

    renamed = client.put(
        f"/api/v1/accounting/accounts/{account_id}",
        json={"name": "Renamed", "institution": "Chase", "kind": "checking", "currency": "USD"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"


def test_post_account_accepts_an_external_investment_pulling_from_trades(client, broker_connection_id) -> None:
    response = client.post(
        "/api/v1/accounting/accounts",
        json={
            "name": "Interactive Brokers",
            "kind": "external_investment",
            "institution": "external",
            "currency": "USD",
            "broker_connection_id": str(broker_connection_id),
        },
    )
    assert response.status_code == 201
    account_id = response.json()["account_id"]
    created = client.get("/api/v1/accounting/store").json()["accounts"][account_id]
    assert created["broker_connection_id"] == str(broker_connection_id)


def test_post_account_accepts_a_manually_tracked_external_investment(client) -> None:
    response = client.post(
        "/api/v1/accounting/accounts",
        json={"name": "Friend's Fund", "kind": "external_investment", "institution": "external", "currency": "USD"},
    )
    assert response.status_code == 201
    account_id = response.json()["account_id"]
    created = client.get("/api/v1/accounting/store").json()["accounts"][account_id]
    assert created["broker_connection_id"] is None


def test_post_account_rejects_a_broker_connection_that_does_not_exist(client) -> None:
    """DB-audit move #1: the seam is a real foreign key, so an account can't name a connection nobody has."""
    response = client.post(
        "/api/v1/accounting/accounts",
        json={
            "name": "Interactive Brokers",
            "kind": "external_investment",
            "institution": "external",
            "currency": "USD",
            "broker_connection_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 404
    assert "broker connection" in response.json()["detail"].lower()


def test_post_account_rejects_a_broker_link_on_a_non_investment_account(client, broker_connection_id) -> None:
    """The `CHECK` that makes "my checking account mirrors a brokerage" unrepresentable."""
    response = client.post(
        "/api/v1/accounting/accounts",
        json={
            "name": "Chase Checking",
            "kind": "checking",
            "institution": "Chase",
            "currency": "USD",
            "broker_connection_id": str(broker_connection_id),
        },
    )
    assert response.status_code == 400


def test_put_account_can_switch_an_external_investment_between_trades_and_manual(client, broker_connection_id) -> None:
    account = _create_account(
        client, name="Friend's Fund", kind="external_investment", institution="external", currency="USD"
    )
    response = client.put(
        f"/api/v1/accounting/accounts/{account['account_id']}",
        json={
            "name": "Friend's Fund",
            "institution": "external",
            "kind": "external_investment",
            "currency": "USD",
            "broker_connection_id": str(broker_connection_id),
        },
    )
    assert response.status_code == 200
    assert response.json()["broker_connection_id"] == str(broker_connection_id)

    back_to_manual = client.put(
        f"/api/v1/accounting/accounts/{account['account_id']}",
        json={"name": "Friend's Fund", "institution": "external", "kind": "external_investment", "currency": "USD"},
    )
    assert back_to_manual.status_code == 200
    assert back_to_manual.json()["broker_connection_id"] is None


def test_ledger_export_returns_every_raw_posting_unresolved_by_rules(client) -> None:
    account_id = _import_chase_checking(client)
    # A rule that would repoint the payroll deposit's placeholder leg to a
    # real account — the raw ledger export must stay unaffected by it,
    # unlike `GET /postings` (the resolved view `TransactionsTab` shows).
    client.post(
        "/api/v1/accounting/transfer-rules",
        json={"description_contains": "SOME EMPLOYER PAYROLL", "counterparty_account_id": account_id},
    )
    body = _ledger_export(client)
    assert len(body) == 4  # two transactions, two postings each
    placeholder_legs = [row for row in body if row["account_id"] == "uncategorized:income"]
    assert len(placeholder_legs) == 1  # still on the placeholder — the rule never touched this export


def test_accounting_statements_export_returns_a_zip_of_every_raw_upload(client) -> None:
    _import_chase_checking(client)
    response = client.get("/api/v1/accounting/statements/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    zip_file = zipfile.ZipFile(io.BytesIO(response.content))
    assert any(name.endswith(".csv") for name in zip_file.namelist())


def test_get_supported_import_kinds_lists_registered_standardizers(client) -> None:
    response = client.get("/api/v1/accounting/supported-import-kinds")
    assert response.status_code == 200
    pairs = {(row["institution"], row["account_kind"]) for row in response.json()}
    assert ("Chase", "checking") in pairs
    assert ("BNP", "checking") not in pairs


def test_put_opening_balance_is_reflected_in_net_worth(client) -> None:
    account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="USD")
    response = client.put(
        f"/api/v1/accounting/accounts/{account['account_id']}/opening-balance",
        json={"account_id": account["account_id"], "amount": 250.0, "as_of_date": "2026-01-01T00:00:00"},
    )
    assert response.status_code == 200
    net_worth = client.get("/api/v1/accounting/net-worth", params={"as_of": "2026-06-01"}).json()
    row = next(r for r in net_worth["accounts"] if r["account_id"] == account["account_id"])
    assert row["balance"] == pytest.approx(250.0)

    delete_response = client.delete(f"/api/v1/accounting/accounts/{account['account_id']}/opening-balance")
    assert delete_response.status_code == 204
    net_worth_after = client.get("/api/v1/accounting/net-worth", params={"as_of": "2026-06-01"}).json()
    row_after = next(r for r in net_worth_after["accounts"] if r["account_id"] == account["account_id"])
    assert row_after["balance"] == pytest.approx(0.0)


def test_net_worth_history_by_account_returns_a_row_per_account_per_date(client) -> None:
    account_id = _import_chase_checking(client)
    response = client.get(
        "/api/v1/accounting/net-worth/history/by-account",
        params={"start": "2026-06-28", "end": "2026-06-30", "interval_days": 1},
    )
    assert response.status_code == 200
    rows = response.json()
    dates = {row["date"] for row in rows if row["account_id"] == account_id}
    assert dates == {"2026-06-28", "2026-06-29", "2026-06-30"}


def test_delete_account_removes_an_account_with_no_postings(client) -> None:
    account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
    response = client.delete(f"/api/v1/accounting/accounts/{account['account_id']}")
    assert response.status_code == 204
    assert account["account_id"] not in client.get("/api/v1/accounting/store").json()["accounts"]


def test_delete_account_blocked_once_it_has_postings(client) -> None:
    account_id = _import_chase_checking(client)
    response = client.delete(f"/api/v1/accounting/accounts/{account_id}")
    assert response.status_code == 400
    assert account_id in client.get("/api/v1/accounting/store").json()["accounts"]


def test_close_account_marks_it_closed_with_no_transfers(client) -> None:
    account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="USD")
    response = client.post(f"/api/v1/accounting/accounts/{account['account_id']}/close", json={"transfers": []})
    assert response.status_code == 200
    assert client.get("/api/v1/accounting/store").json()["accounts"][account["account_id"]]["closed"] is True


def test_close_account_records_a_transfer_that_shows_up_as_real_postings(client, db_session) -> None:
    checking = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="USD")
    savings = _create_account(client, name="BNP Savings", kind="savings", institution="BNP", currency="USD")
    response = client.post(
        f"/api/v1/accounting/accounts/{checking['account_id']}/close",
        json={
            "transfers": [
                {
                    "transfer_id": "close-bnp-checking-0001",
                    "date": "2026-06-30T00:00:00",
                    "from_account_id": checking["account_id"],
                    "to_account_id": savings["account_id"],
                    "from_amount": 100.0,
                    "to_amount": 100.0,
                    "description": "Closing out BNP checking",
                }
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["manual_transfers"] == [
        {
            "transfer_id": "close-bnp-checking-0001",
            "date": "2026-06-30T00:00:00",
            "from_account_id": checking["account_id"],
            "to_account_id": savings["account_id"],
            "from_amount": 100.0,
            "to_amount": 100.0,
            "description": "Closing out BNP checking",
        }
    ]
    assert client.get("/api/v1/accounting/store").json()["accounts"][checking["account_id"]]["closed"] is True
    # `GET /store` doesn't carry `manual_transfers` — they're only ever an
    # implementation detail of a close or an opening balance, never a
    # collection the client reads — so the stored rows are read directly.
    assert len(load_manual_transfers(db_session, DEFAULT_USER_ID)) == 1

    net_worth = client.get("/api/v1/accounting/net-worth", params={"as_of": "2026-07-01"}).json()
    balances = {row["account_id"]: row["balance"] for row in net_worth["accounts"]}
    assert balances[checking["account_id"]] == pytest.approx(-100.0)
    assert balances[savings["account_id"]] == pytest.approx(100.0)


def test_close_account_records_the_transfer_as_a_manual_origin_transaction_in_the_raw_ledger(client) -> None:
    """A manual transfer is a real transaction now, not a mini-ledger replayed over one.

    `GET /ledger/export` is the raw ledger, before any overlay stage runs
    — the two legs showing up there is what proves they are stored rows
    rather than something the resolution pipeline synthesized.
    """
    checking = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="USD")
    savings = _create_account(client, name="BNP Savings", kind="savings", institution="BNP", currency="USD")
    client.post(
        f"/api/v1/accounting/accounts/{checking['account_id']}/close",
        json={
            "transfers": [
                {
                    "transfer_id": "close-bnp-checking-0001",
                    "date": "2026-06-30T00:00:00",
                    "from_account_id": checking["account_id"],
                    "to_account_id": savings["account_id"],
                    "from_amount": 100.0,
                    "to_amount": 100.0,
                    "description": "Closing out BNP checking",
                }
            ]
        },
    )

    raw = _ledger_export(client)
    legs = {posting["posting_id"]: posting for posting in raw}
    assert legs["manual-transfer:close-bnp-checking-0001:from"]["amount"] == pytest.approx(-100.0)
    assert legs["manual-transfer:close-bnp-checking-0001:to"]["amount"] == pytest.approx(100.0)
    assert legs["manual-transfer:close-bnp-checking-0001:from"]["transaction_id"] == (
        "manual-transfer:close-bnp-checking-0001"
    )


def test_close_account_rejects_a_transfer_whose_from_account_doesnt_match(client) -> None:
    checking = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="USD")
    savings = _create_account(client, name="BNP Savings", kind="savings", institution="BNP", currency="USD")
    response = client.post(
        f"/api/v1/accounting/accounts/{checking['account_id']}/close",
        json={
            "transfers": [
                {
                    "transfer_id": "close-bnp-checking-0001",
                    "date": "2026-06-30T00:00:00",
                    "from_account_id": savings["account_id"],
                    "to_account_id": checking["account_id"],
                    "from_amount": 100.0,
                    "to_amount": 100.0,
                }
            ]
        },
    )
    assert response.status_code == 400


def test_close_account_rejects_a_transfer_to_an_unknown_account(client) -> None:
    checking = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="USD")
    response = client.post(
        f"/api/v1/accounting/accounts/{checking['account_id']}/close",
        json={
            "transfers": [
                {
                    "transfer_id": "close-bnp-checking-0001",
                    "date": "2026-06-30T00:00:00",
                    "from_account_id": checking["account_id"],
                    "to_account_id": "does-not-exist",
                    "from_amount": 100.0,
                    "to_amount": 100.0,
                }
            ]
        },
    )
    assert response.status_code == 400


def test_reopen_account_clears_the_closed_flag_but_keeps_recorded_transfers(client) -> None:
    account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="USD")
    client.post(f"/api/v1/accounting/accounts/{account['account_id']}/close", json={"transfers": []})
    response = client.post(f"/api/v1/accounting/accounts/{account['account_id']}/reopen")
    assert response.status_code == 200
    assert client.get("/api/v1/accounting/store").json()["accounts"][account["account_id"]]["closed"] is False


def test_net_worth_history_returns_one_point_per_interval(client) -> None:
    _import_chase_checking(client)
    response = client.get(
        "/api/v1/accounting/net-worth/history", params={"start": "2026-06-29", "end": "2026-06-30", "interval_days": 1}
    )
    assert response.status_code == 200
    points = response.json()
    assert [point["date"] for point in points] == ["2026-06-29", "2026-06-30"]
    assert points[-1]["net_worth"] == pytest.approx(1430.0)


def test_category_totals_buckets_uncategorized_payroll_as_income(client) -> None:
    _import_chase_checking(client)
    response = client.get(
        "/api/v1/accounting/income-statement/category-totals", params={"start": "2026-06-01", "end": "2026-06-30"}
    )
    assert response.status_code == 200
    totals = response.json()
    income_row = next(row for row in totals if row["classification"] == "income")
    assert income_row["amount"] == pytest.approx(1500.0)


def test_category_totals_excludes_unconfirmed_pending_suggestions(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = _postings(client)
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    fake_response = '{"category_id": "income:salary", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )
    client.post(f"/api/v1/accounting/postings/{payroll['posting_id']}/ai-suggest-category")

    response = client.get(
        "/api/v1/accounting/income-statement/category-totals", params={"start": "2026-06-01", "end": "2026-06-30"}
    )
    totals = response.json()
    assert all(row["category_id"] != "income:salary" for row in totals)
    income_row = next(row for row in totals if row["classification"] == "income")
    assert income_row["category_name"] == "Uncategorized"
    assert income_row["amount"] == pytest.approx(1500.0)

    client.post("/api/v1/accounting/postings/validate-pending", json={"posting_ids": [payroll["posting_id"]]})
    response = client.get(
        "/api/v1/accounting/income-statement/category-totals", params={"start": "2026-06-01", "end": "2026-06-30"}
    )
    totals = response.json()
    salary_row = next(row for row in totals if row["category_id"] == "income:salary")
    assert salary_row["amount"] == pytest.approx(1500.0)


def test_get_simulator_projection_computes_compound_growth(client) -> None:
    response = client.get(
        "/api/v1/accounting/simulator/project",
        params={"initial_capital": 1000.0, "monthly_contribution": 0.0, "horizon_years": 1, "annual_rate_pct": 12.0},
    )
    assert response.status_code == 200
    points = response.json()
    assert points[0]["balance"] == pytest.approx(1000.0)
    assert points[12]["balance"] == pytest.approx(1000.0 * (1.01**12))


def test_post_simulator_scenario_creates_one_with_a_server_generated_id(client) -> None:
    response = client.post(
        "/api/v1/accounting/simulator/scenarios",
        json={
            "name": "Base case",
            "initial_capital": 1000.0,
            "monthly_contribution": 100.0,
            "horizon_years": 10,
            "annual_rate_pct": 6.0,
        },
    )
    assert response.status_code == 201
    scenario = response.json()
    assert scenario["scenario_id"]
    assert scenario["name"] == "Base case"
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json() == scenario


def test_post_simulator_scenario_twice_with_identical_fields_creates_two_distinct_rows(client) -> None:
    payload = {
        "name": "Base case",
        "initial_capital": 1000.0,
        "monthly_contribution": 100.0,
        "horizon_years": 10,
        "annual_rate_pct": 6.0,
    }
    first = client.post("/api/v1/accounting/simulator/scenarios", json=payload).json()
    second = client.post("/api/v1/accounting/simulator/scenarios", json=payload).json()
    assert first["scenario_id"] != second["scenario_id"]
    assert len(client.get("/api/v1/accounting/store").json()["simulator_scenarios"]) == 2


def test_delete_simulator_scenario_removes_only_that_scenario(client) -> None:
    payload = {
        "name": "Base case",
        "initial_capital": 1000.0,
        "monthly_contribution": 100.0,
        "horizon_years": 10,
        "annual_rate_pct": 6.0,
    }
    first = client.post("/api/v1/accounting/simulator/scenarios", json=payload).json()
    second = client.post("/api/v1/accounting/simulator/scenarios", json=payload).json()
    response = client.delete(f"/api/v1/accounting/simulator/scenarios/{first['scenario_id']}")
    assert response.status_code == 204
    remaining = {s["scenario_id"] for s in client.get("/api/v1/accounting/store").json()["simulator_scenarios"]}
    assert remaining == {second["scenario_id"]}


def test_delete_simulator_scenario_that_is_already_gone_gets_404(client) -> None:
    assert client.delete("/api/v1/accounting/simulator/scenarios/nope").status_code == 404


def test_interest_summary_reports_savings_interest_earned(client, db_session) -> None:
    account = _create_account(client, name="SoFi Savings", kind="savings", institution="SoFi")
    # Seeded directly as a posting, not through a real importer — this test
    # is checking that the interest-summary endpoint correctly aggregates an
    # already-categorized "Interest Earned" posting, not exercising SoFi
    # statement parsing.
    interest_posting = Posting(
        posting_id="p1",
        transaction_id="t1",
        account_id=account["account_id"],
        posted_at=datetime(2026, 4, 30),
        amount=7.70,
        currency="USD",
        category_id="income:interest-earned",
        subcategory_id=None,
        tag_ids=[],
        description="Interest earned",
        meta={},
    )
    frame = pl.DataFrame([interest_posting.model_dump(mode="python")], schema=LEDGER_FRAME_SCHEMA)
    ingest_module._write_ledger(frame, db_session, user_id=DEFAULT_USER_ID)

    response = client.get("/api/v1/accounting/interest-summary", params={"as_of": "2026-04-30"})
    assert response.status_code == 200
    rows = response.json()
    savings_row = next(row for row in rows if row["account_id"] == account["account_id"])
    assert savings_row["interest_earned_this_year"] == pytest.approx(7.70)
    # The seeded ledger has only this one posting, so the account's
    # running balance is the interest posting alone.
    assert savings_row["current_balance"] == pytest.approx(7.70)


def test_monthly_income_expense_reports_both_sides(client) -> None:
    _import_chase_checking(client)
    response = client.get(
        "/api/v1/accounting/income-statement/monthly", params={"start": "2026-06-01", "end": "2026-06-30"}
    )
    assert response.status_code == 200
    month = response.json()[0]
    assert month["income"] == pytest.approx(1500.0)
    assert month["expense"] == pytest.approx(70.0)


def test_get_store_carries_no_store_wide_version(client) -> None:
    """There is no whole-store counter left — optimistic concurrency is per-row, on the rows that need it."""
    response = client.get("/api/v1/accounting/store")
    assert response.status_code == 200
    assert "version" not in response.json()


def test_two_unrelated_writes_both_land_without_conflicting(client) -> None:
    """Nothing store-wide can make two genuinely unrelated edits conflict with each other.

    Every write is either scoped to the rows a request names or guarded by its own row version
    (`PATCH /transfer-rules/{rule_id}` and friends), so a second create against a store another
    create already changed simply succeeds.
    """
    client.post(
        "/api/v1/accounting/categories", json={"name": "Other", "classification": "expense", "color": "#111111"}
    )

    response = client.post(
        "/api/v1/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    assert response.status_code == 201
    assert {"expense:custom", "expense:other"} <= set(client.get("/api/v1/accounting/store").json()["categories"])


def test_posting_the_same_upsert_twice_reports_created_then_replaced(client) -> None:
    """The one distinction a blanket `201` would erase, on all three content-derived-id upserts.

    Each of these routes creates on the first call and replaces on the
    second, at the same server-derived id. A `201` on the second would
    claim a row came into existence that was already there — and would
    carry a `Location` for a resource this call did not create.
    """
    pattern_body = {"description_contains": "NETFLIX", "category_id": "expense:subscriptions"}
    first = client.post("/api/v1/accounting/category-patterns", json=pattern_body)
    second = client.post("/api/v1/accounting/category-patterns", json=pattern_body)
    assert (first.status_code, second.status_code) == (201, 200)
    assert "Location" in first.headers
    assert "Location" not in second.headers
    assert second.json()["pattern_id"] == first.json()["pattern_id"]

    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    rule_body = {"description_contains": "PAYROLL", "counterparty_account_id": employer["account_id"]}
    first = client.post("/api/v1/accounting/transfer-rules", json=rule_body)
    second = client.post("/api/v1/accounting/transfer-rules", json=rule_body)
    assert (first.status_code, second.status_code) == (201, 200)
    assert "Location" in first.headers
    assert "Location" not in second.headers

    budget_body = {"month": "2026-06", "category_id": "expense:food-drink", "amount": 300.0, "currency": "USD"}
    first = client.post("/api/v1/accounting/budgets", json=budget_body)
    second = client.post("/api/v1/accounting/budgets", json={**budget_body, "amount": 400.0})
    assert (first.status_code, second.status_code) == (201, 200)
    assert "Location" in first.headers
    assert "Location" not in second.headers
    assert second.json()["amount"] == pytest.approx(400.0)
