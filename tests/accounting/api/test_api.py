import io
import zipfile
from datetime import date, timedelta

import polars as pl
import pytest
import xlsxwriter
from fastapi.testclient import TestClient

import db.models as dbm
from accounting import api as accounting_api
from accounting.api.routers import imports as accounting_imports_router
from accounting.api.routers import llm as accounting_llm_router
from accounting.config import AccountingConfig
from accounting.importers import ingest as ingest_module
from accounting.importers.sofi.statement_pdf import standardize_sofi_statement_text
from accounting.market_data import exchange_rates
from accounting.market_data.exchange_rates import RATE_HISTORY_SCHEMA
from db.current_user import DEFAULT_USER_ID
from db.session import get_db
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

    Every accounting route defaults to `db.current_user.DEFAULT_USER_ID`
    (there's no login flow yet — see `accounting.store.load_store`), so the
    one `User` row FK-satisfying every table has to exist under that exact
    id, not a random `test_user_id` (that fixture is for tests that call
    store/ledger functions directly with an explicit `user_id`).
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
    assert body["account_id"] == "chase:checking:9579"


def test_detect_returns_none_for_an_unknown_shape(client) -> None:
    body = client.post("/api/accounting/detect", json={"header": ["A", "B"], "filename": "x.csv"}).json()
    assert body is None


def test_import_registers_a_new_account_and_ingests_postings(client) -> None:
    response = client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == "chase:checking:9579"
    assert body["new_posting_count"] == 4

    store = client.get("/api/accounting/store").json()
    assert "chase:checking:9579" in store["accounts"]


def test_import_unsupported_institution_is_a_400(client) -> None:
    response = client.post(
        "/api/accounting/import",
        files={"file": ("x.csv", "a,b\n1,2\n", "text/csv")},
        data={
            "institution": "BankOfAmerica",
            "account_kind": "checking",
            "account_id": "boa:checking:0000",
            "account_name": "BoA Checking",
        },
    )
    assert response.status_code == 400


def test_canonical_import_registers_a_new_account_and_creates_a_category(client) -> None:
    csv_text = (
        "Date,Description,Amount,Category\n2026-06-30,Grocery Store,-42.50,Groceries\n2026-06-29,Paycheck,1500.00,\n"
    )
    response = client.post(
        "/api/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": "generic-bank:checking:0001",
            "account_name": "Generic Checking",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == "generic-bank:checking:0001"
    assert body["new_posting_count"] == 4
    assert [category["name"] for category in body["new_categories"]] == ["Groceries"]

    store = client.get("/api/accounting/store").json()
    assert "generic-bank:checking:0001" in store["accounts"]
    assert any(category["name"] == "Groceries" for category in store["categories"].values())


def test_canonical_import_handles_a_utf8_bom_prefixed_file(client) -> None:
    csv_text = "﻿Date,Description,Amount\n2026-06-30,Grocery Store,-42.50\n"
    response = client.post(
        "/api/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text.encode("utf-8"), "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": "generic-bank:checking:0002",
            "account_name": "Generic Checking",
        },
    )
    assert response.status_code == 200
    assert response.json()["new_posting_count"] == 2


def test_canonical_import_accepts_an_xlsx_file(client) -> None:
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
            "account_id": "generic-bank:checking:0003",
            "account_name": "Generic Checking",
        },
    )
    assert response.status_code == 200
    assert response.json()["new_posting_count"] == 2


