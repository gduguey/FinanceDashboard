import io
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest
import xlsxwriter
from fastapi.testclient import TestClient

import db.models as dbm
from accounting import api as accounting_api
from accounting.api.routers import imports as accounting_imports_router
from accounting.api.routers import llm as accounting_llm_router
from accounting.config import AccountingConfig
from accounting.db.llm import LLMUsage
from accounting.importers import ingest as ingest_module
from accounting.market_data import exchange_rates
from accounting.models import Posting
from accounting.market_data.exchange_rates import RATE_HISTORY_SCHEMA
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
    db_session.add(dbm.User(id=DEFAULT_USER_ID, email="default@example.com", hashed_password="unset"))  # noqa: S106
    db_session.commit()

    def _override_get_db():
        yield db_session

    trades_api.app.dependency_overrides[get_db] = _override_get_db
    yield
    trades_api.app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client():
    return TestClient(trades_api.app)


def test_get_store_seeds_default_categories_and_placeholder_accounts(client) -> None:
    body = client.get("/api/accounting/store").json()
    assert "expense:food-drink" in body["categories"]
    assert "uncategorized:expense" in body["accounts"]
    assert body["transfer_rules"] == []


def test_detect_returns_a_guess_for_a_known_shape(client) -> None:
    body = client.post(
        "/api/accounting/detect",
        json={
            "header": ["Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #"],
            "filename": "Chase9579_Activity_20260704.CSV",
        },
    ).json()
    assert body["institution"] == "Chase"
    assert body["account_kind"] == "checking"


def test_detect_returns_none_for_an_unknown_shape(client) -> None:
    body = client.post("/api/accounting/detect", json={"header": ["A", "B"], "filename": "x.csv"}).json()
    assert body is None


def test_import_against_an_unknown_account_id_is_a_422(client) -> None:
    response = client.post(
        "/api/accounting/import",
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
        "/api/accounting/import",
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

    store = client.get("/api/accounting/store").json()
    assert account["account_id"] in store["accounts"]


def test_import_unsupported_institution_is_a_400(client) -> None:
    account = _create_account(client, name="BoA Checking", kind="checking", institution="BankOfAmerica")
    response = client.post(
        "/api/accounting/import",
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
        "/api/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": "does-not-exist",
            "account_name": "Generic Checking",
        },
    )
    assert response.status_code == 422


