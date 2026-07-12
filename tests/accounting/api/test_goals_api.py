from datetime import date, timedelta

import polars as pl
import pytest
from fastapi.testclient import TestClient

import db.models as dbm
from accounting import api as accounting_api
from accounting.config import AccountingConfig
from accounting.market_data import exchange_rates
from accounting.market_data.exchange_rates import RATE_HISTORY_SCHEMA
from db.current_user import DEFAULT_USER_ID
from db.session import get_db
from trades import api as trades_api

CHECKING_CSV = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    "CREDIT,06/01/2026,SOME EMPLOYER PAYROLL PPD ID: 1234567890,3000.00,ACH_CREDIT,4000.00,,\n"
)


def _fake_rate_history() -> pl.DataFrame:
    today = date.today()
    return pl.DataFrame(
        {"date": [today - timedelta(days=1), today], "currency": ["EUR", "EUR"], "rate_to_base": [1.9, 2.1]},
        schema=RATE_HISTORY_SCHEMA,
    )


@pytest.fixture(autouse=True)
def isolated_accounting_config(tmp_path, monkeypatch):
    monkeypatch.setattr(accounting_api.state, "config", AccountingConfig(data_dir=tmp_path))


@pytest.fixture(autouse=True)
def _db_for_api(db_session):
    """See `test_accounting_api.py`'s fixture of the same name — routes every request through
    this test's own rolled-back session instead of the real (shared, never-rolled-back) one.
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


def test_put_goal_contributions_referencing_a_nonexistent_goal_fails() -> None:
    """`goal_id` is a real foreign key now (see `accounting.db.goals.GoalContribution`) — a contribution
    naming a goal that doesn't exist can no longer be silently accepted. No new API-level validation was
    added for this, so it surfaces exactly like every other foreign-key violation in this app: an
    unhandled `IntegrityError` propagating out of the route as a 500, not a clean 4xx.
    """
    client = TestClient(trades_api.app, raise_server_exceptions=False)
    response = client.put(
        "/api/accounting/goal-contributions",
        json={
            "c1": {
                "contribution_id": "c1",
                "goal_id": "does-not-exist",
                "date": "2026-06-10T00:00:00",
                "amount": 500.0,
                "currency": "USD",
            }
        },
    )
    assert response.status_code == 500


def test_goals_summary_converts_into_the_requested_display_currency(client, monkeypatch) -> None:
    monkeypatch.setattr(
        exchange_rates, "fetch_rate_history", lambda config, history_years=2, session=None: _fake_rate_history()
    )
    exchange_rates.update_rate_history_cache(accounting_api.state.config)
    _import_checking(client)
    _create_goal(client)
    client.put(
        "/api/accounting/goal-contributions",
        json={
            "c1": {
                "contribution_id": "c1",
                "goal_id": "emergency-fund",
                "date": f"{date.today().isoformat()}T00:00:00",
                "amount": 500.0,
                "currency": "USD",
            }
        },
    )
    summary = client.get("/api/accounting/goals/summary", params={"display_currency": "EUR"}).json()
    # 1 EUR = 2 USD (smoothed), so 500 USD converts to 250 EUR.
    assert summary["balances"]["emergency-fund"] == pytest.approx(250.0)
    # 3000 USD income -> 1500 EUR, minus 250 EUR contributed = 1250 EUR unallocated.
    assert summary["unallocated"] == pytest.approx(1250.0)


def test_run_recurring_additions_writes_a_contribution_once_due(client) -> None:
    _import_checking(client)
    _create_goal(client)
    client.put(
        "/api/accounting/recurring-additions",
        json=[
            {
                "addition_id": "auto:emergency-fund",
                "goal_id": "emergency-fund",
                "start_date": "2026-01-05",
                "frequency": "monthly",
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
                "start_date": "2026-01-05",
                "frequency": "monthly",
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


def test_run_recurring_additions_supports_a_weekly_schedule(client) -> None:
    _import_checking(client)
    _create_goal(client)
    client.put(
        "/api/accounting/recurring-additions",
        json=[
            {
                "addition_id": "auto:emergency-fund",
                "goal_id": "emergency-fund",
                "start_date": "2026-06-01",
                "frequency": "weekly",
                "mode": "fixed_amount",
                "value": 100.0,
                "currency": "USD",
                "priority": 0,
            }
        ],
    )
    first = client.post("/api/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-01"})
    assert len(first.json()) == 1
    # Same week — already funded, so a second call the same week is a no-op...
    same_week = client.post("/api/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-05"})
    assert same_week.json() == []
    # ...but the next week's occurrence is a brand-new, separately-funded contribution.
    next_week = client.post("/api/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-08"})
    assert len(next_week.json()) == 1

    summary = client.get("/api/accounting/goals/summary", params={"as_of": "2026-06-30"}).json()
    assert summary["balances"]["emergency-fund"] == pytest.approx(200.0)


def test_run_recurring_additions_stops_after_the_end_date(client) -> None:
    _import_checking(client)
    _create_goal(client)
    client.put(
        "/api/accounting/recurring-additions",
        json=[
            {
                "addition_id": "auto:emergency-fund",
                "goal_id": "emergency-fund",
                "start_date": "2026-06-01",
                "frequency": "daily",
                "end_date": "2026-06-03",
                "mode": "fixed_amount",
                "value": 50.0,
                "currency": "USD",
                "priority": 0,
            }
        ],
    )
    client.post("/api/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-03"})
    past_end = client.post("/api/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-10"})
    assert past_end.json() == []  # already funded through the end date — nothing new to add

    summary = client.get("/api/accounting/goals/summary", params={"as_of": "2026-06-30"}).json()
    assert summary["balances"]["emergency-fund"] == pytest.approx(50.0)


def test_put_recurring_additions_still_accepts_the_legacy_schedule_day_of_month(client) -> None:
    _create_goal(client)
    response = client.put(
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
    assert response.status_code == 200
    saved = response.json()[0]
    assert saved["frequency"] == "monthly"
    assert saved["start_date"] == "2000-01-05"


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
