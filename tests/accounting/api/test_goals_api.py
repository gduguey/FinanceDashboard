from datetime import date, timedelta

import polars as pl
import pytest
from fastapi.testclient import TestClient

import db.models as dbm
from accounting import api as accounting_api
from accounting.config import AccountingConfig
from accounting.market_data import exchange_rates
from accounting.market_data.exchange_rates import RATE_HISTORY_SCHEMA
from db.session import get_db
from tests.conftest import DEFAULT_USER_ID
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


def _create_account(client, **overrides) -> dict:
    payload = {"name": "Test Account", "kind": "checking", "institution": "Chase", "currency": "USD", **overrides}
    response = client.post("/api/accounting/accounts", json=payload)
    assert response.status_code == 200
    return response.json()


def _import_checking(client) -> str:
    account = _create_account(client, name="Chase Checking", kind="checking", institution="Chase")
    response = client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Chase Checking",
        },
    )
    assert response.status_code == 200
    return account["account_id"]


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


def test_post_goal_creates_one_with_a_server_generated_id(client) -> None:
    response = client.post(
        "/api/accounting/goals",
        json={"name": "Emergency fund", "target_amount": 10000.0, "target_date": "2027-01-01T00:00:00"},
    )
    assert response.status_code == 200
    goal = response.json()
    assert goal["goal_id"]
    assert goal["name"] == "Emergency fund"
    assert goal["color"]
    assert goal["created_at"]


def test_post_goal_twice_with_the_same_name_creates_two_distinct_goals(client) -> None:
    payload = {"name": "Emergency fund", "target_amount": 10000.0, "target_date": "2027-01-01T00:00:00"}
    first = client.post("/api/accounting/goals", json=payload).json()
    second = client.post("/api/accounting/goals", json=payload).json()
    assert first["goal_id"] != second["goal_id"]
    assert len(client.get("/api/accounting/store").json()["goals"]) == 2


def test_post_goal_picks_a_color_distinct_from_existing_goals(client) -> None:
    first = client.post(
        "/api/accounting/goals",
        json={"name": "Goal A", "target_amount": 100.0, "target_date": "2027-01-01T00:00:00"},
    ).json()
    second = client.post(
        "/api/accounting/goals",
        json={"name": "Goal B", "target_amount": 100.0, "target_date": "2027-01-01T00:00:00"},
    ).json()
    assert first["color"] != second["color"]