def test_canonical_import_date_order_dmy_reads_day_first(client) -> None:
    csv_text = "Date,Description,Amount\n01/12/2026,Grocery Store,-42.50\n"
    response = client.post(
        "/api/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": "generic-bank:checking:0004",
            "account_name": "Generic Checking",
            "date_order": "DMY",
        },
    )
    assert response.status_code == 200
    postings = client.get("/api/accounting/postings").json()
    real_leg = next(p for p in postings if p["account_id"] == "generic-bank:checking:0004")
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
    csv_text = (
        "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,Groceries\n2026-06-29,Restaurant,-20.00,Dining\n"
    )
    response = client.post(
        "/api/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": "generic-bank:checking:0006",
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
    response = client.post(
        "/api/accounting/import/canonical",
        files={"file": ("bad.csv", "Foo,Bar\n1,2\n", "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": "generic-bank:checking:0002",
            "account_name": "Generic Checking",
        },
    )
    assert response.status_code == 422
    assert "Date" in response.json()["detail"]


def _import_chase_checking(client, account_id: str = "chase:checking:9579") -> None:
    response = client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": account_id,
            "account_name": "Chase Checking",
        },
    )
    assert response.status_code == 200


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
    _import_chase_checking(client)
    # "Freelance Gig Income" isn't one of the default-seeded category names (unlike "Salary"),
    # so its absence afterward actually proves the preview created nothing.
    sheet_csv = "Date,Description,Amount,Category\n06/30/2026,Payroll,1500.00,Freelance Gig Income\n"
    client.post(
        "/api/accounting/import/categorize-from-file/preview",
        files={"file": ("my-sheet.csv", sheet_csv, "text/csv")},
    )
    postings = client.get("/api/accounting/postings").json()
    real_leg = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)
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
    _import_chase_checking(client)
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
    real_leg = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)
    assert real_leg["category_id"] is not None
    store = client.get("/api/accounting/store").json()
    assert store["categories"][real_leg["category_id"]]["name"] == "Salary"

    # Never creates a new transaction — same two postings as right after the original import.
    assert len(postings) == 4


def test_categorize_from_file_apply_skips_rows_not_confirmed(client) -> None:
    _import_chase_checking(client)
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
    payroll_leg = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)
    payment_leg = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] < 0)
    assert payroll_leg["category_id"] is not None
    assert payment_leg["category_id"] is None


def test_categorize_from_file_apply_never_matches_the_same_posting_twice(client) -> None:
    # Two real, distinct $5 coffees on the same day — matching must not collapse them onto one posting.
    _import_chase_checking(client)
    two_coffees_csv = (
        "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
        "DEBIT,06/28/2026,COFFEE SHOP,-5.00,DEBIT_CARD,2500.00,,\n"
        "DEBIT,06/28/2026,COFFEE SHOP,-5.00,DEBIT_CARD,2495.00,,\n"
    )
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579-2.csv", two_coffees_csv, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking",
        },
    )
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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)
    assert payroll["category_id"] is None  # generic payroll text doesn't match the EQORE-specific seed rule


def test_manual_override_wins_over_no_rule_match(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)

    response = client.put(
        f"/api/accounting/postings/{payroll['posting_id']}/override", json={"category_id": "income:salary"}
    )
    assert response.status_code == 200

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["posting_id"] == payroll["posting_id"])
    assert updated_payroll["category_id"] == "income:salary"


def test_setting_a_subcategory_after_a_category_preserves_the_category(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)
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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)

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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)

    response = client.put(
        f"/api/accounting/postings/{payroll['posting_id']}/split",
        json=[{"amount": 100.0}, {"amount": 100.0}],
    )
    assert response.status_code == 400


def test_delete_posting_split_restores_the_original_posting(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)

    client.put(
        f"/api/accounting/postings/{payroll['posting_id']}/split",
        json=[{"amount": 1000.0}, {"amount": 500.0}],
    )
    client.delete(f"/api/accounting/postings/{payroll['posting_id']}/split")

    updated = client.get("/api/accounting/postings").json()
    assert any(p["posting_id"] == payroll["posting_id"] for p in updated)


def test_import_paystub_reconciles_against_a_matching_bank_posting(client, monkeypatch) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
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
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)
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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)

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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)

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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)
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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)
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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)

    monkeypatch.setattr(accounting_llm_router, "_llm_providers", lambda session, user_id: [])
    response = client.post(f"/api/accounting/postings/{payroll['posting_id']}/ai-suggest-category")
    assert response.status_code == 503


def test_ai_suggest_category_404s_for_an_unknown_posting(client, monkeypatch) -> None:
    monkeypatch.setattr(accounting_llm_router, "_llm_providers", lambda session, user_id: [])
    response = client.post("/api/accounting/postings/does-not-exist/ai-suggest-category")
    assert response.status_code == 404


def test_ai_suggest_category_marks_the_posting_pending_until_validated(client, monkeypatch) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)

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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)

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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)
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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)

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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)

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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)

    response = client.post(f"/api/accounting/postings/{payroll['posting_id']}/pattern-suggest-category")
    assert response.json() == {"category_id": None, "subcategory_id": None, "applied": False}