def test_canonical_import_registers_a_new_account_and_creates_a_category(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = (
        "Date,Description,Amount,Category\n2026-06-30,Grocery Store,-42.50,Groceries\n2026-06-29,Paycheck,1500.00,\n"
    )
    response = client.post(
        "/api/accounting/import/canonical",
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

    store = client.get("/api/accounting/store").json()
    assert account["account_id"] in store["accounts"]
    assert any(category["name"] == "Groceries" for category in store["categories"].values())


def test_canonical_import_handles_a_utf8_bom_prefixed_file(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = "﻿Date,Description,Amount\n2026-06-30,Grocery Store,-42.50\n"
    response = client.post(
        "/api/accounting/import/canonical",
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
        "/api/accounting/import/canonical",
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
        "/api/accounting/import/canonical",
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
    postings = client.get("/api/accounting/postings").json()
    real_leg = next(p for p in postings if p["account_id"] == account["account_id"])
    assert real_leg["posted_at"].startswith("2026-12-01")


def test_canonical_import_preview_does_not_persist_anything(client) -> None:
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Grocery Store,-42.50,Groceries\n"
    response = client.post(
        "/api/accounting/import/canonical/preview",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={"account_id": "generic-bank:checking:0005"},
    )
    assert response.status_code == 200
    assert [category["name"] for category in response.json()["new_categories"]] == ["Groceries"]

    store = client.get("/api/accounting/store").json()
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
        "/api/accounting/import/canonical",
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

    store = client.get("/api/accounting/store").json()
    food_categories = [category for category in store["categories"].values() if category["name"] == "Food"]
    assert len(food_categories) == 1


def test_canonical_import_returns_a_422_for_an_unparseable_file(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    response = client.post(
        "/api/accounting/import/canonical",
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
    response = client.post("/api/accounting/accounts", json=payload)
    assert response.status_code == 200
    return response.json()


def _import_chase_checking(client, account_id: str | None = None, csv_text: str = CHASE_CHECKING_CSV) -> str:
    if account_id is None:
        account_id = _create_account(
            client, name="Chase Checking", kind="checking", institution="Chase", last_four="9579"
        )["account_id"]
    response = client.post(
        "/api/accounting/import",
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
        "/api/accounting/import",
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
        "/api/accounting/import/categorize-from-file/preview",
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
        "/api/accounting/import/categorize-from-file/preview",
        files={"file": ("my-sheet.csv", sheet_csv, "text/csv")},
    )
    postings = client.get("/api/accounting/postings").json()
    real_leg = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    assert real_leg["category_id"] is None

    store = client.get("/api/accounting/store").json()
    assert not any(category["name"] == "Freelance Gig Income" for category in store["categories"].values())


def test_categorize_from_file_preview_reports_an_unmatched_row(client) -> None:
    _import_chase_checking(client)
    sheet_csv = "Date,Description,Amount,Category\n01/15/2026,Some Unrelated Purchase,-999.99,Shopping\n"
    response = client.post(
        "/api/accounting/import/categorize-from-file/preview",
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
        "/api/accounting/import/categorize-from-file/preview",
        files={"file": ("my-sheet.csv", sheet_csv, "text/csv")},
    ).json()
    row_number = preview["matches"][0]["row_number"]

    response = client.post(
        "/api/accounting/import/categorize-from-file/apply",
        files={"file": ("my-sheet.csv", sheet_csv, "text/csv")},
        data={"confirmed_row_numbers": f"[{row_number}]"},
    )
    assert response.status_code == 200
    assert response.json()["updated_posting_count"] == 1

    postings = client.get("/api/accounting/postings").json()
    real_leg = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    assert real_leg["category_id"] is not None
    store = client.get("/api/accounting/store").json()
    assert store["categories"][real_leg["category_id"]]["name"] == "Salary"

    # Never creates a new transaction — same two postings as right after the original import.
    assert len(postings) == 4


def test_categorize_from_file_apply_skips_rows_not_confirmed(client) -> None:
    account_id = _import_chase_checking(client)
    sheet_csv = (
        "Date,Description,Amount,Category\n"
        "06/30/2026,Payroll,1500.00,Salary\n"
        "06/29/2026,Payment to Chase card ending in 1234 06/29,-70.00,Credit Card Payment\n"
    )
    client.post(
        "/api/accounting/import/categorize-from-file/apply",
        files={"file": ("my-sheet.csv", sheet_csv, "text/csv")},
        data={"confirmed_row_numbers": "[2]"},  # only the payroll row (header is row 1, so first data row is row 2)
    )
    postings = client.get("/api/accounting/postings").json()
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
        "/api/accounting/import/categorize-from-file/preview",
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
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    assert payroll["category_id"] is None  # generic payroll text doesn't match the EQORE-specific seed rule


def test_manual_override_wins_over_no_rule_match(client) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    response = client.put(
        f"/api/accounting/postings/{payroll['posting_id']}/override", json={"category_id": "income:salary"}
    )
    assert response.status_code == 200

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"


def test_setting_a_subcategory_after_a_category_preserves_the_category(client) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    posting_id = payroll["posting_id"]

    client.put(f"/api/accounting/postings/{posting_id}/override", json={"category_id": "income:salary"})
    response = client.put(f"/api/accounting/postings/{posting_id}/override", json={"subcategory_id": "income:bonus"})
    assert response.status_code == 200
    assert response.json()["category_id"] == "income:salary"
    assert response.json()["subcategory_id"] == "income:bonus"

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["posting_id"] == posting_id)
    assert updated_payroll["category_id"] == "income:salary"
    assert updated_payroll["subcategory_id"] == "income:bonus"


def test_put_posting_split_replaces_one_posting_with_categorized_legs(client) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    response = client.put(
        f"/api/accounting/postings/{payroll['posting_id']}/split",
        json=[
            {"amount": 1400.0, "category_id": "income:salary", "description": "Wage"},
            {"amount": 100.0, "category_id": "income:reimbursement", "description": "Expense reimbursement"},
        ],
    )
    assert response.status_code == 200

    updated = client.get("/api/accounting/postings").json()
    assert not any(p["posting_id"] == payroll["posting_id"] for p in updated)
    legs = [p for p in updated if p["posting_id"].startswith(f"{payroll['posting_id']}:split:")]
    assert sorted(leg["amount"] for leg in legs) == pytest.approx([100.0, 1400.0])
    assert {leg["category_id"] for leg in legs} == {"income:salary", "income:reimbursement"}


def test_put_posting_split_rejects_legs_that_dont_sum_correctly(client) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    response = client.put(
        f"/api/accounting/postings/{payroll['posting_id']}/split",
        json=[{"amount": 100.0}, {"amount": 100.0}],
    )
    assert response.status_code == 400


def test_delete_posting_split_restores_the_original_posting(client) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    client.put(
        f"/api/accounting/postings/{payroll['posting_id']}/split",
        json=[{"amount": 1000.0}, {"amount": 500.0}],
    )
    client.delete(f"/api/accounting/postings/{payroll['posting_id']}/split")

    updated = client.get("/api/accounting/postings").json()
    assert any(p["posting_id"] == payroll["posting_id"] for p in updated)


def test_import_paystub_reconciles_against_a_matching_bank_posting(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    paystub_text = (
        "Pay Date: 06/30/2026\nGross Pay: $2,000.00\nTotal Taxes: $500.00\nNet Pay: $1,500.00\n"
        "Direct Deposit\nChecking ending in 9579 $1,500.00\n"
    )
    monkeypatch.setattr(accounting_imports_router, "extract_paystub_pdf_text", lambda _pdf_bytes: paystub_text)

    response = client.post(
        "/api/accounting/import/paystub", files={"file": ("paystub.pdf", b"%PDF-fake", "application/pdf")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["statement"]["gross_pay"] == pytest.approx(2000.0)

    postings = client.get("/api/accounting/postings").json()
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
        "/api/accounting/import/paystub", files={"file": ("paystub.pdf", b"%PDF-fake", "application/pdf")}
    )
    assert response.status_code == 400


class _FakeLLMProvider:
    def __init__(self, response: str) -> None:
        self._response = response

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return self._response


def test_ai_suggest_category_applies_a_valid_suggestion(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    fake_response = '{"category_id": "income:salary", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )

    response = client.post(f"/api/accounting/postings/{payroll['posting_id']}/ai-suggest-category")
    assert response.status_code == 200
    body = response.json()
    assert body == {"category_id": "income:salary", "subcategory_id": None, "applied": True}

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"


def test_ai_suggest_category_does_not_apply_a_hallucinated_category(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    fake_response = '{"category_id": "not-a-real-category", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )

    response = client.post(f"/api/accounting/postings/{payroll['posting_id']}/ai-suggest-category")
    assert response.status_code == 200
    assert response.json() == {"category_id": None, "subcategory_id": None, "applied": False}

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] is None


def test_ai_suggest_category_with_lock_category_id_only_fills_the_subcategory(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    client.put(
        f"/api/accounting/postings/{payroll['posting_id']}/override", json={"category_id": "income:reimbursement"}
    )

    fake_response = '{"category_id": "income:reimbursement", "subcategory_id": "income:reimbursement:employer"}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )

    response = client.post(
        f"/api/accounting/postings/{payroll['posting_id']}/ai-suggest-category",
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
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    client.put(f"/api/accounting/postings/{payroll['posting_id']}/override", json={"category_id": "income:salary"})

    fake_response = '{"category_id": "income:reimbursement", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )

    response = client.post(
        f"/api/accounting/postings/{payroll['posting_id']}/ai-suggest-category",
        params={"lock_category_id": "income:salary"},
    )
    assert response.status_code == 200
    assert response.json() == {"category_id": None, "subcategory_id": None, "applied": False}

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"  # untouched, never overwritten


def test_ai_suggest_category_503s_when_no_provider_is_configured(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    monkeypatch.setattr(accounting_llm_router, "_llm_providers", lambda session, user_id: [])
    response = client.post(f"/api/accounting/postings/{payroll['posting_id']}/ai-suggest-category")
    assert response.status_code == 503


def test_ai_suggest_category_404s_for_an_unknown_posting(client, monkeypatch) -> None:
    monkeypatch.setattr(accounting_llm_router, "_llm_providers", lambda session, user_id: [])
    response = client.post("/api/accounting/postings/does-not-exist/ai-suggest-category")
    assert response.status_code == 404


def test_ai_suggest_category_marks_the_posting_pending_until_validated(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    fake_response = '{"category_id": "income:salary", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )
    client.post(f"/api/accounting/postings/{payroll['posting_id']}/ai-suggest-category")

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"
    assert updated_payroll["pending_source"] == "ai"
    assert updated_payroll["pending_selected"] is True


def test_validate_pending_accepts_a_selected_suggestion(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    fake_response = '{"category_id": "income:salary", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )
    client.post(f"/api/accounting/postings/{payroll['posting_id']}/ai-suggest-category")

    response = client.post("/api/accounting/postings/validate-pending", json={"posting_ids": [payroll["posting_id"]]})
    assert response.status_code == 200
    assert response.json() == {"accepted": 1, "reverted": 0}

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"
    assert updated_payroll["pending_source"] is None


def test_validate_pending_reverts_an_unselected_suggestion(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    assert payroll["category_id"] is None

    fake_response = '{"category_id": "income:salary", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )
    client.post(f"/api/accounting/postings/{payroll['posting_id']}/ai-suggest-category")
    client.put(f"/api/accounting/postings/{payroll['posting_id']}/override", json={"pending_selected": False})

    response = client.post("/api/accounting/postings/validate-pending", json={"posting_ids": [payroll["posting_id"]]})
    assert response.status_code == 200
    assert response.json() == {"accepted": 0, "reverted": 1}

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] is None
    assert updated_payroll["pending_source"] is None


def test_validate_pending_ignores_postings_outside_the_given_list(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    fake_response = '{"category_id": "income:salary", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )
    client.post(f"/api/accounting/postings/{payroll['posting_id']}/ai-suggest-category")

    response = client.post("/api/accounting/postings/validate-pending", json={"posting_ids": ["some-other-posting"]})
    assert response.json() == {"accepted": 0, "reverted": 0}

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["pending_source"] == "ai"


def test_pattern_suggest_category_stages_a_pending_suggestion(client) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    client.put(
        "/api/accounting/category-patterns",
        json={"p1": {"pattern_id": "p1", "description_contains": "PAYROLL", "category_id": "income:salary"}},
    )

    response = client.post(f"/api/accounting/postings/{payroll['posting_id']}/pattern-suggest-category")
    assert response.status_code == 200
    assert response.json() == {"category_id": "income:salary", "subcategory_id": None, "applied": True}

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"
    assert updated_payroll["pending_source"] == "pattern"


def test_pattern_suggest_category_returns_unapplied_when_nothing_matches(client) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    response = client.post(f"/api/accounting/postings/{payroll['posting_id']}/pattern-suggest-category")
    assert response.json() == {"category_id": None, "subcategory_id": None, "applied": False}


def test_pattern_suggest_category_bulk_stages_suggestions_for_many_postings_in_one_call(client) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    card_payment = next(p for p in postings if p["account_id"] == account_id and p["amount"] < 0)

    client.put(
        "/api/accounting/category-patterns",
        json={
            "p1": {"pattern_id": "p1", "description_contains": "PAYROLL", "category_id": "income:salary"},
            "p2": {"pattern_id": "p2", "description_contains": "Chase card", "category_id": "expense:admin-fees"},
        },
    )

    response = client.post(
        "/api/accounting/postings/pattern-suggest-category/bulk",
        json={"posting_ids": [payroll["posting_id"], card_payment["posting_id"]]},
    )
    assert response.status_code == 200
    assert response.json() == {"applied": 2}

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    updated_card = next(p for p in updated if p["posting_id"] == card_payment["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"
    assert updated_payroll["pending_source"] == "pattern"
    assert updated_card["category_id"] == "expense:admin-fees"
    assert updated_card["pending_source"] == "pattern"


def test_pattern_suggest_category_bulk_skips_postings_whose_existing_category_disagrees(client) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    client.put(
        f"/api/accounting/postings/{payroll['posting_id']}/override",
        json={"category_id": "income:bonus"},
    )

    client.put(
        "/api/accounting/category-patterns",
        json={"p1": {"pattern_id": "p1", "description_contains": "PAYROLL", "category_id": "income:salary"}},
    )

    response = client.post(
        "/api/accounting/postings/pattern-suggest-category/bulk", json={"posting_ids": [payroll["posting_id"]]}
    )
    assert response.json() == {"applied": 0}

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:bonus"
    assert updated_payroll["pending_source"] is None


def test_put_category_patterns_persists_and_is_returned_by_store(client) -> None:
    response = client.put(
        "/api/accounting/category-patterns",
        json={"p1": {"pattern_id": "p1", "description_contains": "PAYROLL", "category_id": "income:salary"}},
    )
    assert response.status_code == 200

    store = client.get("/api/accounting/store").json()
    assert store["category_patterns"]["p1"]["category_id"] == "income:salary"


def test_llm_usage_starts_unconfigured_and_unused(client, monkeypatch) -> None:
    class _NoCredentials:
        gemini_api_key = None
        mistral_api_key = None

    monkeypatch.setattr(accounting_llm_router, "resolve_llm_credentials", lambda session, user_id: _NoCredentials())

    body = client.get("/api/accounting/llm-usage").json()
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

    body = client.get("/api/accounting/llm-usage").json()
    assert body["gemini"] == {
        "configured": True,
        "used_count": 0,
        "period": "daily",
        "is_limited": True,
        "last_error": "429 RESOURCE_EXHAUSTED",
    }


def test_llm_settings_default_to_no_override(client) -> None:
    body = client.get("/api/accounting/settings/llm").json()
    assert body == {"gemini_key_set": False, "mistral_key_set": False}


def test_llm_settings_put_then_get_round_trips(client) -> None:
    put_response = client.put("/api/accounting/settings/llm", json={"gemini_api_key": "gem-key"})
    assert put_response.status_code == 200
    assert put_response.json() == {"gemini_key_set": True, "mistral_key_set": False}
    assert client.get("/api/accounting/settings/llm").json() == {"gemini_key_set": True, "mistral_key_set": False}


def test_llm_settings_put_merges_a_partial_update(client) -> None:
    client.put("/api/accounting/settings/llm", json={"gemini_api_key": "gem-key"})
    client.put("/api/accounting/settings/llm", json={"mistral_api_key": "mis-key"})
    body = client.get("/api/accounting/settings/llm").json()
    assert body == {"gemini_key_set": True, "mistral_key_set": True}


def test_llm_settings_delete_clears_the_override(client) -> None:
    client.put("/api/accounting/settings/llm", json={"gemini_api_key": "gem-key"})
    delete_response = client.delete("/api/accounting/settings/llm")
    assert delete_response.status_code == 200
    assert client.get("/api/accounting/settings/llm").json() == {"gemini_key_set": False, "mistral_key_set": False}


def test_net_worth_reports_the_checking_balance_as_an_asset(client) -> None:
    account_id = _import_chase_checking(client)
    body = client.get("/api/accounting/net-worth").json()
    assert body["assets"] == pytest.approx(1430.0)
    checking_row = next(row for row in body["accounts"] if row["account_id"] == account_id)
    assert checking_row["balance"] == pytest.approx(1430.0)


def test_net_worth_degrades_gracefully_when_trades_has_never_been_synced(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(trades_api.app.state, "config", AppConfig(ibkr={"cache_dir": tmp_path / "empty-ibkr"}))
    investment = _create_account(
        client,
        name="Interactive Brokers",
        kind="external_investment",
        institution="external",
        currency="USD",
        parent_account_id=None,
        external_ref="trades",
        meta={},
    )
    client.put(
        "/api/accounting/transfer-rules",
        json=[
            {
                "rule_id": "interactive-brokers-transfer",
                "description_contains": "INTERACTIVE BROK",
                "account_id": None,
                "category_id": None,
                "subcategory_id": None,
                "counterparty_account_id": investment["account_id"],
                "priority": 0,
                "description": "",
            }
        ],
    )
    sofi_savings = _create_account(client, name="SoFi Savings", kind="savings", institution="SoFi")
    sofi_savings_csv = (
        "Date,Description,Type,Amount,Current balance,Status\n"
        "2026-07-01,INTERACTIVE BROK,DIRECT_PAY,-2500,14.21,Posted\n"
    )
    client.post(
        "/api/accounting/import",
        files={"file": ("SOFI-Savings.csv", sofi_savings_csv, "text/csv")},
        data={
            "institution": "SoFi",
            "account_kind": "savings",
            "account_id": sofi_savings["account_id"],
            "account_name": "SoFi Savings",
        },
    )
    response = client.get("/api/accounting/net-worth")
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
    suggestions = client.get("/api/accounting/transfer-suggestions").json()
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
    assert client.get("/api/accounting/transfer-suggestions").json() == []
    wider = client.get("/api/accounting/transfer-suggestions", params={"window_days": 7}).json()
    assert len(wider) == 1


def test_duplicate_suggestions_finds_the_same_purchase_imported_from_two_sources(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic")
    client.post(
        "/api/accounting/import/canonical",
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
        "/api/accounting/import/canonical",
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
    suggestions = client.get("/api/accounting/duplicate-suggestions").json()
    assert len(suggestions) == 1
    group = suggestions[0]
    assert group["account_id"] == account["account_id"]
    assert len(group["postings"]) == 2

    transaction_ids = [posting["transaction_id"] for posting in group["postings"]]
    merge_response = client.put(
        "/api/accounting/posting-merges",
        json={
            "m1": {
                "merge_id": "m1",
                "kept_transaction_id": transaction_ids[0],
                "duplicate_transaction_ids": [transaction_ids[1]],
                "description": "Whole Foods Market",
            }
        },
    )
    assert merge_response.status_code == 200
    assert client.get("/api/accounting/duplicate-suggestions").json() == []

    postings = client.get("/api/accounting/postings").json()
    remaining_transaction_ids = {
        posting["transaction_id"] for posting in postings if posting["account_id"] == account["account_id"]
    }
    assert remaining_transaction_ids == {transaction_ids[0]}


def test_monthly_income_expense_correctly_drops_a_duplicate_that_straddles_the_query_window(client) -> None:
    """A dashboard endpoint scoped to one month must still resolve a merge whose two sides are in different months.

    `_resolved_postings_and_store` now passes `since`/`until` straight
    through to `load_ledger`'s own SQL filter — safe even for a merge like
    this one (kept side dated the last day of June, duplicate dated the
    first day of July) because `apply_posting_merges` drops a duplicate
    purely by transaction id, read from `store.posting_merges` in full
    (never date-filtered), regardless of whether the transaction it was
    merged into even appears in this same date-limited frame. So querying
    "July only" still correctly drops the July-dated duplicate, even
    though the June-dated kept transaction was never loaded at all.
    """
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic")
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("a.csv", "Date,Description,Amount\n2026-06-30,WHOLE FOODS #123,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("b.csv", "Date,Description,Amount\n2026-07-01,Whole Foods Market,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
            "separator": ",",
        },
    )
    suggestions = client.get("/api/accounting/duplicate-suggestions").json()
    assert len(suggestions) == 1
    postings_in_group = suggestions[0]["postings"]
    kept_id = next(p for p in postings_in_group if p["posted_at"].startswith("2026-06-30"))["transaction_id"]
    duplicate_id = next(p for p in postings_in_group if p["posted_at"].startswith("2026-07-01"))["transaction_id"]
    merge_response = client.put(
        "/api/accounting/posting-merges",
        json={"m1": {"merge_id": "m1", "kept_transaction_id": kept_id, "duplicate_transaction_ids": [duplicate_id]}},
    )
    assert merge_response.status_code == 200

    july = client.get(
        "/api/accounting/income-statement/monthly", params={"start": "2026-07-01", "end": "2026-07-31"}
    ).json()
    july_row = next((row for row in july if row["month"] == "2026-07"), None)
    assert july_row is None or july_row["expense"] == pytest.approx(0.0)

    june = client.get(
        "/api/accounting/income-statement/monthly", params={"start": "2026-06-01", "end": "2026-06-30"}
    ).json()
    june_row = next(row for row in june if row["month"] == "2026-06")
    assert june_row["expense"] == pytest.approx(42.50)


def _two_duplicate_transaction_ids(client) -> tuple[str, str]:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic")
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("a.csv", "Date,Description,Amount\n2026-06-30,WHOLE FOODS #123,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("b.csv", "Date,Description,Amount\n2026-06-30,Whole Foods Market,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
            "separator": ",",
        },
    )
    suggestions = client.get("/api/accounting/duplicate-suggestions").json()
    transaction_ids = [posting["transaction_id"] for posting in suggestions[0]["postings"]]
    return transaction_ids[0], transaction_ids[1]


def test_post_posting_merge_creates_one_and_resolves_the_duplicate(client) -> None:
    kept_id, duplicate_id = _two_duplicate_transaction_ids(client)

    response = client.post(
        "/api/accounting/posting-merges",
        json={
            "kept_transaction_id": kept_id,
            "duplicate_transaction_ids": [duplicate_id],
            "description": "Whole Foods",
        },
    )

    assert response.status_code == 200
    merge = response.json()
    assert merge["merge_id"] == f"merge:{kept_id}"
    assert merge["kept_transaction_id"] == kept_id
    assert client.get("/api/accounting/duplicate-suggestions").json() == []


def test_post_posting_merge_twice_for_the_same_kept_transaction_replaces_rather_than_duplicates(client) -> None:
    kept_id, duplicate_id = _two_duplicate_transaction_ids(client)
    client.post(
        "/api/accounting/posting-merges",
        json={"kept_transaction_id": kept_id, "duplicate_transaction_ids": [duplicate_id]},
    )

    response = client.post(
        "/api/accounting/posting-merges",
        json={"kept_transaction_id": kept_id, "duplicate_transaction_ids": [duplicate_id], "description": "updated"},
    )

    assert response.status_code == 200
    merges = client.get("/api/accounting/store").json()["posting_merges"]
    assert list(merges.keys()) == [f"merge:{kept_id}"]
    assert merges[f"merge:{kept_id}"]["description"] == "updated"


def test_delete_posting_merge_undoes_it(client) -> None:
    kept_id, duplicate_id = _two_duplicate_transaction_ids(client)
    client.post(
        "/api/accounting/posting-merges",
        json={"kept_transaction_id": kept_id, "duplicate_transaction_ids": [duplicate_id]},
    )

    response = client.delete(f"/api/accounting/posting-merges/merge:{kept_id}")

    assert response.status_code == 200
    assert client.get("/api/accounting/store").json()["posting_merges"] == {}
    assert len(client.get("/api/accounting/duplicate-suggestions").json()) == 1


def test_delete_posting_merge_404s_for_an_unknown_id(client) -> None:
    response = client.delete("/api/accounting/posting-merges/does-not-exist")
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
    suggestions = client.get("/api/accounting/transfer-suggestions").json()
    assert len(suggestions) == 1
    assert suggestions[0]["suggestion_id"]
    # Recomputing (no state changed) yields the exact same id.
    again = client.get("/api/accounting/transfer-suggestions").json()
    assert again[0]["suggestion_id"] == suggestions[0]["suggestion_id"]


def test_dismissing_a_transfer_suggestion_removes_it_from_the_proposed_list(client) -> None:
    _seed_chase_transfer_suggestion(client)
    suggestion_id = client.get("/api/accounting/transfer-suggestions").json()[0]["suggestion_id"]

    response = client.post(
        "/api/accounting/dismissed-suggestions",
        json={"suggestion_id": suggestion_id, "kind": "transfer", "description": "Chase checking <-> credit card"},
    )
    assert response.status_code == 200
    assert client.get("/api/accounting/transfer-suggestions").json() == []


def test_dismissed_suggestion_shows_up_in_the_archive(client) -> None:
    _seed_chase_transfer_suggestion(client)
    suggestion_id = client.get("/api/accounting/transfer-suggestions").json()[0]["suggestion_id"]
    client.post(
        "/api/accounting/dismissed-suggestions",
        json={"suggestion_id": suggestion_id, "kind": "transfer", "description": "Chase checking <-> credit card"},
    )

    archive = client.get("/api/accounting/dismissed-suggestions").json()
    assert len(archive) == 1
    assert archive[0]["suggestion_id"] == suggestion_id
    assert archive[0]["kind"] == "transfer"
    assert archive[0]["description"] == "Chase checking <-> credit card"


def test_restoring_a_dismissed_suggestion_brings_it_back(client) -> None:
    _seed_chase_transfer_suggestion(client)
    suggestion_id = client.get("/api/accounting/transfer-suggestions").json()[0]["suggestion_id"]
    client.post(
        "/api/accounting/dismissed-suggestions",
        json={"suggestion_id": suggestion_id, "kind": "transfer", "description": "desc"},
    )
    assert client.get("/api/accounting/transfer-suggestions").json() == []

    restore_response = client.delete(f"/api/accounting/dismissed-suggestions/{suggestion_id}")
    assert restore_response.status_code == 200
    assert len(client.get("/api/accounting/transfer-suggestions").json()) == 1
    assert client.get("/api/accounting/dismissed-suggestions").json() == []


def test_restoring_an_unknown_suggestion_is_a_404(client) -> None:
    response = client.delete("/api/accounting/dismissed-suggestions/does-not-exist")
    assert response.status_code == 404


def test_dismissing_a_duplicate_suggestion_removes_it_from_the_proposed_list(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic")
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("a.csv", "Date,Description,Amount\n2026-06-30,WHOLE FOODS #123,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("b.csv", "Date,Description,Amount\n2026-06-30,Whole Foods Market,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
            "separator": ",",
        },
    )
    suggestions = client.get("/api/accounting/duplicate-suggestions").json()
    assert len(suggestions) == 1
    suggestion_id = suggestions[0]["suggestion_id"]

    response = client.post(
        "/api/accounting/dismissed-suggestions",
        json={"suggestion_id": suggestion_id, "kind": "duplicate", "description": "Whole Foods x2"},
    )
    assert response.status_code == 200
    assert client.get("/api/accounting/duplicate-suggestions").json() == []


def test_postings_report_which_rule_resolved_them(client) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)
    assert payroll["resolved_by_transfer_rule_id"] is None

    # `counterparty_account_id` is now a real foreign key into `accounts` (see
    # `accounting.db.automation.TransferRule`) — the account it names must already
    # exist, unlike before when a rule could forward-reference one created later.
    employer = _create_account(client, name="EQORE", kind="income_source", institution="internal")
    client.put(
        "/api/accounting/transfer-rules",
        json=[
            {
                "rule_id": "payroll-rule",
                "description_contains": "PAYROLL",
                "counterparty_account_id": employer["account_id"],
                "priority": 0,
            }
        ],
    )

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["account_id"] == account_id and p["amount"] > 0)
    assert updated_payroll["resolved_by_transfer_rule_id"] == "payroll-rule"


def test_put_transfer_rules_referencing_a_nonexistent_account_fails() -> None:
    """`counterparty_account_id` is a real foreign key now (see `accounting.db.automation.TransferRule`) —
    a rule naming an account that doesn't exist can no longer be silently accepted. No new API-level
    validation was added on top of that constraint — the database itself is the source of truth here,
    so this surfaces exactly like every other foreign-key violation in this app: an unhandled
    `IntegrityError` propagating out of the route as a 500, not a clean 4xx.
    """
    client = TestClient(trades_api.app, raise_server_exceptions=False)
    response = client.put(
        "/api/accounting/transfer-rules",
        json=[
            {
                "rule_id": "payroll-rule",
                "description_contains": "PAYROLL",
                "counterparty_account_id": "does-not-exist",
                "priority": 0,
            }
        ],
    )
    assert response.status_code == 500


def test_put_categories_replaces_the_whole_tree(client) -> None:
    response = client.put(
        "/api/accounting/categories",
        json={
            "expense:custom": {
                "category_id": "expense:custom",
                "name": "Custom",
                "classification": "expense",
                "color": "#000000",
            }
        },
    )
    assert response.status_code == 200
    store = client.get("/api/accounting/store").json()
    assert list(store["categories"].keys()) == ["expense:custom"]


def test_put_categories_auto_creates_other_for_a_categorys_first_subcategory(client) -> None:
    response = client.put(
        "/api/accounting/categories",
        json={
            "expense:custom": {
                "category_id": "expense:custom",
                "name": "Custom",
                "classification": "expense",
                "color": "#000000",
            },
            "expense:custom:gadgets": {
                "category_id": "expense:custom:gadgets",
                "name": "Gadgets",
                "classification": "expense",
                "parent_category_id": "expense:custom",
                "color": "#000000",
            },
        },
    )
    assert response.status_code == 200
    assert "expense:custom:other" in response.json()


def test_category_rename_without_a_collision_just_renames(client) -> None:
    client.put(
        "/api/accounting/categories",
        json={
            "expense:custom": {
                "category_id": "expense:custom",
                "name": "Custom",
                "classification": "expense",
                "color": "#000000",
            }
        },
    )
    response = client.post("/api/accounting/categories/expense:custom/rename", json={"name": "Renamed"})
    assert response.status_code == 200
    body = response.json()
    assert body["merged"] is False
    assert body["categories"]["expense:custom"]["name"] == "Renamed"


def test_category_rename_404s_for_an_unknown_category(client) -> None:
    response = client.post("/api/accounting/categories/expense:nope/rename", json={"name": "Anything"})
    assert response.status_code == 404


def test_category_rename_merges_into_an_existing_category_and_repoints_postings(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,Nourriture\n"
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    store = client.get("/api/accounting/store").json()
    nourriture = next(c for c in store["categories"].values() if c["name"] == "Nourriture")
    client.put(
        "/api/accounting/categories",
        json={
            **store["categories"],
            "expense:food": {
                "category_id": "expense:food",
                "name": "Food",
                "classification": "expense",
                "color": "#111111",
            },
        },
    )

    response = client.post(f"/api/accounting/categories/{nourriture['category_id']}/rename", json={"name": "Food"})
    assert response.status_code == 200
    body = response.json()
    assert body["merged"] is True
    assert nourriture["category_id"] not in body["categories"]
    assert "expense:food" in body["categories"]

    postings = client.get("/api/accounting/postings").json()
    grocery_leg = next(p for p in postings if p["account_id"] == account["account_id"])
    assert grocery_leg["category_id"] == "expense:food"


def test_category_rename_merge_repoints_a_manual_override(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,Nourriture\n"
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    store = client.get("/api/accounting/store").json()
    nourriture = next(c for c in store["categories"].values() if c["name"] == "Nourriture")
    postings = client.get("/api/accounting/postings").json()
    other_posting = next(p for p in postings if p["account_id"] != account["account_id"])
    client.put(
        f"/api/accounting/postings/{other_posting['posting_id']}/override",
        json={"category_id": nourriture["category_id"]},
    )
    client.put(
        "/api/accounting/categories",
        json={
            **store["categories"],
            "expense:food": {
                "category_id": "expense:food",
                "name": "Food",
                "classification": "expense",
                "color": "#111111",
            },
        },
    )

    client.post(f"/api/accounting/categories/{nourriture['category_id']}/rename", json={"name": "Food"})

    postings = client.get("/api/accounting/postings").json()
    overridden = next(p for p in postings if p["posting_id"] == other_posting["posting_id"])
    assert overridden["category_id"] == "expense:food"


def test_category_delete_preview_counts_postings_pointing_at_the_category(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,Nourriture\n"
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    store = client.get("/api/accounting/store").json()
    nourriture = next(c for c in store["categories"].values() if c["name"] == "Nourriture")

    response = client.get(f"/api/accounting/categories/{nourriture['category_id']}/delete-preview")
    assert response.status_code == 200
    assert response.json()["posting_count"] == 1


def test_category_delete_preview_is_zero_for_an_unused_category(client) -> None:
    response = client.get("/api/accounting/categories/expense:food-drink/delete-preview")
    assert response.status_code == 200
    assert response.json()["posting_count"] == 0


def test_category_delete_preview_404s_for_an_unknown_category(client) -> None:
    response = client.get("/api/accounting/categories/expense:nope/delete-preview")
    assert response.status_code == 404


def test_category_delete_removes_the_category_and_uncategorizes_its_postings(client) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,Nourriture\n"
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    store = client.get("/api/accounting/store").json()
    nourriture = next(c for c in store["categories"].values() if c["name"] == "Nourriture")

    response = client.delete(f"/api/accounting/categories/{nourriture['category_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["uncategorized_posting_count"] == 1
    assert nourriture["category_id"] not in body["categories"]

    postings = client.get("/api/accounting/postings").json()
    grocery_leg = next(p for p in postings if p["account_id"] == account["account_id"])
    assert grocery_leg["category_id"] is None


def test_category_delete_cascades_to_subcategories(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    client.post("/api/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#222222"})

    response = client.delete("/api/accounting/categories/expense:custom")
    assert response.status_code == 200
    body = response.json()
    assert "expense:custom" not in body["categories"]
    assert "expense:custom:gadgets" not in body["categories"]


def test_category_delete_404s_for_an_unknown_category(client) -> None:
    response = client.delete("/api/accounting/categories/expense:nope")
    assert response.status_code == 404


def test_post_category_creates_a_new_top_level_category(client) -> None:
    response = client.post(
        "/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["category_id"] == "expense:custom"
    store = client.get("/api/accounting/store").json()
    assert "expense:custom" in store["categories"]


def test_post_category_409s_on_a_same_classification_duplicate_name(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    response = client.post(
        "/api/accounting/categories", json={"name": "custom", "classification": "expense", "color": "#111111"}
    )
    assert response.status_code == 409


def test_post_category_allows_the_same_name_under_a_different_classification(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    response = client.post(
        "/api/accounting/categories", json={"name": "Custom", "classification": "income", "color": "#111111"}
    )
    assert response.status_code == 200


def test_post_subcategory_creates_a_new_subcategory(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    response = client.post(
        "/api/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#222222"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["category_id"] == "expense:custom:gadgets"
    assert body["parent_category_id"] == "expense:custom"


def test_post_subcategory_404s_for_an_unknown_parent(client) -> None:
    response = client.post(
        "/api/accounting/categories/expense:nope/subcategories", json={"name": "Gadgets", "color": "#222222"}
    )
    assert response.status_code == 404


def test_post_subcategory_409s_on_a_same_name_sibling(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    client.post("/api/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#222222"})
    response = client.post(
        "/api/accounting/categories/expense:custom/subcategories", json={"name": "gadgets", "color": "#333333"}
    )
    assert response.status_code == 409


def test_post_subcategory_allows_the_same_name_under_a_different_parent(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    client.post(
        "/api/accounting/categories", json={"name": "Other Top", "classification": "expense", "color": "#444444"}
    )
    client.post("/api/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#222222"})
    response = client.post(
        "/api/accounting/categories/expense:other-top/subcategories", json={"name": "Gadgets", "color": "#555555"}
    )
    assert response.status_code == 200


def test_category_rename_preview_reports_no_merge_for_a_plain_rename(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    response = client.get("/api/accounting/categories/expense:custom/rename-preview", params={"name": "Renamed"})
    assert response.status_code == 200
    body = response.json()
    assert body["will_merge"] is False
    assert body["target_name"] is None


def test_category_rename_preview_404s_for_an_unknown_category(client) -> None:
    response = client.get("/api/accounting/categories/expense:nope/rename-preview", params={"name": "Anything"})
    assert response.status_code == 404


def test_category_rename_preview_reports_merge_for_a_top_level_same_classification_collision(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    client.post("/api/accounting/categories", json={"name": "Food", "classification": "expense", "color": "#111111"})
    response = client.get("/api/accounting/categories/expense:custom/rename-preview", params={"name": "Food"})
    assert response.status_code == 200
    body = response.json()
    assert body["will_merge"] is True
    assert body["target_name"] == "Food"


def test_category_rename_preview_reports_no_merge_across_different_classifications(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    client.post("/api/accounting/categories", json={"name": "Food", "classification": "income", "color": "#111111"})
    response = client.get("/api/accounting/categories/expense:custom/rename-preview", params={"name": "Food"})
    assert response.status_code == 200
    assert response.json()["will_merge"] is False


def test_category_rename_preview_reports_merge_for_a_subcategory_same_parent_collision(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    client.post("/api/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#222222"})
    client.post("/api/accounting/categories/expense:custom/subcategories", json={"name": "Books", "color": "#333333"})
    response = client.get("/api/accounting/categories/expense:custom:gadgets/rename-preview", params={"name": "Books"})
    assert response.status_code == 200
    body = response.json()
    assert body["will_merge"] is True
    assert body["target_name"] == "Books"


def test_category_rename_preview_reports_no_merge_across_different_parents(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    client.post(
        "/api/accounting/categories", json={"name": "Other Top", "classification": "expense", "color": "#444444"}
    )
    client.post("/api/accounting/categories/expense:custom/subcategories", json={"name": "Gadgets", "color": "#222222"})
    client.post(
        "/api/accounting/categories/expense:other-top/subcategories", json={"name": "Books", "color": "#333333"}
    )
    response = client.get("/api/accounting/categories/expense:custom:gadgets/rename-preview", params={"name": "Books"})
    assert response.status_code == 200
    assert response.json()["will_merge"] is False


def test_category_rename_preview_reports_a_budget_the_merge_would_delete(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    client.post("/api/accounting/categories", json={"name": "Food", "classification": "expense", "color": "#111111"})
    client.put(
        "/api/accounting/budgets",
        json=[
            {"budget_id": "b1", "month": "2026-06", "category_id": "expense:custom", "amount": 100.0},
            {"budget_id": "b2", "month": "2026-06", "category_id": "expense:food", "amount": 200.0},
        ],
    )
    response = client.get("/api/accounting/categories/expense:custom/rename-preview", params={"name": "Food"})
    assert response.status_code == 200
    body = response.json()
    assert body["will_merge"] is True
    assert body["budgets_to_delete"] == [{"month": "2026-06", "amount": pytest.approx(100.0), "currency": "USD"}]


def test_category_rename_preview_reports_no_budgets_to_delete_without_a_collision(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    client.post("/api/accounting/categories", json={"name": "Food", "classification": "expense", "color": "#111111"})
    client.put(
        "/api/accounting/budgets",
        json=[{"budget_id": "b1", "month": "2026-06", "category_id": "expense:custom", "amount": 100.0}],
    )
    response = client.get("/api/accounting/categories/expense:custom/rename-preview", params={"name": "Food"})
    assert response.status_code == 200
    assert response.json()["budgets_to_delete"] == []


def test_category_rename_merge_drops_the_merged_away_budget_instead_of_failing(client) -> None:
    client.post("/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"})
    client.post("/api/accounting/categories", json={"name": "Food", "classification": "expense", "color": "#111111"})
    client.put(
        "/api/accounting/budgets",
        json=[
            {"budget_id": "b1", "month": "2026-06", "category_id": "expense:custom", "amount": 100.0},
            {"budget_id": "b2", "month": "2026-06", "category_id": "expense:food", "amount": 200.0},
        ],
    )
    response = client.post("/api/accounting/categories/expense:custom/rename", json={"name": "Food"})
    assert response.status_code == 200
    budgets = client.get("/api/accounting/store").json()["budgets"]
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
    frame = pl.DataFrame([posting.model_dump(mode="python")], schema=Posting.polars_schema)
    ingest_module._write_ledger(frame, db_session, user_id=DEFAULT_USER_ID)


def test_tag_rename_without_a_collision_just_renames(client) -> None:
    trip = client.post("/api/accounting/tags", json={"name": "Trip"}).json()
    response = client.post(f"/api/accounting/tags/{trip['tag_id']}/rename", json={"name": "Vacation"})
    assert response.status_code == 200
    body = response.json()
    assert body["merged"] is False
    assert body["tags"][trip["tag_id"]]["name"] == "Vacation"


def test_tag_rename_404s_for_an_unknown_tag(client) -> None:
    response = client.post("/api/accounting/tags/tag:nope/rename", json={"name": "Anything"})
    assert response.status_code == 404


def test_tag_rename_merges_into_an_existing_tag_and_repoints_posting_tags(client, db_session) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    trip = client.post("/api/accounting/tags", json={"name": "Trip"}).json()
    vacation = client.post("/api/accounting/tags", json={"name": "Vacation"}).json()
    _write_posting_with_tags(db_session, account["account_id"], "p1", "t1", [trip["tag_id"]])

    response = client.post(f"/api/accounting/tags/{trip['tag_id']}/rename", json={"name": "Vacation"})
    assert response.status_code == 200
    body = response.json()
    assert body["merged"] is True
    assert trip["tag_id"] not in body["tags"]
    assert vacation["tag_id"] in body["tags"]

    postings = client.get("/api/accounting/postings").json()
    p1 = next(p for p in postings if p["posting_id"] == "p1")
    assert p1["tag_ids"] == [vacation["tag_id"]]


def test_tag_rename_merge_handles_a_posting_already_tagged_with_both(client, db_session) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    trip = client.post("/api/accounting/tags", json={"name": "Trip"}).json()
    vacation = client.post("/api/accounting/tags", json={"name": "Vacation"}).json()
    _write_posting_with_tags(db_session, account["account_id"], "p1", "t1", [trip["tag_id"], vacation["tag_id"]])

    response = client.post(f"/api/accounting/tags/{trip['tag_id']}/rename", json={"name": "Vacation"})
    assert response.status_code == 200

    postings = client.get("/api/accounting/postings").json()
    p1 = next(p for p in postings if p["posting_id"] == "p1")
    assert p1["tag_ids"] == [vacation["tag_id"]]


def test_tag_rename_merge_repoints_a_tag_ids_override(client, db_session) -> None:
    account = _create_account(client, name="Generic Checking", kind="checking", institution="Generic Bank")
    trip = client.post("/api/accounting/tags", json={"name": "Trip"}).json()
    vacation = client.post("/api/accounting/tags", json={"name": "Vacation"}).json()
    _write_posting_with_tags(db_session, account["account_id"], "p1", "t1", [])
    client.put("/api/accounting/postings/p1/override", json={"tag_ids": [trip["tag_id"]]})

    client.post(f"/api/accounting/tags/{trip['tag_id']}/rename", json={"name": "Vacation"})

    postings = client.get("/api/accounting/postings").json()
    p1 = next(p for p in postings if p["posting_id"] == "p1")
    assert p1["tag_ids"] == [vacation["tag_id"]]


def test_post_tag_creates_a_new_tag(client) -> None:
    response = client.post("/api/accounting/tags", json={"name": "Trip"})
    assert response.status_code == 200
    body = response.json()
    assert body["tag_id"] == "tag:trip"
    store = client.get("/api/accounting/store").json()
    assert "tag:trip" in store["tags"]


def test_post_tag_409s_on_a_duplicate_name_case_insensitive(client) -> None:
    client.post("/api/accounting/tags", json={"name": "Trip"})
    response = client.post("/api/accounting/tags", json={"name": "trip"})
    assert response.status_code == 409


def test_post_tag_allows_a_distinct_name(client) -> None:
    client.post("/api/accounting/tags", json={"name": "Trip"})
    response = client.post("/api/accounting/tags", json={"name": "Move"})
    assert response.status_code == 200


def test_tag_rename_preview_reports_no_merge_for_a_plain_rename(client) -> None:
    client.post("/api/accounting/tags", json={"name": "Trip"})
    response = client.get("/api/accounting/tags/tag:trip/rename-preview", params={"name": "Renamed"})
    assert response.status_code == 200
    body = response.json()
    assert body["will_merge"] is False
    assert body["target_name"] is None


def test_tag_rename_preview_404s_for_an_unknown_tag(client) -> None:
    response = client.get("/api/accounting/tags/tag:nope/rename-preview", params={"name": "Anything"})
    assert response.status_code == 404


def test_tag_rename_preview_reports_merge_for_a_name_collision(client) -> None:
    client.post("/api/accounting/tags", json={"name": "Trip"})
    client.post("/api/accounting/tags", json={"name": "Vacation"})
    response = client.get("/api/accounting/tags/tag:trip/rename-preview", params={"name": "Vacation"})
    assert response.status_code == 200
    body = response.json()
    assert body["will_merge"] is True
    assert body["target_name"] == "Vacation"


def test_put_categories_removes_other_once_it_is_left_alone(client) -> None:
    client.put(
        "/api/accounting/categories",
        json={
            "expense:custom": {
                "category_id": "expense:custom",
                "name": "Custom",
                "classification": "expense",
                "color": "#000000",
            },
            "expense:custom:gadgets": {
                "category_id": "expense:custom:gadgets",
                "name": "Gadgets",
                "classification": "expense",
                "parent_category_id": "expense:custom",
                "color": "#000000",
            },
        },
    )
    response = client.put(
        "/api/accounting/categories",
        json={
            "expense:custom": {
                "category_id": "expense:custom",
                "name": "Custom",
                "classification": "expense",
                "color": "#000000",
            },
            "expense:custom:other": {
                "category_id": "expense:custom:other",
                "name": "Other",
                "classification": "expense",
                "parent_category_id": "expense:custom",
                "color": "#000000",
            },
        },
    )
    assert "expense:custom:other" not in response.json()


def test_put_other_assets_persists(client) -> None:
    response = client.put("/api/accounting/other-assets", json=[{"asset_id": "car", "name": "Car", "value": 15000.0}])
    assert response.status_code == 200
    body = client.get("/api/accounting/net-worth").json()
    assert body["other_assets_total"] == pytest.approx(15000.0)


def test_put_budgets_persists_and_comparison_reflects_actual_spend(client) -> None:
    _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payment = next(p for p in postings if p["amount"] < 0)
    client.put(f"/api/accounting/postings/{payment['posting_id']}/override", json={"category_id": "expense:admin-fees"})

    response = client.put(
        "/api/accounting/budgets",
        json=[{"budget_id": "b1", "month": "2026-06", "category_id": "expense:admin-fees", "amount": 100.0}],
    )
    assert response.status_code == 200
    assert "expense:admin-fees" in [b["category_id"] for b in client.get("/api/accounting/store").json()["budgets"]]

    comparison = client.get("/api/accounting/budgets/comparison", params={"month": "2026-06"}).json()
    assert len(comparison) == 1
    assert comparison[0]["budgeted"] == pytest.approx(100.0)
    assert comparison[0]["actual"] == pytest.approx(70.0)


def test_get_budget_comparison_rejects_a_malformed_month(client) -> None:
    response = client.get("/api/accounting/budgets/comparison", params={"month": "not-a-month"})
    assert response.status_code == 400


def test_put_general_budgets_persists_separately_from_per_month_budgets(client) -> None:
    client.put(
        "/api/accounting/budgets",
        json=[{"budget_id": "b1", "month": "2026-06", "category_id": "expense:food-drink", "amount": 100.0}],
    )
    response = client.put(
        "/api/accounting/general-budgets",
        json={"expense:food-drink": {"category_id": "expense:food-drink", "amount": 500.0}},
    )
    assert response.status_code == 200
    store = client.get("/api/accounting/store").json()
    assert store["general_budgets"]["expense:food-drink"]["amount"] == pytest.approx(500.0)
    assert store["budgets"][0]["amount"] == pytest.approx(100.0)


def test_post_budget_upserts_one_budget_without_touching_others(client) -> None:
    client.put(
        "/api/accounting/budgets",
        json=[{"budget_id": "existing", "month": "2026-05", "category_id": "expense:transport", "amount": 40.0}],
    )

    response = client.post(
        "/api/accounting/budgets",
        json={"month": "2026-06", "category_id": "expense:food-drink", "amount": 300.0, "currency": "USD"},
    )

    assert response.status_code == 200
    created = response.json()
    assert created["budget_id"] == "2026-06:expense:food-drink"
    assert created["amount"] == pytest.approx(300.0)

    budgets = client.get("/api/accounting/store").json()["budgets"]
    assert {b["budget_id"] for b in budgets} == {"existing", "2026-06:expense:food-drink"}


def test_post_budget_with_a_subcategory_includes_it_in_the_derived_id(client) -> None:
    response = client.post(
        "/api/accounting/budgets",
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
        "/api/accounting/budgets", json={"month": "2026-06", "category_id": "expense:food-drink", "amount": 100.0}
    )
    client.post(
        "/api/accounting/budgets", json={"month": "2026-06", "category_id": "expense:food-drink", "amount": 250.0}
    )

    budgets = client.get("/api/accounting/store").json()["budgets"]
    matching = [b for b in budgets if b["budget_id"] == "2026-06:expense:food-drink"]
    assert len(matching) == 1
    assert matching[0]["amount"] == pytest.approx(250.0)


def test_delete_budget_removes_only_that_one(client) -> None:
    client.put(
        "/api/accounting/budgets",
        json=[
            {"budget_id": "b1", "month": "2026-06", "category_id": "expense:food-drink", "amount": 100.0},
            {"budget_id": "b2", "month": "2026-06", "category_id": "expense:transport", "amount": 50.0},
        ],
    )

    response = client.delete("/api/accounting/budgets/b1")

    assert response.status_code == 200
    budgets = client.get("/api/accounting/store").json()["budgets"]
    assert {b["budget_id"] for b in budgets} == {"b2"}


def test_delete_budget_404s_for_an_unknown_id(client) -> None:
    response = client.delete("/api/accounting/budgets/does-not-exist")
    assert response.status_code == 404


def test_post_general_budget_upserts_one_without_touching_others(client) -> None:
    client.put(
        "/api/accounting/general-budgets",
        json={"expense:transport": {"category_id": "expense:transport", "amount": 40.0}},
    )

    response = client.post(
        "/api/accounting/general-budgets", json={"category_id": "expense:food-drink", "amount": 500.0}
    )

    assert response.status_code == 200
    assert response.json()["amount"] == pytest.approx(500.0)
    general_budgets = client.get("/api/accounting/store").json()["general_budgets"]
    assert set(general_budgets.keys()) == {"expense:transport", "expense:food-drink"}


def test_post_general_budget_with_a_subcategory_keys_by_subcategory(client) -> None:
    response = client.post(
        "/api/accounting/general-budgets",
        json={"category_id": "expense:food-drink", "subcategory_id": "expense:food-drink:groceries", "amount": 200.0},
    )
    assert response.status_code == 200
    general_budgets = client.get("/api/accounting/store").json()["general_budgets"]
    assert "expense:food-drink:groceries" in general_budgets
    assert "expense:food-drink" not in general_budgets


def test_delete_general_budget_removes_only_that_one(client) -> None:
    client.put(
        "/api/accounting/general-budgets",
        json={
            "expense:food-drink": {"category_id": "expense:food-drink", "amount": 500.0},
            "expense:transport": {"category_id": "expense:transport", "amount": 40.0},
        },
    )

    response = client.delete("/api/accounting/general-budgets/expense:food-drink")

    assert response.status_code == 200
    general_budgets = client.get("/api/accounting/store").json()["general_budgets"]
    assert set(general_budgets.keys()) == {"expense:transport"}


def test_delete_general_budget_404s_for_an_unknown_key(client) -> None:
    response = client.delete("/api/accounting/general-budgets/does-not-exist")
    assert response.status_code == 404


def test_get_suggested_budget_amount_returns_zero_with_no_history(client) -> None:
    response = client.get(
        "/api/accounting/budgets/suggested-amount", params={"category_id": "expense:food-drink", "month": "2026-06"}
    )
    assert response.status_code == 200
    assert response.json()["suggested_amount"] == pytest.approx(0.0)


def test_rebuild_with_nothing_imported_is_a_404(client) -> None:
    assert client.post("/api/accounting/rebuild").status_code == 404


def test_rebuild_reconstructs_from_raw_after_import(client) -> None:
    _import_chase_checking(client)
    response = client.post("/api/accounting/rebuild")
    assert response.status_code == 200


def test_get_currencies_lists_usd_and_eur(client) -> None:
    body = client.get("/api/accounting/currencies").json()
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
    response = client.get("/api/accounting/net-worth")
    assert response.status_code == 200


def test_net_worth_400s_when_a_needed_currency_was_never_synced(client) -> None:
    _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
    response = client.get("/api/accounting/net-worth")
    assert response.status_code == 400


def test_get_current_exchange_rate_after_sync(client, monkeypatch) -> None:
    _mock_fetch(monkeypatch)
    exchange_rates.update_rate_history_cache(accounting_api.state.config)
    response = client.get("/api/accounting/exchange-rates/current", params={"currency": "EUR"})
    assert response.status_code == 200
    body = response.json()
    assert body["rate_to_base"] == pytest.approx(2.0)
    assert body["base_currency"] == "USD"


def test_get_exchange_rate_history_after_sync(client, monkeypatch) -> None:
    _mock_fetch(monkeypatch)
    exchange_rates.update_rate_history_cache(accounting_api.state.config)
    response = client.get("/api/accounting/exchange-rates/history", params={"currency": "EUR"})
    assert response.status_code == 200
    body = response.json()
    assert [row["rate"] for row in body] == pytest.approx([1.9, 2.1])


def test_post_account_creates_a_new_account(client) -> None:
    response = client.post(
        "/api/accounting/accounts",
        json={"name": "BNP Checking", "kind": "checking", "institution": "BNP", "currency": "EUR"},
    )
    assert response.status_code == 200
    account_id = response.json()["account_id"]
    assert account_id
    assert account_id in client.get("/api/accounting/store").json()["accounts"]


def test_post_account_twice_with_no_last_four_creates_two_distinct_accounts(client) -> None:
    first = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
    second = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
    assert first["account_id"] != second["account_id"]

    accounts = client.get("/api/accounting/store").json()["accounts"]
    assert first["account_id"] in accounts
    assert second["account_id"] in accounts


def test_accounts_are_isolated_between_users(client, db_session) -> None:
    """Proof that `store.py`'s account CRUD is genuinely per-user, not a shared global store.

    Regression test for the FK-violation/cross-user-leak sweep: before every
    endpoint threaded a real `user_id` through `load_store`/`save_store`,
    this router had no way to keep two users' accounts apart at all.
    """
    other_user_id = uuid.uuid4()
    db_session.add(dbm.User(id=other_user_id, email="other@example.com", hashed_password="unset"))  # noqa: S106
    db_session.commit()

    account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
    assert account["account_id"] in client.get("/api/accounting/store").json()["accounts"]

    trades_api.app.dependency_overrides[get_current_user_id] = lambda: other_user_id
    try:
        # The first user's account must not be visible to the second user...
        other_store = client.get("/api/accounting/store").json()
        assert account["account_id"] not in other_store["accounts"]

        # ...and the second user is free to register their own account too,
        # getting back its own distinct, server-generated id.
        other_account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
        assert other_account["account_id"] != account["account_id"]
        assert other_account["account_id"] in client.get("/api/accounting/store").json()["accounts"]
    finally:
        trades_api.app.dependency_overrides[get_current_user_id] = lambda: DEFAULT_USER_ID

    # Back as the first user, their own account is unaffected by the second
    # user's account, and still the only one they can see.
    assert account["account_id"] in client.get("/api/accounting/store").json()["accounts"]


def test_put_account_fully_edits_an_account_with_no_postings(client) -> None:
    account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
    response = client.put(
        f"/api/accounting/accounts/{account['account_id']}",
        json={"name": "BNP Main", "institution": "BNP Paribas", "kind": "savings", "currency": "USD"},
    )
    assert response.status_code == 200
    updated = client.get("/api/accounting/store").json()["accounts"][account["account_id"]]
    assert updated["institution"] == "BNP Paribas"
    assert updated["kind"] == "savings"
    assert updated["currency"] == "USD"


def test_put_account_updates_last_four_independently_and_it_round_trips_through_store(client) -> None:
    account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="USD")
    assert account["last_four"] is None

    response = client.put(
        f"/api/accounting/accounts/{account['account_id']}",
        json={"name": "BNP Checking", "institution": "BNP", "kind": "checking", "currency": "USD", "last_four": "4321"},
    )
    assert response.status_code == 200
    assert response.json()["last_four"] == "4321"

    store = client.get("/api/accounting/store").json()
    assert store["accounts"][account["account_id"]]["last_four"] == "4321"


def test_put_account_blocks_locked_field_changes_once_it_has_postings(client) -> None:
    account_id = _import_chase_checking(client)
    response = client.put(
        f"/api/accounting/accounts/{account_id}",
        json={"name": "Chase Checking", "institution": "Chase", "kind": "savings", "currency": "USD"},
    )
    assert response.status_code == 400

    renamed = client.put(
        f"/api/accounting/accounts/{account_id}",
        json={"name": "Renamed", "institution": "Chase", "kind": "checking", "currency": "USD"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"


def test_post_account_accepts_an_external_investment_pulling_from_trades(client) -> None:
    response = client.post(
        "/api/accounting/accounts",
        json={
            "name": "Interactive Brokers",
            "kind": "external_investment",
            "institution": "external",
            "currency": "USD",
            "external_ref": "trades",
        },
    )
    assert response.status_code == 200
    account_id = response.json()["account_id"]
    created = client.get("/api/accounting/store").json()["accounts"][account_id]
    assert created["external_ref"] == "trades"


def test_post_account_accepts_a_manually_tracked_external_investment(client) -> None:
    response = client.post(
        "/api/accounting/accounts",
        json={"name": "Friend's Fund", "kind": "external_investment", "institution": "external", "currency": "USD"},
    )
    assert response.status_code == 200
    account_id = response.json()["account_id"]
    created = client.get("/api/accounting/store").json()["accounts"][account_id]
    assert created["external_ref"] is None


def test_put_account_can_switch_an_external_investment_between_trades_and_manual(client) -> None:
    account = _create_account(
        client, name="Friend's Fund", kind="external_investment", institution="external", currency="USD"
    )
    response = client.put(
        f"/api/accounting/accounts/{account['account_id']}",
        json={
            "name": "Friend's Fund",
            "institution": "external",
            "kind": "external_investment",
            "currency": "USD",
            "external_ref": "trades",
        },
    )
    assert response.status_code == 200
    assert response.json()["external_ref"] == "trades"

    back_to_manual = client.put(
        f"/api/accounting/accounts/{account['account_id']}",
        json={"name": "Friend's Fund", "institution": "external", "kind": "external_investment", "currency": "USD"},
    )
    assert back_to_manual.status_code == 200
    assert back_to_manual.json()["external_ref"] is None


def test_ledger_export_returns_every_raw_posting_unresolved_by_rules(client) -> None:
    account_id = _import_chase_checking(client)
    # A rule that would repoint the payroll deposit's placeholder leg to a
    # real account — the raw ledger export must stay unaffected by it,
    # unlike `GET /postings` (the resolved view `TransactionsTab` shows).
    client.put(
        "/api/accounting/transfer-rules",
        json=[
            {
                "rule_id": "payroll-rule",
                "description_contains": "SOME EMPLOYER PAYROLL",
                "account_id": None,
                "category_id": None,
                "subcategory_id": None,
                "counterparty_account_id": account_id,
                "priority": 0,
                "description": "",
            }
        ],
    )
    response = client.get("/api/accounting/ledger/export")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 4  # two transactions, two postings each
    placeholder_legs = [row for row in body if row["account_id"] == "uncategorized:income"]
    assert len(placeholder_legs) == 1  # still on the placeholder — the rule never touched this export


def test_accounting_statements_export_returns_a_zip_of_every_raw_upload(client) -> None:
    _import_chase_checking(client)
    response = client.get("/api/accounting/statements/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    zip_file = zipfile.ZipFile(io.BytesIO(response.content))
    assert any(name.endswith(".csv") for name in zip_file.namelist())


def test_get_supported_import_kinds_lists_registered_standardizers(client) -> None:
    response = client.get("/api/accounting/supported-import-kinds")
    assert response.status_code == 200
    pairs = {(row["institution"], row["account_kind"]) for row in response.json()}
    assert ("Chase", "checking") in pairs
    assert ("BNP", "checking") not in pairs


def test_put_opening_balance_is_reflected_in_net_worth(client) -> None:
    account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="USD")
    response = client.put(
        f"/api/accounting/accounts/{account['account_id']}/opening-balance",
        json={"account_id": account["account_id"], "amount": 250.0, "as_of_date": "2026-01-01T00:00:00"},
    )
    assert response.status_code == 200
    net_worth = client.get("/api/accounting/net-worth", params={"as_of": "2026-06-01"}).json()
    row = next(r for r in net_worth["accounts"] if r["account_id"] == account["account_id"])
    assert row["balance"] == pytest.approx(250.0)

    delete_response = client.delete(f"/api/accounting/accounts/{account['account_id']}/opening-balance")
    assert delete_response.status_code == 200
    net_worth_after = client.get("/api/accounting/net-worth", params={"as_of": "2026-06-01"}).json()
    row_after = next(r for r in net_worth_after["accounts"] if r["account_id"] == account["account_id"])
    assert row_after["balance"] == pytest.approx(0.0)


def test_net_worth_history_by_account_returns_a_row_per_account_per_date(client) -> None:
    account_id = _import_chase_checking(client)
    response = client.get(
        "/api/accounting/net-worth/history/by-account",
        params={"start": "2026-06-28", "end": "2026-06-30", "interval_days": 1},
    )
    assert response.status_code == 200
    rows = response.json()
    dates = {row["date"] for row in rows if row["account_id"] == account_id}
    assert dates == {"2026-06-28", "2026-06-29", "2026-06-30"}


def test_delete_account_removes_an_account_with_no_postings(client) -> None:
    account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="EUR")
    response = client.delete(f"/api/accounting/accounts/{account['account_id']}")
    assert response.status_code == 200
    assert account["account_id"] not in client.get("/api/accounting/store").json()["accounts"]


def test_delete_account_blocked_once_it_has_postings(client) -> None:
    account_id = _import_chase_checking(client)
    response = client.delete(f"/api/accounting/accounts/{account_id}")
    assert response.status_code == 400
    assert account_id in client.get("/api/accounting/store").json()["accounts"]


def test_close_account_marks_it_closed_with_no_transfers(client) -> None:
    account = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="USD")
    response = client.post(f"/api/accounting/accounts/{account['account_id']}/close", json={"transfers": []})
    assert response.status_code == 200
    assert client.get("/api/accounting/store").json()["accounts"][account["account_id"]]["closed"] is True


def test_close_account_records_a_transfer_that_shows_up_as_real_postings(client) -> None:
    checking = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="USD")
    savings = _create_account(client, name="BNP Savings", kind="savings", institution="BNP", currency="USD")
    response = client.post(
        f"/api/accounting/accounts/{checking['account_id']}/close",
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
    store = client.get("/api/accounting/store").json()
    assert store["accounts"][checking["account_id"]]["closed"] is True
    assert len(store["manual_transfers"]) == 1

    net_worth = client.get("/api/accounting/net-worth", params={"as_of": "2026-07-01"}).json()
    balances = {row["account_id"]: row["balance"] for row in net_worth["accounts"]}
    assert balances[checking["account_id"]] == pytest.approx(-100.0)
    assert balances[savings["account_id"]] == pytest.approx(100.0)


def test_close_account_rejects_a_transfer_whose_from_account_doesnt_match(client) -> None:
    checking = _create_account(client, name="BNP Checking", kind="checking", institution="BNP", currency="USD")
    savings = _create_account(client, name="BNP Savings", kind="savings", institution="BNP", currency="USD")
    response = client.post(
        f"/api/accounting/accounts/{checking['account_id']}/close",
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
        f"/api/accounting/accounts/{checking['account_id']}/close",
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
    client.post(f"/api/accounting/accounts/{account['account_id']}/close", json={"transfers": []})
    response = client.post(f"/api/accounting/accounts/{account['account_id']}/reopen")
    assert response.status_code == 200
    assert client.get("/api/accounting/store").json()["accounts"][account["account_id"]]["closed"] is False


def test_net_worth_history_returns_one_point_per_interval(client) -> None:
    _import_chase_checking(client)
    response = client.get(
        "/api/accounting/net-worth/history", params={"start": "2026-06-29", "end": "2026-06-30", "interval_days": 1}
    )
    assert response.status_code == 200
    points = response.json()
    assert [point["date"] for point in points] == ["2026-06-29", "2026-06-30"]
    assert points[-1]["net_worth"] == pytest.approx(1430.0)


def test_category_totals_buckets_uncategorized_payroll_as_income(client) -> None:
    _import_chase_checking(client)
    response = client.get(
        "/api/accounting/income-statement/category-totals", params={"start": "2026-06-01", "end": "2026-06-30"}
    )
    assert response.status_code == 200
    totals = response.json()
    income_row = next(row for row in totals if row["classification"] == "income")
    assert income_row["amount"] == pytest.approx(1500.0)


def test_category_totals_excludes_unconfirmed_pending_suggestions(client, monkeypatch) -> None:
    account_id = _import_chase_checking(client)
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == account_id and p["amount"] > 0)

    fake_response = '{"category_id": "income:salary", "subcategory_id": null}'
    monkeypatch.setattr(
        accounting_llm_router, "_llm_providers", lambda session, user_id: [_FakeLLMProvider(fake_response)]
    )
    client.post(f"/api/accounting/postings/{payroll['posting_id']}/ai-suggest-category")

    response = client.get(
        "/api/accounting/income-statement/category-totals", params={"start": "2026-06-01", "end": "2026-06-30"}
    )
    totals = response.json()
    assert all(row["category_id"] != "income:salary" for row in totals)
    income_row = next(row for row in totals if row["classification"] == "income")
    assert income_row["category_name"] == "Uncategorized"
    assert income_row["amount"] == pytest.approx(1500.0)

    client.post("/api/accounting/postings/validate-pending", json={"posting_ids": [payroll["posting_id"]]})
    response = client.get(
        "/api/accounting/income-statement/category-totals", params={"start": "2026-06-01", "end": "2026-06-30"}
    )
    totals = response.json()
    salary_row = next(row for row in totals if row["category_id"] == "income:salary")
    assert salary_row["amount"] == pytest.approx(1500.0)


def test_get_simulator_projection_computes_compound_growth(client) -> None:
    response = client.get(
        "/api/accounting/simulator/project",
        params={"initial_capital": 1000.0, "monthly_contribution": 0.0, "horizon_years": 1, "annual_rate_pct": 12.0},
    )
    assert response.status_code == 200
    points = response.json()
    assert points[0]["balance"] == pytest.approx(1000.0)
    assert points[12]["balance"] == pytest.approx(1000.0 * (1.01**12))


def test_put_simulator_scenarios_persists(client) -> None:
    response = client.put(
        "/api/accounting/simulator/scenarios",
        json=[
            {
                "scenario_id": "s1",
                "name": "Base case",
                "initial_capital": 1000.0,
                "monthly_contribution": 100.0,
                "horizon_years": 10,
                "annual_rate_pct": 6.0,
            }
        ],
    )
    assert response.status_code == 200
    store = client.get("/api/accounting/store").json()
    assert store["simulator_scenarios"][0]["name"] == "Base case"


def test_interest_summary_reports_savings_interest_earned(client, db_session) -> None:
    account = _create_account(client, name="SoFi Savings", kind="savings", institution="SoFi")
    # Seeded directly as a posting, not via `importers.sofi.statement_pdf`'s
    # real PDF/text parser — this test is checking that the interest-summary
    # endpoint correctly aggregates an already-categorized "Interest Earned"
    # posting, not exercising SoFi statement parsing (see
    # tests/accounting/importers/sofi/test_statement_pdf.py for that).
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
    frame = pl.DataFrame([interest_posting.model_dump(mode="python")], schema=Posting.polars_schema)
    ingest_module._write_ledger(frame, db_session, user_id=DEFAULT_USER_ID)

    response = client.get("/api/accounting/interest-summary", params={"as_of": "2026-04-30"})
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
        "/api/accounting/income-statement/monthly", params={"start": "2026-06-01", "end": "2026-06-30"}
    )
    assert response.status_code == 200
    month = response.json()[0]
    assert month["income"] == pytest.approx(1500.0)
    assert month["expense"] == pytest.approx(70.0)


def test_get_store_returns_a_version(client) -> None:
    response = client.get("/api/accounting/store")
    assert response.status_code == 200
    assert isinstance(response.json()["version"], int)


def test_mutation_with_the_current_expected_version_succeeds_and_bumps(client) -> None:
    version = client.get("/api/accounting/store").json()["version"]
    response = client.post(
        "/api/accounting/categories",
        json={"name": "Custom", "classification": "expense", "color": "#000000"},
        headers={"X-Expected-Store-Version": str(version)},
    )
    assert response.status_code == 200
    assert client.get("/api/accounting/store").json()["version"] == version + 1


def test_mutation_with_a_stale_expected_version_409s(client) -> None:
    version = client.get("/api/accounting/store").json()["version"]
    # Someone else's save lands first.
    client.post("/api/accounting/categories", json={"name": "Other", "classification": "expense", "color": "#111111"})

    response = client.post(
        "/api/accounting/categories",
        json={"name": "Custom", "classification": "expense", "color": "#000000"},
        headers={"X-Expected-Store-Version": str(version)},
    )
    assert response.status_code == 409
    assert "changed elsewhere" in response.json()["detail"]


def test_mutation_with_no_expected_version_header_still_succeeds(client) -> None:
    response = client.post(
        "/api/accounting/categories", json={"name": "Custom", "classification": "expense", "color": "#000000"}
    )
    assert response.status_code == 200