def test_patch_goal_updates_fields_and_increments_version(client) -> None:
    _create_goal(client)
    goal = client.get("/api/accounting/store").json()["goals"]["emergency-fund"]
    assert goal["version"] == 1

    response = client.patch(
        "/api/accounting/goals/emergency-fund",
        json={
            "name": "New Car",
            "target_amount": 20000.0,
            "target_currency": "USD",
            "target_date": "2028-01-01T00:00:00",
            "color": "#123456",
            "expected_version": 1,
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "New Car"
    assert updated["target_amount"] == pytest.approx(20000.0)
    assert updated["color"] == "#123456"
    assert updated["version"] == 2
    assert updated["created_at"] == "2026-06-01T00:00:00"  # untouched by the update

    persisted = client.get("/api/accounting/store").json()["goals"]["emergency-fund"]
    assert persisted["name"] == "New Car"
    assert persisted["version"] == 2


def test_patch_goal_with_a_stale_expected_version_gets_409(client) -> None:
    _create_goal(client)
    response = client.patch(
        "/api/accounting/goals/emergency-fund",
        json={
            "name": "New Car",
            "target_amount": 20000.0,
            "target_currency": "USD",
            "target_date": "2028-01-01T00:00:00",
            "color": "#123456",
            "expected_version": 2,
        },
    )
    assert response.status_code == 409

    unchanged = client.get("/api/accounting/store").json()["goals"]["emergency-fund"]
    assert unchanged["name"] == "Emergency Fund"
    assert unchanged["version"] == 1


def test_patch_goal_that_does_not_exist_gets_404(client) -> None:
    response = client.patch(
        "/api/accounting/goals/does-not-exist",
        json={
            "name": "New Car",
            "target_amount": 20000.0,
            "target_currency": "USD",
            "target_date": "2028-01-01T00:00:00",
            "color": "#123456",
            "expected_version": 1,
        },
    )
    assert response.status_code == 404


def test_delete_goal_removes_it(client) -> None:
    _create_goal(client)
    response = client.delete("/api/accounting/goals/emergency-fund")
    assert response.status_code == 200
    assert client.get("/api/accounting/store").json()["goals"] == {}


def test_delete_goal_that_is_already_gone_gets_404(client) -> None:
    response = client.delete("/api/accounting/goals/does-not-exist")
    assert response.status_code == 404


def test_two_patches_on_different_goals_do_not_clobber_each_other(client) -> None:
    """Reproduces the audit's actual finding for `useSetGoals`: editing two *different* goals used to

    round-trip through the same whole-store `save_store` call — a scoped, row-versioned `PATCH` for one
    goal must never touch, let alone revert, a sibling goal's own fields.
    """
    # `_create_goal` goes through `PUT /goals` (a whole-list replace), so the second call can't be used
    # to add a goal alongside the first — `POST /goals` is the additive create.
    _create_goal(client, goal_id="emergency-fund")
    new_car = client.post(
        "/api/accounting/goals",
        json={"name": "New Car", "target_amount": 20000.0, "target_date": "2028-01-01T00:00:00"},
    ).json()

    response_a = client.patch(
        "/api/accounting/goals/emergency-fund",
        json={
            "name": "Renamed Emergency Fund",
            "target_amount": 10000.0,
            "target_currency": "USD",
            "target_date": "2027-01-01T00:00:00",
            "color": "#4da568",
            "expected_version": 1,
        },
    )
    response_b = client.patch(
        f"/api/accounting/goals/{new_car['goal_id']}",
        json={
            "name": "Renamed New Car",
            "target_amount": 10000.0,
            "target_currency": "USD",
            "target_date": "2027-01-01T00:00:00",
            "color": "#4da568",
            "expected_version": 1,
        },
    )
    assert response_a.status_code == 200
    assert response_b.status_code == 200

    goals = client.get("/api/accounting/store").json()["goals"]
    assert goals["emergency-fund"]["name"] == "Renamed Emergency Fund"
    assert goals[new_car["goal_id"]]["name"] == "Renamed New Car"


def test_creating_an_unrelated_goal_does_not_reset_another_goals_version(client) -> None:
    _create_goal(client, goal_id="emergency-fund")
    patched = client.patch(
        "/api/accounting/goals/emergency-fund",
        json={
            "name": "Renamed",
            "target_amount": 10000.0,
            "target_currency": "USD",
            "target_date": "2027-01-01T00:00:00",
            "color": "#4da568",
            "expected_version": 1,
        },
    ).json()
    assert patched["version"] == 2

    client.post(
        "/api/accounting/goals",
        json={"name": "New Car", "target_amount": 20000.0, "target_date": "2028-01-01T00:00:00"},
    )

    persisted = client.get("/api/accounting/store").json()["goals"]["emergency-fund"]
    assert persisted["version"] == 2

    response = client.patch(
        "/api/accounting/goals/emergency-fund",
        json={
            "name": "Renamed Again",
            "target_amount": 10000.0,
            "target_currency": "USD",
            "target_date": "2027-01-01T00:00:00",
            "color": "#4da568",
            "expected_version": 2,
        },
    )
    assert response.status_code == 200


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


def test_post_goal_contribution_creates_one_with_a_server_generated_id(client) -> None:
    _create_goal(client)

    response = client.post(
        "/api/accounting/goal-contributions",
        json={"goal_id": "emergency-fund", "date": "2026-06-10T00:00:00", "amount": 500.0, "currency": "USD"},
    )

    assert response.status_code == 200
    created = response.json()
    assert created["contribution_id"]
    assert created["amount"] == pytest.approx(500.0)
    assert created["origin"] == "manual"

    contributions = client.get("/api/accounting/store").json()["goal_contributions"]
    assert set(contributions.keys()) == {created["contribution_id"]}


def test_post_goal_contribution_twice_creates_two_distinct_rows(client) -> None:
    _create_goal(client)
    body = {"goal_id": "emergency-fund", "date": "2026-06-10T00:00:00", "amount": 500.0, "currency": "USD"}

    first = client.post("/api/accounting/goal-contributions", json=body)
    second = client.post("/api/accounting/goal-contributions", json=body)

    assert first.json()["contribution_id"] != second.json()["contribution_id"]
    contributions = client.get("/api/accounting/store").json()["goal_contributions"]
    assert len(contributions) == 2


def test_put_goal_contribution_replaces_one_without_touching_others(client) -> None:
    _create_goal(client)
    created = client.post(
        "/api/accounting/goal-contributions",
        json={"goal_id": "emergency-fund", "date": "2026-06-10T00:00:00", "amount": 500.0, "currency": "USD"},
    ).json()
    other = client.post(
        "/api/accounting/goal-contributions",
        json={"goal_id": "emergency-fund", "date": "2026-06-11T00:00:00", "amount": 100.0, "currency": "USD"},
    ).json()

    response = client.put(
        f"/api/accounting/goal-contributions/{created['contribution_id']}",
        json={
            "goal_id": "emergency-fund",
            "date": "2026-06-10T00:00:00",
            "amount": 750.0,
            "currency": "USD",
            "note": "topped up",
        },
    )

    assert response.status_code == 200
    assert response.json()["amount"] == pytest.approx(750.0)
    contributions = client.get("/api/accounting/store").json()["goal_contributions"]
    assert contributions[created["contribution_id"]]["amount"] == pytest.approx(750.0)
    assert contributions[other["contribution_id"]]["amount"] == pytest.approx(100.0)


def test_put_goal_contribution_404s_for_an_unknown_id(client) -> None:
    _create_goal(client)
    response = client.put(
        "/api/accounting/goal-contributions/does-not-exist",
        json={"goal_id": "emergency-fund", "date": "2026-06-10T00:00:00", "amount": 750.0, "currency": "USD"},
    )
    assert response.status_code == 404


def test_delete_goal_contribution_removes_only_that_one(client) -> None:
    _create_goal(client)
    created = client.post(
        "/api/accounting/goal-contributions",
        json={"goal_id": "emergency-fund", "date": "2026-06-10T00:00:00", "amount": 500.0, "currency": "USD"},
    ).json()
    other = client.post(
        "/api/accounting/goal-contributions",
        json={"goal_id": "emergency-fund", "date": "2026-06-11T00:00:00", "amount": 100.0, "currency": "USD"},
    ).json()

    response = client.delete(f"/api/accounting/goal-contributions/{created['contribution_id']}")

    assert response.status_code == 200
    contributions = client.get("/api/accounting/store").json()["goal_contributions"]
    assert set(contributions.keys()) == {other["contribution_id"]}


def test_delete_goal_contribution_404s_for_an_unknown_id(client) -> None:
    response = client.delete("/api/accounting/goal-contributions/does-not-exist")
    assert response.status_code == 404


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


def test_post_recurring_addition_creates_one_with_a_server_generated_id(client) -> None:
    _create_goal(client)
    response = client.post(
        "/api/accounting/recurring-additions",
        json={
            "goal_id": "emergency-fund",
            "start_date": "2026-06-05",
            "frequency": "monthly",
            "mode": "fixed_amount",
            "value": 500.0,
            "currency": "USD",
        },
    )
    assert response.status_code == 200
    addition = response.json()
    assert addition["addition_id"]
    assert addition["goal_id"] == "emergency-fund"
    assert addition["priority"] == 0


def test_post_recurring_addition_appends_after_existing_ones_by_priority(client) -> None:
    _create_goal(client)
    payload = {
        "goal_id": "emergency-fund",
        "start_date": "2026-06-05",
        "frequency": "monthly",
        "mode": "fixed_amount",
        "value": 500.0,
        "currency": "USD",
    }
    first = client.post("/api/accounting/recurring-additions", json=payload).json()
    second = client.post("/api/accounting/recurring-additions", json=payload).json()
    assert first["addition_id"] != second["addition_id"]
    assert first["priority"] == 0
    assert second["priority"] == 1


def test_patch_recurring_addition_edits_one_without_touching_another(client) -> None:
    _create_goal(client)
    payload = {
        "goal_id": "emergency-fund",
        "start_date": "2026-06-05",
        "frequency": "monthly",
        "mode": "fixed_amount",
        "value": 500.0,
        "currency": "USD",
    }
    first = client.post("/api/accounting/recurring-additions", json=payload).json()
    second = client.post("/api/accounting/recurring-additions", json=payload).json()

    response = client.patch(
        f"/api/accounting/recurring-additions/{first['addition_id']}",
        json={
            "goal_id": "emergency-fund",
            "start_date": "2026-06-05",
            "frequency": "monthly",
            "mode": "fixed_amount",
            "value": 750.0,
            "currency": "USD",
            "priority": first["priority"],
        },
    )
    assert response.status_code == 200
    assert response.json()["value"] == pytest.approx(750.0)

    additions = {a["addition_id"]: a for a in client.get("/api/accounting/store").json()["recurring_additions"]}
    assert additions[first["addition_id"]]["value"] == pytest.approx(750.0)
    assert additions[second["addition_id"]]["value"] == pytest.approx(500.0)  # untouched


def test_patch_recurring_addition_404s_for_an_unknown_id(client) -> None:
    _create_goal(client)
    response = client.patch(
        "/api/accounting/recurring-additions/nope",
        json={
            "goal_id": "emergency-fund",
            "start_date": "2026-06-05",
            "frequency": "monthly",
            "mode": "fixed_amount",
            "value": 1.0,
            "currency": "USD",
            "priority": 0,
        },
    )
    assert response.status_code == 404


def test_delete_recurring_addition_removes_only_that_one(client) -> None:
    _create_goal(client)
    payload = {
        "goal_id": "emergency-fund",
        "start_date": "2026-06-05",
        "frequency": "monthly",
        "mode": "fixed_amount",
        "value": 500.0,
        "currency": "USD",
    }
    first = client.post("/api/accounting/recurring-additions", json=payload).json()
    second = client.post("/api/accounting/recurring-additions", json=payload).json()

    response = client.delete(f"/api/accounting/recurring-additions/{first['addition_id']}")
    assert response.status_code == 200
    remaining = {a["addition_id"] for a in client.get("/api/accounting/store").json()["recurring_additions"]}
    assert remaining == {second["addition_id"]}


def test_delete_recurring_addition_that_is_already_gone_gets_404(client) -> None:
    assert client.delete("/api/accounting/recurring-additions/nope").status_code == 404


_BIG_EXPENSE_CSV = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    "DEBIT,06/20/2026,Big unexpected expense,-4000.00,SALE,-4000.00,,\n"
)


def test_run_withdrawal_automation_draws_down_a_goal_when_unallocated_goes_negative(client) -> None:
    account = _create_account(client, name="Chase Checking", kind="checking", institution="Chase")
    response = client.post(
        "/api/accounting/import",
        files={"file": ("Chase9579.csv", _BIG_EXPENSE_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Chase Checking",
        },
    )
    assert response.status_code == 200
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


def test_put_withdrawal_priorities_ignores_a_stale_store_version(client) -> None:
    """Reordering withdrawal priorities is a pure last-write-wins ordering op, so it opts out of the

    whole-store version check — a deliberately stale `X-Expected-Store-Version` header must not 409 it.
    """
    _create_goal(client)
    response = client.put(
        "/api/accounting/withdrawal-priorities",
        json=[{"goal_id": "emergency-fund", "priority": 0}],
        headers={"X-Expected-Store-Version": "0"},  # stale on purpose
    )
    assert response.status_code == 200


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