def test_pattern_suggest_category_bulk_stages_suggestions_for_many_postings_in_one_call(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)
    card_payment = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] < 0)

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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)
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

    from accounting.llm.usage import record_call  # noqa: PLC0415

    record_call("gemini", db_session, DEFAULT_USER_ID, error="429 RESOURCE_EXHAUSTED")

    body = client.get("/api/accounting/llm-usage").json()
    assert body["gemini"] == {
        "configured": True,
        # A failed call freezes the counter rather than incrementing it —
        # see test_accounting_llm_usage.py's record_call tests.
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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    body = client.get("/api/accounting/net-worth").json()
    assert body["assets"] == pytest.approx(1430.0)
    checking_row = next(row for row in body["accounts"] if row["account_id"] == "chase:checking:9579")
    assert checking_row["balance"] == pytest.approx(1430.0)


def test_net_worth_degrades_gracefully_when_trades_has_never_been_synced(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(trades_api.app.state, "config", AppConfig(ibkr={"cache_dir": tmp_path / "empty-ibkr"}))
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "external:interactive-brokers",
            "name": "Interactive Brokers",
            "kind": "external_investment",
            "institution": "external",
            "currency": "USD",
            "parent_account_id": None,
            "external_ref": "trades",
            "meta": {},
        },
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
                "counterparty_account_id": "external:interactive-brokers",
                "priority": 0,
                "description": "",
            }
        ],
    )
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
            "account_id": "sofi:savings:3680",
            "account_name": "SoFi Savings (...3680)",
        },
    )
    response = client.get("/api/accounting/net-worth")
    assert response.status_code == 200
    body = response.json()
    investment_row = next(row for row in body["accounts"] if row["account_id"] == "external:interactive-brokers")
    assert investment_row["balance"] == pytest.approx(0.0)


def test_transfer_suggestions_finds_the_chase_card_payoff(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    credit_card_csv = (
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
        "06/29/2026,06/29/2026,Something else entirely,Other,Sale,70.00,\n"
    )
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase1234.csv", credit_card_csv, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "credit_card",
            "account_id": "chase:credit_card:1234",
            "account_name": "Chase Credit Card (...1234)",
        },
    )
    suggestions = client.get("/api/accounting/transfer-suggestions").json()
    assert len(suggestions) == 1
    assert {suggestions[0]["account_id"], suggestions[0]["other_account_id"]} == {
        "chase:checking:9579",
        "chase:credit_card:1234",
    }
    assert suggestions[0]["description"]
    assert suggestions[0]["other_description"]


def test_transfer_suggestions_respects_a_wider_window_days(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    credit_card_csv = (
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
        "06/24/2026,06/24/2026,Something else entirely,Other,Sale,70.00,\n"
    )
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase1234.csv", credit_card_csv, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "credit_card",
            "account_id": "chase:credit_card:1234",
            "account_name": "Chase Credit Card (...1234)",
        },
    )
    # The two postings are 5 days apart (06/24 vs 06/29) — outside the
    # default 3-day window, but within a wider one.
    assert client.get("/api/accounting/transfer-suggestions").json() == []
    wider = client.get("/api/accounting/transfer-suggestions", params={"window_days": 7}).json()
    assert len(wider) == 1


def test_duplicate_suggestions_finds_the_same_purchase_imported_from_two_sources(client) -> None:
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "generic:checking:0001",
            "name": "Generic Checking",
            "kind": "checking",
            "institution": "Generic",
            "currency": "USD",
        },
    )
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
            "account_id": "generic:checking:0001",
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
            "account_id": "generic:checking:0001",
            "account_name": "Generic Checking",
            "separator": ",",
        },
    )
    suggestions = client.get("/api/accounting/duplicate-suggestions").json()
    assert len(suggestions) == 1
    group = suggestions[0]
    assert group["account_id"] == "generic:checking:0001"
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
        posting["transaction_id"] for posting in postings if posting["account_id"] == "generic:checking:0001"
    }
    assert remaining_transaction_ids == {transaction_ids[0]}


