import pytest
from fastapi.testclient import TestClient

from accounting import api as accounting_api
from accounting.config import AccountingConfig
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


@pytest.fixture
def client():
    return TestClient(trades_api.app)


def test_get_store_seeds_default_categories_and_placeholder_accounts(client) -> None:
    body = client.get("/api/accounting/store").json()
    assert "expense:food-drink" in body["categories"]
    assert "uncategorized:expense" in body["accounts"]
    assert any(rule["rule_id"] == "eqore-payroll" for rule in body["rules"])


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
    assert body["assets_usd"] == pytest.approx(1430.0)
    checking_row = next(row for row in body["accounts"] if row["account_id"] == "chase:checking:9579")
    assert checking_row["balance_usd"] == pytest.approx(1430.0)


def test_net_worth_degrades_gracefully_when_trades_has_never_been_synced(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(trades_api.app.state, "config", AppConfig(ibkr={"cache_dir": tmp_path / "empty-ibkr"}))
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
    assert investment_row["balance_usd"] == pytest.approx(0.0)


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


def test_put_other_assets_persists(client) -> None:
    response = client.put(
        "/api/accounting/other-assets", json=[{"asset_id": "car", "name": "Car", "value_usd": 15000.0}]
    )
    assert response.status_code == 200
    body = client.get("/api/accounting/net-worth").json()
    assert body["other_assets_usd"] == pytest.approx(15000.0)


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
    assert response.json()["total_posting_count"] == 4
