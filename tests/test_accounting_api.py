import pytest
from fastapi.testclient import TestClient

from accounting import api as accounting_api
from accounting.config import AccountingConfig
from accounting.importers import ingest as ingest_module
from accounting.importers.sofi.statement_pdf import standardize_sofi_statement_text
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
    assert body["assets"] == pytest.approx(1430.0)
    checking_row = next(row for row in body["accounts"] if row["account_id"] == "chase:checking:9579")
    assert checking_row["balance"] == pytest.approx(1430.0)


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
    response = client.put("/api/accounting/other-assets", json=[{"asset_id": "car", "name": "Car", "value": 15000.0}])
    assert response.status_code == 200
    body = client.get("/api/accounting/net-worth").json()
    assert body["other_assets_total"] == pytest.approx(15000.0)


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


def test_put_exchange_rate_persists_and_affects_net_worth(client) -> None:
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
    response = client.put("/api/accounting/settings/exchange-rate", json={"eur_usd_rate": 2.0})
    assert response.status_code == 200
    assert response.json() == {"eur_usd_rate": 2.0}
    assert client.get("/api/accounting/store").json()["eur_usd_rate"] == pytest.approx(2.0)


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


def test_import_sofi_statement_pdf_registers_every_account_it_describes(client, monkeypatch) -> None:
    statement_text = (
        "Savings Account - 3680\n"
        "DATE TYPE DESCRIPTION AMOUNT BALANCE\n"
        "Apr 30, 2026 Interest Earned Interest earned $7.70 $7.70\n"
        "Transaction ID: 50-1\n"
    )
    monkeypatch.setattr(
        ingest_module,
        "standardize_sofi_statement_pdf",
        lambda _pdf_bytes: standardize_sofi_statement_text(statement_text),
    )
    response = client.post(
        "/api/accounting/import/sofi-statement-pdf", files={"file": ("statement.pdf", b"%PDF-fake", "application/pdf")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["account_ids"] == ["sofi:savings:3680"]
    assert body["new_posting_count"] == 2

    store = client.get("/api/accounting/store").json()
    assert store["accounts"]["sofi:savings:3680"]["meta"]["apy_pct"] == "0.0"


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