def _seed_chase_transfer_suggestion(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    credit_card_csv = (
        "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
        "06/29/2026,06/29/2026,Something else entirely,Other,Sale,70.00,\n"
    )
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase1234.csv", credit_card_csv, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "credit_card",
            "account_id": "chase:credit_card:1234",
            "account_name": "Chase Credit Card (...1234)",
        },
    )


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
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "generic:checking:0001",
            "name": "Generic Checking",
            "kind": "checking",
            "institution": "Generic",
            "currency": "USD",
        },
    )
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("a.csv", "Date,Description,Amount\n2026-06-30,WHOLE FOODS #123,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": "generic:checking:0001",
            "account_name": "Generic Checking",
        },
    )
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("b.csv", "Date,Description,Amount\n2026-06-30,Whole Foods Market,-42.50\n", "text/csv")},
        data={
            "institution": "Generic",
            "account_kind": "checking",
            "account_id": "generic:checking:0001",
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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)
    assert payroll["resolved_by_transfer_rule_id"] is None

    client.put(
        "/api/accounting/transfer-rules",
        json=[
            {
                "rule_id": "payroll-rule",
                "description_contains": "PAYROLL",
                "counterparty_account_id": "employer:eqore",
                "category_id": "income:salary",
                "priority": 0,
            }
        ],
    )
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "employer:eqore",
            "name": "EQORE",
            "kind": "income_source",
            "institution": "internal",
            "currency": "USD",
        },
    )

    updated = client.get("/api/accounting/postings").json()
    updated_payroll = next(p for p in updated if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)
    assert updated_payroll["resolved_by_transfer_rule_id"] == "payroll-rule"
    assert updated_payroll["category_id"] == "income:salary"


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
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,Nourriture\n"
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": "generic-bank:checking:0001",
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
    grocery_leg = next(p for p in postings if p["account_id"] == "generic-bank:checking:0001")
    assert grocery_leg["category_id"] == "expense:food"


def test_category_rename_merge_repoints_a_manual_override(client) -> None:
    csv_text = "Date,Description,Amount,Category\n2026-06-30,Store,-42.50,Nourriture\n"
    client.post(
        "/api/accounting/import/canonical",
        files={"file": ("generic.csv", csv_text, "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": "generic-bank:checking:0001",
            "account_name": "Generic Checking",
        },
    )
    store = client.get("/api/accounting/store").json()
    nourriture = next(c for c in store["categories"].values() if c["name"] == "Nourriture")
    postings = client.get("/api/accounting/postings").json()
    other_posting = next(p for p in postings if p["account_id"] != "generic-bank:checking:0001")
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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
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


def test_get_suggested_budget_amount_returns_zero_with_no_history(client) -> None:
    response = client.get(
        "/api/accounting/budgets/suggested-amount", params={"category_id": "expense:food-drink", "month": "2026-06"}
    )
    assert response.status_code == 200
    assert response.json()["suggested_amount"] == pytest.approx(0.0)


def test_rebuild_with_nothing_imported_is_a_404(client) -> None:
    assert client.post("/api/accounting/rebuild").status_code == 404


def test_rebuild_reconstructs_from_raw_after_import(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
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


def test_sync_exchange_rates_persists_the_fetched_history(client, monkeypatch) -> None:
    _mock_fetch(monkeypatch)
    response = client.post("/api/accounting/sync-exchange-rates")
    assert response.status_code == 200
    assert response.json()["rates_to_base"]["EUR"] == pytest.approx(2.0)


def test_net_worth_succeeds_for_a_eur_account_once_rates_are_synced(client, monkeypatch) -> None:
    _mock_fetch(monkeypatch)
    client.post("/api/accounting/sync-exchange-rates")
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "bnp:checking:0001",
            "name": "BNP Checking",
            "kind": "checking",
            "institution": "BNP",
            "currency": "EUR",
        },
    )
    response = client.get("/api/accounting/net-worth")
    assert response.status_code == 200


def test_net_worth_400s_when_a_needed_currency_was_never_synced(client) -> None:
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "bnp:checking:0001",
            "name": "BNP Checking",
            "kind": "checking",
            "institution": "BNP",
            "currency": "EUR",
        },
    )
    response = client.get("/api/accounting/net-worth")
    assert response.status_code == 400


def test_get_current_exchange_rate_after_sync(client, monkeypatch) -> None:
    _mock_fetch(monkeypatch)
    client.post("/api/accounting/sync-exchange-rates")
    response = client.get("/api/accounting/exchange-rates/current", params={"currency": "EUR"})
    assert response.status_code == 200
    body = response.json()
    assert body["rate_to_base"] == pytest.approx(2.0)
    assert body["base_currency"] == "USD"


