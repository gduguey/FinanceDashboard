import pytest
from fastapi.testclient import TestClient

from accounting import api as accounting_api
from accounting.config import AccountingConfig
from trades import api as trades_api

CHECKING_CSV = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    "CREDIT,06/01/2026,SOME EMPLOYER PAYROLL PPD ID: 1234567890,3000.00,ACH_CREDIT,4000.00,,\n"
)


@pytest.fixture(autouse=True)
def isolated_accounting_config(tmp_path, monkeypatch):
    monkeypatch.setattr(accounting_api.state, "config", AccountingConfig(data_dir=tmp_path))


@pytest.fixture
def client():
    return TestClient(trades_api.app)


def _import_checking(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )


def _create_goal(client, goal_id: str = "emergency-fund") -> None:
    response = client.put(
        "/api/accounting/goals",
        json={
            goal_id: {
                "goal_id": goal_id,
                "name": "Emergency Fund",
                "target_amount": 10000.0,
                "target_currency": "USD",
                "target_date": "2027-01-01T00:00:00",
                "color": "#4da568",
                "created_at": "2026-06-01T00:00:00",
            }
        },
    )
    assert response.status_code == 200


def test_put_goals_persists_and_is_returned_by_store(client) -> None:
    _create_goal(client)
    store = client.get("/api/accounting/store").json()
    assert store["goals"]["emergency-fund"]["name"] == "Emergency Fund"


def test_goals_summary_reports_balance_and_unallocated(client) -> None:
    _import_checking(client)
    _create_goal(client)
    client.put(
        "/api/accounting/goal-contributions",
        json={
            "c1": {
                "contribution_id": "c1",
                "goal_id": "emergency-fund",
                "date": "2026-06-10T00:00:00",
                "amount": 500.0,
                "currency": "USD",
            }
        },
    )
    summary = client.get("/api/accounting/goals/summary", params={"as_of": "2026-06-30"}).json()
    assert summary["balances"]["emergency-fund"] == pytest.approx(500.0)
    # 3000 income - 500 allocated to the goal = 2500 unallocated
    assert summary["unallocated"] == pytest.approx(2500.0)


def test_run_recurring_additions_writes_a_contribution_once_due(client) -> None:
    _import_checking(client)
    _create_goal(client)
    client.put(
        "/api/accounting/recurring-additions",
        json=[
            {
                "addition_id": "auto:emergency-fund",
                "goal_id": "emergency-fund",
                "schedule_day_of_month": 5,
                "mode": "fixed_amount",
                "value": 500.0,
                "currency": "USD",
                "priority": 0,
            }
        ],
    )
    response = client.post("/api/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-10"})
    assert response.status_code == 200
    written = response.json()
    assert len(written) == 1
    assert written[0]["goal_id"] == "emergency-fund"
    assert written[0]["amount"] == pytest.approx(500.0)
    assert written[0]["origin"] == "automation"

    summary = client.get("/api/accounting/goals/summary", params={"as_of": "2026-06-30"}).json()
    assert summary["balances"]["emergency-fund"] == pytest.approx(500.0)


def test_run_recurring_additions_is_idempotent_within_the_same_month(client) -> None:
    _import_checking(client)
    _create_goal(client)
    client.put(
        "/api/accounting/recurring-additions",
        json=[
            {
                "addition_id": "auto:emergency-fund",
                "goal_id": "emergency-fund",
                "schedule_day_of_month": 5,
                "mode": "fixed_amount",
                "value": 500.0,
                "currency": "USD",
                "priority": 0,
            }
        ],
    )
    client.post("/api/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-10"})
    second_run = client.post("/api/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-20"})
    assert second_run.json() == []

    summary = client.get("/api/accounting/goals/summary", params={"as_of": "2026-06-30"}).json()
    assert summary["balances"]["emergency-fund"] == pytest.approx(500.0)  # not double-funded


_BIG_EXPENSE_CSV = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    "DEBIT,06/20/2026,Big unexpected expense,-4000.00,SALE,-4000.00,,\n"
)


def test_run_withdrawal_automation_draws_down_a_goal_when_unallocated_goes_negative(client) -> None:
    client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", _BIG_EXPENSE_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": "chase:checking:9579",
            "account_name": "Chase Checking (...9579)",
        },
    )
    _create_goal(client)
    client.put(
        "/api/accounting/goal-contributions",
        json={
            "c1": {
                "contribution_id": "c1",
                "goal_id": "emergency-fund",
                "date": "2026-06-01T00:00:00",
                "amount": 1000.0,
                "currency": "USD",
            }
        },
    )
    client.put("/api/accounting/withdrawal-priorities", json=[{"goal_id": "emergency-fund", "priority": 0}])

    response = client.post("/api/accounting/goals/run-withdrawal-automation", params={"as_of": "2026-06-25"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["withdrawals"]) == 1
    assert body["withdrawals"][0]["amount"] == pytest.approx(-1000.0)
    # unallocated was -4000 (0 income - 4000 expense) - 1000 (already in the goal) = -5000 shortfall to cover,
    # but the goal only had 1000 to give, so a residual shortfall remains.
    assert body["remaining_shortfall"] == pytest.approx(4000.0)


def test_simulate_contribution_flags_exceeding_unallocated(client) -> None:
    _import_checking(client)
    _create_goal(client)
    response = client.post(
        "/api/accounting/goals/simulate-contribution",
        json={"goal_id": "emergency-fund", "date": "2026-06-15", "amount": 5000.0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["exceeds_unallocated"] is True
    assert body["unallocated_as_of_date"] == pytest.approx(3000.0)


def test_simulate_contribution_within_unallocated_does_not_flag(client) -> None:
    _import_checking(client)
    _create_goal(client)
    response = client.post(
        "/api/accounting/goals/simulate-contribution",
        json={"goal_id": "emergency-fund", "date": "2026-06-15", "amount": 500.0},
    )
    assert response.json()["exceeds_unallocated"] is False