def test_get_exchange_rate_history_after_sync(client, monkeypatch) -> None:
    _mock_fetch(monkeypatch)
    client.post("/api/accounting/sync-exchange-rates")
    response = client.get("/api/accounting/exchange-rates/history", params={"currency": "EUR"})
    assert response.status_code == 200
    body = response.json()
    assert [row["rate"] for row in body] == pytest.approx([1.9, 2.1])


def test_post_account_creates_a_new_account(client) -> None:
    response = client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "bnp:checking:0001",
            "name": "BNP Checking",
            "kind": "checking",
            "institution": "BNP",
            "currency": "EUR",
        },
    )
    assert response.status_code == 200
    assert "bnp:checking:0001" in client.get("/api/accounting/store").json()["accounts"]


def test_post_account_conflicts_on_a_duplicate_id(client) -> None:
    account = {
        "account_id": "bnp:checking:0001",
        "name": "BNP Checking",
        "kind": "checking",
        "institution": "BNP",
        "currency": "EUR",
    }
    client.post("/api/accounting/accounts", json=account)
    response = client.post("/api/accounting/accounts", json=account)
    assert response.status_code == 409


def test_put_account_fully_edits_an_account_with_no_postings(client) -> None:
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "bnp:checking:0001",
            "name": "BNP Checking",
            "kind": "checking",
            "institution": "BNP",
            "currency": "EUR",
        },
    )
    response = client.put(
        "/api/accounting/accounts/bnp:checking:0001",
        json={"name": "BNP Main", "institution": "BNP Paribas", "kind": "savings", "currency": "USD"},
    )
    assert response.status_code == 200
    updated = client.get("/api/accounting/store").json()["accounts"]["bnp:checking:0001"]
    assert updated["institution"] == "BNP Paribas"
    assert updated["kind"] == "savings"
    assert updated["currency"] == "USD"


def test_put_account_blocks_locked_field_changes_once_it_has_postings(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    response = client.put(
        "/api/accounting/accounts/chase:checking:9579",
        json={"name": "Chase Checking", "institution": "Chase", "kind": "savings", "currency": "USD"},
    )
    assert response.status_code == 400

    renamed = client.put(
        "/api/accounting/accounts/chase:checking:9579",
        json={"name": "Renamed", "institution": "Chase", "kind": "checking", "currency": "USD"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"


def test_post_account_accepts_an_external_investment_pulling_from_trades(client) -> None:
    response = client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "external:interactive-brokers",
            "name": "Interactive Brokers",
            "kind": "external_investment",
            "institution": "external",
            "currency": "USD",
            "external_ref": "trades",
        },
    )
    assert response.status_code == 200
    created = client.get("/api/accounting/store").json()["accounts"]["external:interactive-brokers"]
    assert created["external_ref"] == "trades"


def test_post_account_accepts_a_manually_tracked_external_investment(client) -> None:
    response = client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "external:friends-fund",
            "name": "Friend's Fund",
            "kind": "external_investment",
            "institution": "external",
            "currency": "USD",
        },
    )
    assert response.status_code == 200
    created = client.get("/api/accounting/store").json()["accounts"]["external:friends-fund"]
    assert created["external_ref"] is None


def test_put_account_can_switch_an_external_investment_between_trades_and_manual(client) -> None:
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "external:friends-fund",
            "name": "Friend's Fund",
            "kind": "external_investment",
            "institution": "external",
            "currency": "USD",
        },
    )
    response = client.put(
        "/api/accounting/accounts/external:friends-fund",
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
        "/api/accounting/accounts/external:friends-fund",
        json={"name": "Friend's Fund", "institution": "external", "kind": "external_investment", "currency": "USD"},
    )
    assert back_to_manual.status_code == 200
    assert back_to_manual.json()["external_ref"] is None


def test_ledger_export_returns_every_raw_posting_unresolved_by_rules(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
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
                "counterparty_account_id": "chase:checking:9579",
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
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
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
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "bnp:checking:0001",
            "name": "BNP Checking",
            "kind": "checking",
            "institution": "BNP",
            "currency": "USD",
        },
    )
    response = client.put(
        "/api/accounting/accounts/bnp:checking:0001/opening-balance",
        json={"account_id": "bnp:checking:0001", "amount": 250.0, "as_of_date": "2026-01-01T00:00:00"},
    )
    assert response.status_code == 200
    net_worth = client.get("/api/accounting/net-worth", params={"as_of": "2026-06-01"}).json()
    row = next(r for r in net_worth["accounts"] if r["account_id"] == "bnp:checking:0001")
    assert row["balance"] == pytest.approx(250.0)

    delete_response = client.delete("/api/accounting/accounts/bnp:checking:0001/opening-balance")
    assert delete_response.status_code == 200
    net_worth_after = client.get("/api/accounting/net-worth", params={"as_of": "2026-06-01"}).json()
    row_after = next(r for r in net_worth_after["accounts"] if r["account_id"] == "bnp:checking:0001")
    assert row_after["balance"] == pytest.approx(0.0)


def test_net_worth_history_by_account_returns_a_row_per_account_per_date(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    response = client.get(
        "/api/accounting/net-worth/history/by-account",
        params={"start": "2026-06-28", "end": "2026-06-30", "interval_days": 1},
    )
    assert response.status_code == 200
    rows = response.json()
    dates = {row["date"] for row in rows if row["account_id"] == "chase:checking:9579"}
    assert dates == {"2026-06-28", "2026-06-29", "2026-06-30"}


def test_delete_account_removes_an_account_with_no_postings(client) -> None:
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "bnp:checking:0001",
            "name": "BNP Checking",
            "kind": "checking",
            "institution": "BNP",
            "currency": "EUR",
        },
    )
    response = client.delete("/api/accounting/accounts/bnp:checking:0001")
    assert response.status_code == 200
    assert "bnp:checking:0001" not in client.get("/api/accounting/store").json()["accounts"]


def test_delete_account_blocked_once_it_has_postings(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    response = client.delete("/api/accounting/accounts/chase:checking:9579")
    assert response.status_code == 400
    assert "chase:checking:9579" in client.get("/api/accounting/store").json()["accounts"]


def test_close_account_marks_it_closed_with_no_transfers(client) -> None:
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "bnp:checking:0001",
            "name": "BNP Checking",
            "kind": "checking",
            "institution": "BNP",
            "currency": "USD",
        },
    )
    response = client.post("/api/accounting/accounts/bnp:checking:0001/close", json={"transfers": []})
    assert response.status_code == 200
    assert client.get("/api/accounting/store").json()["accounts"]["bnp:checking:0001"]["closed"] is True


def test_close_account_records_a_transfer_that_shows_up_as_real_postings(client) -> None:
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "bnp:checking:0001",
            "name": "BNP Checking",
            "kind": "checking",
            "institution": "BNP",
            "currency": "USD",
        },
    )
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "bnp:savings:0002",
            "name": "BNP Savings",
            "kind": "savings",
            "institution": "BNP",
            "currency": "USD",
        },
    )
    response = client.post(
        "/api/accounting/accounts/bnp:checking:0001/close",
        json={
            "transfers": [
                {
                    "transfer_id": "close-bnp-checking-0001",
                    "date": "2026-06-30T00:00:00",
                    "from_account_id": "bnp:checking:0001",
                    "to_account_id": "bnp:savings:0002",
                    "from_amount": 100.0,
                    "to_amount": 100.0,
                    "description": "Closing out BNP checking",
                }
            ]
        },
    )
    assert response.status_code == 200
    store = client.get("/api/accounting/store").json()
    assert store["accounts"]["bnp:checking:0001"]["closed"] is True
    assert len(store["manual_transfers"]) == 1

    net_worth = client.get("/api/accounting/net-worth", params={"as_of": "2026-07-01"}).json()
    balances = {row["account_id"]: row["balance"] for row in net_worth["accounts"]}
    assert balances["bnp:checking:0001"] == pytest.approx(-100.0)
    assert balances["bnp:savings:0002"] == pytest.approx(100.0)


def test_close_account_rejects_a_transfer_whose_from_account_doesnt_match(client) -> None:
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "bnp:checking:0001",
            "name": "BNP Checking",
            "kind": "checking",
            "institution": "BNP",
            "currency": "USD",
        },
    )
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "bnp:savings:0002",
            "name": "BNP Savings",
            "kind": "savings",
            "institution": "BNP",
            "currency": "USD",
        },
    )
    response = client.post(
        "/api/accounting/accounts/bnp:checking:0001/close",
        json={
            "transfers": [
                {
                    "transfer_id": "close-bnp-checking-0001",
                    "date": "2026-06-30T00:00:00",
                    "from_account_id": "bnp:savings:0002",
                    "to_account_id": "bnp:checking:0001",
                    "from_amount": 100.0,
                    "to_amount": 100.0,
                }
            ]
        },
    )
    assert response.status_code == 400


def test_close_account_rejects_a_transfer_to_an_unknown_account(client) -> None:
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "bnp:checking:0001",
            "name": "BNP Checking",
            "kind": "checking",
            "institution": "BNP",
            "currency": "USD",
        },
    )
    response = client.post(
        "/api/accounting/accounts/bnp:checking:0001/close",
        json={
            "transfers": [
                {
                    "transfer_id": "close-bnp-checking-0001",
                    "date": "2026-06-30T00:00:00",
                    "from_account_id": "bnp:checking:0001",
                    "to_account_id": "does-not-exist",
                    "from_amount": 100.0,
                    "to_amount": 100.0,
                }
            ]
        },
    )
    assert response.status_code == 400


def test_reopen_account_clears_the_closed_flag_but_keeps_recorded_transfers(client) -> None:
    client.post(
        "/api/accounting/accounts",
        json={
            "account_id": "bnp:checking:0001",
            "name": "BNP Checking",
            "kind": "checking",
            "institution": "BNP",
            "currency": "USD",
        },
    )
    client.post("/api/accounting/accounts/bnp:checking:0001/close", json={"transfers": []})
    response = client.post("/api/accounting/accounts/bnp:checking:0001/reopen")
    assert response.status_code == 200
    assert client.get("/api/accounting/store").json()["accounts"]["bnp:checking:0001"]["closed"] is False


def test_net_worth_history_returns_one_point_per_interval(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    response = client.get(
        "/api/accounting/net-worth/history", params={"start": "2026-06-29", "end": "2026-06-30", "interval_days": 1}
    )
    assert response.status_code == 200
    points = response.json()
    assert [point["date"] for point in points] == ["2026-06-29", "2026-06-30"]
    assert points[-1]["net_worth"] == pytest.approx(1430.0)


def test_category_totals_buckets_uncategorized_payroll_as_income(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    response = client.get(
        "/api/accounting/income-statement/category-totals", params={"start": "2026-06-01", "end": "2026-06-30"}
    )
    assert response.status_code == 200
    totals = response.json()
    income_row = next(row for row in totals if row["classification"] == "income")
    assert income_row["amount"] == pytest.approx(1500.0)


def test_category_totals_excludes_unconfirmed_pending_suggestions(client, monkeypatch) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    postings = client.get("/api/accounting/postings").json()
    payroll = next(p for p in postings if p["account_id"] == "chase:checking:9579" and p["amount"] > 0)

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


def test_interest_summary_reports_savings_interest_earned(client, monkeypatch) -> None:
    statement_text = (
        "Savings Account - 3680\n"
        "DATE TYPE DESCRIPTION AMOUNT BALANCE\n"
        "Apr 30, 2026 Interest Earned Interest earned $7.70 $107.70\n"
        "Transaction ID: 50-1\n"
    )
    monkeypatch.setattr(
        ingest_module,
        "standardize_sofi_statement_pdf",
        lambda _pdf_bytes: standardize_sofi_statement_text(statement_text),
    )
    # New PDF imports are retired (see `importers.sofi.statement_pdf`'s
    # docstring) — archive the raw PDF directly, the way an old import
    # would have, and let a rebuild re-derive it instead.
    pdf_dir = accounting_api.state.config.raw_statement_dir / "SoFi" / "statement_pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    (pdf_dir / "statement.pdf").write_bytes(b"%PDF-fake")
    client.post("/api/accounting/rebuild")
    response = client.get("/api/accounting/interest-summary", params={"as_of": "2026-04-30"})
    assert response.status_code == 200
    rows = response.json()
    savings_row = next(row for row in rows if row["account_id"] == "sofi:savings:3680")
    assert savings_row["interest_earned_this_year"] == pytest.approx(7.70)
    # The fake statement has only this one row, so the ledger-derived balance
    # is the interest posting alone — not the statement's own BALANCE column,
    # which would need an earlier deposit row this fixture doesn't include.
    assert savings_row["current_balance"] == pytest.approx(7.70)


def test_monthly_income_expense_reports_both_sides(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    response = client.get(
        "/api/accounting/income-statement/monthly", params={"start": "2026-06-01", "end": "2026-06-30"}
    )
    assert response.status_code == 200
    month = response.json()[0]
    assert month["income"] == pytest.approx(1500.0)
    assert month["expense"] == pytest.approx(70.0)
