from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest
from fastapi.testclient import TestClient

import db.models as dbm
from accounting import api as accounting_api
from accounting.config import AccountingConfig
from accounting.market_data import exchange_rates
from accounting.market_data.exchange_rates import RATE_HISTORY_SCHEMA
from accounting.models import Goal
from accounting.repositories.planning import insert_goal
from db.session import get_db
from tests.conftest import DEFAULT_USER_ID
from trades import api as trades_api

CHECKING_CSV = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    "CREDIT,06/01/2026,SOME EMPLOYER PAYROLL PPD ID: 1234567890,3000.00,ACH_CREDIT,4000.00,,\n"
)


def _add_contribution_automation(client, **overrides) -> dict:
    payload = {
        "goal_id": "emergency-fund",
        "start_date": "2026-01-05",
        "frequency": "monthly",
        "mode": "fixed_amount",
        "value": 500.0,
        "currency": "USD",
        **overrides,
    }
    response = client.post("/api/v1/accounting/goal-automations/contributions", json=payload)
    assert response.status_code == 201
    return response.json()


def _add_withdrawal(client, goal_id: str = "emergency-fund") -> dict:
    """Put one goal into the drawdown order, the only way a client can join it.

    Returns
    -------
    dict
        The created entry, whose `automation_id` the reorder route addresses it by.
    """
    response = client.post("/api/v1/accounting/goal-automations/withdrawals", json={"goal_id": goal_id})
    assert response.status_code == 201
    return response.json()


def _reorder(client, direction: str, automation_ids: list[str]):
    """Submit one direction's whole ordering as ids.

    Returns
    -------
    httpx.Response
    """
    return client.put(
        f"/api/v1/accounting/goal-automations/{direction}/order",
        json={"automation_ids": automation_ids},
    )


def _stored_automations(client) -> dict[str, dict]:
    """Read every automation back out of the store, keyed by id.

    Returns
    -------
    dict[str, dict]
    """
    return {a["automation_id"]: a for a in client.get("/api/v1/accounting/store").json()["goal_automations"]}


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


def _create_account(client, **overrides) -> dict:
    payload = {"name": "Test Account", "kind": "checking", "institution": "Chase", "currency": "USD", **overrides}
    response = client.post("/api/v1/accounting/accounts", json=payload)
    assert response.status_code == 201
    return response.json()


def _import_checking(client) -> str:
    account = _create_account(client, name="Chase Checking", kind="checking", institution="Chase")
    response = client.post(
        "/api/v1/accounting/import",
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


def _create_goal(session, goal_id: str = "emergency-fund") -> None:
    """Seed one goal through the repository rather than over HTTP, purely to pin a readable `goal_id`.

    Every test below names this goal by id — in a URL, in a contribution
    body, in a `store["goals"][...]` lookup — and `POST /goals` mints an
    opaque `goal:{uuid4hex}` one, so seeding over HTTP would mean
    threading a generated id through all of them for no gain in coverage
    (`POST /goals` has its own tests). `session` is the test's own
    `db_session`, which is the very session `_db_for_api` routes every
    API request through, so a goal written here is visible to the client.
    """
    insert_goal(
        session,
        DEFAULT_USER_ID,
        Goal(
            goal_id=goal_id,
            name="Emergency Fund",
            target_amount=10000.0,
            target_currency="USD",
            target_date=datetime(2027, 1, 1, tzinfo=UTC),
            color="#4da568",
            created_at=datetime(2026, 6, 1, tzinfo=UTC),
        ),
    )


def test_the_goal_item_route_does_not_shadow_the_summary_route(client) -> None:
    """`/goals/summary` matches `/goals/{goal_id}` on a GET, so registration order decides which wins.

    Nothing in FastAPI enforces the order that makes this correct — only the
    comment above `get_goal`. This fails if the item route is ever moved
    above `get_goals_summary`, which would answer 404 for a goal named
    "summary" instead of returning the summary.
    """
    summary = client.get("/api/v1/accounting/goals/summary")
    assert summary.status_code == 200
    assert "unallocated" in summary.json()
    assert client.get("/api/v1/accounting/goals/goal:nope").status_code == 404


def test_post_goal_creates_one_with_a_server_generated_id(client) -> None:
    response = client.post(
        "/api/v1/accounting/goals",
        json={"name": "Emergency fund", "target_amount": 10000.0, "target_date": "2027-01-01T00:00:00"},
    )
    assert response.status_code == 201
    goal = response.json()
    assert goal["goal_id"]
    assert goal["name"] == "Emergency fund"
    assert goal["color"]
    assert goal["created_at"]
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json() == goal


def test_post_goal_twice_with_the_same_name_creates_two_distinct_goals(client) -> None:
    payload = {"name": "Emergency fund", "target_amount": 10000.0, "target_date": "2027-01-01T00:00:00"}
    first = client.post("/api/v1/accounting/goals", json=payload).json()
    second = client.post("/api/v1/accounting/goals", json=payload).json()
    assert first["goal_id"] != second["goal_id"]
    assert len(client.get("/api/v1/accounting/store").json()["goals"]) == 2


def test_post_goal_picks_a_color_distinct_from_existing_goals(client) -> None:
    first = client.post(
        "/api/v1/accounting/goals",
        json={"name": "Goal A", "target_amount": 100.0, "target_date": "2027-01-01T00:00:00"},
    ).json()
    second = client.post(
        "/api/v1/accounting/goals",
        json={"name": "Goal B", "target_amount": 100.0, "target_date": "2027-01-01T00:00:00"},
    ).json()
    assert first["color"] != second["color"]


def test_patch_goal_updates_fields_and_increments_version(client, db_session) -> None:
    _create_goal(db_session)
    goal = client.get("/api/v1/accounting/store").json()["goals"]["emergency-fund"]
    assert goal["version"] == 1

    response = client.patch(
        "/api/v1/accounting/goals/emergency-fund",
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
    assert updated["created_at"] == "2026-06-01T00:00:00Z"  # untouched by the update; UTC-aware since the tz migration

    persisted = client.get("/api/v1/accounting/store").json()["goals"]["emergency-fund"]
    assert persisted["name"] == "New Car"
    assert persisted["version"] == 2


def test_patch_goal_with_a_stale_expected_version_gets_409(client, db_session) -> None:
    _create_goal(db_session)
    response = client.patch(
        "/api/v1/accounting/goals/emergency-fund",
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

    unchanged = client.get("/api/v1/accounting/store").json()["goals"]["emergency-fund"]
    assert unchanged["name"] == "Emergency Fund"
    assert unchanged["version"] == 1


def test_patch_goal_that_does_not_exist_gets_404(client) -> None:
    response = client.patch(
        "/api/v1/accounting/goals/does-not-exist",
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


def test_delete_goal_removes_it(client, db_session) -> None:
    _create_goal(db_session)
    response = client.delete("/api/v1/accounting/goals/emergency-fund")
    assert response.status_code == 204
    assert client.get("/api/v1/accounting/store").json()["goals"] == {}


def test_delete_goal_that_is_already_gone_gets_404(client) -> None:
    response = client.delete("/api/v1/accounting/goals/does-not-exist")
    assert response.status_code == 404


def test_two_patches_on_different_goals_do_not_clobber_each_other(client, db_session) -> None:
    """Reproduces the audit's actual finding for `useSetGoals`: editing two *different* goals used to

    round-trip through the same whole-store save — a scoped, row-versioned `PATCH` for one goal must
    never touch, let alone revert, a sibling goal's own fields.
    """
    # The first goal is seeded through the repository so it keeps a readable id
    # to patch by (see `_create_goal`); the second goes through the real
    # `POST /goals`, which is what a client actually calls to add one.
    _create_goal(db_session, goal_id="emergency-fund")
    new_car = client.post(
        "/api/v1/accounting/goals",
        json={"name": "New Car", "target_amount": 20000.0, "target_date": "2028-01-01T00:00:00"},
    ).json()

    response_a = client.patch(
        "/api/v1/accounting/goals/emergency-fund",
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
        f"/api/v1/accounting/goals/{new_car['goal_id']}",
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

    goals = client.get("/api/v1/accounting/store").json()["goals"]
    assert goals["emergency-fund"]["name"] == "Renamed Emergency Fund"
    assert goals[new_car["goal_id"]]["name"] == "Renamed New Car"


def test_creating_an_unrelated_goal_does_not_reset_another_goals_version(client, db_session) -> None:
    _create_goal(db_session, goal_id="emergency-fund")
    patched = client.patch(
        "/api/v1/accounting/goals/emergency-fund",
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
        "/api/v1/accounting/goals",
        json={"name": "New Car", "target_amount": 20000.0, "target_date": "2028-01-01T00:00:00"},
    )

    persisted = client.get("/api/v1/accounting/store").json()["goals"]["emergency-fund"]
    assert persisted["version"] == 2

    response = client.patch(
        "/api/v1/accounting/goals/emergency-fund",
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


def test_goals_summary_reports_balance_and_unallocated(client, db_session) -> None:
    _import_checking(client)
    _create_goal(db_session)
    client.post(
        "/api/v1/accounting/goal-contributions",
        json={"goal_id": "emergency-fund", "date": "2026-06-10T00:00:00", "amount": 500.0, "currency": "USD"},
    )
    summary = client.get("/api/v1/accounting/goals/summary", params={"as_of": "2026-06-30"}).json()
    assert summary["balances"]["emergency-fund"] == pytest.approx(500.0)
    # 3000 income - 500 allocated to the goal = 2500 unallocated
    assert summary["unallocated"] == pytest.approx(2500.0)


def test_posting_a_goal_contribution_referencing_a_nonexistent_goal_fails() -> None:
    """`goal_id` is a real foreign key now (see `accounting.db.goals.GoalContribution`) — a contribution
    naming a goal that doesn't exist can no longer be silently accepted. No new API-level validation was
    added for this, so it surfaces exactly like every other foreign-key violation in this app: an
    unhandled `IntegrityError` propagating out of the route as a 500, not a clean 4xx.
    """
    client = TestClient(trades_api.app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/accounting/goal-contributions",
        json={"goal_id": "does-not-exist", "date": "2026-06-10T00:00:00", "amount": 500.0, "currency": "USD"},
    )
    assert response.status_code == 500


def test_post_goal_contribution_creates_one_with_a_server_generated_id(client, db_session) -> None:
    _create_goal(db_session)

    response = client.post(
        "/api/v1/accounting/goal-contributions",
        json={"goal_id": "emergency-fund", "date": "2026-06-10T00:00:00", "amount": 500.0, "currency": "USD"},
    )

    assert response.status_code == 201
    created = response.json()
    assert created["contribution_id"]
    assert created["amount"] == pytest.approx(500.0)
    assert created["origin"] == "manual"
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json() == created

    contributions = client.get("/api/v1/accounting/store").json()["goal_contributions"]
    assert set(contributions.keys()) == {created["contribution_id"]}


def test_post_goal_contribution_round_trips_the_account_the_money_sits_in(client, db_session) -> None:
    # Plumbing only: `account_id` is stored and read back, and no balance or
    # unallocated figure moves because it is set (see `models.GoalContribution`).
    _create_goal(db_session)
    account = _create_account(client, name="Vault", kind="savings", institution="SoFi")

    created = client.post(
        "/api/v1/accounting/goal-contributions",
        json={
            "goal_id": "emergency-fund",
            "date": "2026-06-10T00:00:00",
            "amount": 500.0,
            "currency": "USD",
            "account_id": account["account_id"],
        },
    ).json()
    assert created["account_id"] == account["account_id"]

    contributions = client.get("/api/v1/accounting/store").json()["goal_contributions"]
    assert contributions[created["contribution_id"]]["account_id"] == account["account_id"]

    summary = client.get("/api/v1/accounting/goals/summary", params={"as_of": "2026-06-30"}).json()
    assert summary["balances"]["emergency-fund"] == pytest.approx(500.0)


def test_post_goal_contribution_defaults_the_account_to_none(client, db_session) -> None:
    _create_goal(db_session)
    created = client.post(
        "/api/v1/accounting/goal-contributions",
        json={"goal_id": "emergency-fund", "date": "2026-06-10T00:00:00", "amount": 500.0, "currency": "USD"},
    ).json()
    assert created["account_id"] is None


def test_post_goal_contribution_twice_creates_two_distinct_rows(client, db_session) -> None:
    _create_goal(db_session)
    body = {"goal_id": "emergency-fund", "date": "2026-06-10T00:00:00", "amount": 500.0, "currency": "USD"}

    first = client.post("/api/v1/accounting/goal-contributions", json=body)
    second = client.post("/api/v1/accounting/goal-contributions", json=body)

    assert first.json()["contribution_id"] != second.json()["contribution_id"]
    contributions = client.get("/api/v1/accounting/store").json()["goal_contributions"]
    assert len(contributions) == 2


def test_put_goal_contribution_replaces_one_without_touching_others(client, db_session) -> None:
    _create_goal(db_session)
    created = client.post(
        "/api/v1/accounting/goal-contributions",
        json={"goal_id": "emergency-fund", "date": "2026-06-10T00:00:00", "amount": 500.0, "currency": "USD"},
    ).json()
    other = client.post(
        "/api/v1/accounting/goal-contributions",
        json={"goal_id": "emergency-fund", "date": "2026-06-11T00:00:00", "amount": 100.0, "currency": "USD"},
    ).json()

    response = client.put(
        f"/api/v1/accounting/goal-contributions/{created['contribution_id']}",
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
    contributions = client.get("/api/v1/accounting/store").json()["goal_contributions"]
    assert contributions[created["contribution_id"]]["amount"] == pytest.approx(750.0)
    assert contributions[other["contribution_id"]]["amount"] == pytest.approx(100.0)


def test_put_goal_contribution_404s_for_an_unknown_id(client, db_session) -> None:
    _create_goal(db_session)
    response = client.put(
        "/api/v1/accounting/goal-contributions/does-not-exist",
        json={"goal_id": "emergency-fund", "date": "2026-06-10T00:00:00", "amount": 750.0, "currency": "USD"},
    )
    assert response.status_code == 404


def test_delete_goal_contribution_removes_only_that_one(client, db_session) -> None:
    _create_goal(db_session)
    created = client.post(
        "/api/v1/accounting/goal-contributions",
        json={"goal_id": "emergency-fund", "date": "2026-06-10T00:00:00", "amount": 500.0, "currency": "USD"},
    ).json()
    other = client.post(
        "/api/v1/accounting/goal-contributions",
        json={"goal_id": "emergency-fund", "date": "2026-06-11T00:00:00", "amount": 100.0, "currency": "USD"},
    ).json()

    response = client.delete(f"/api/v1/accounting/goal-contributions/{created['contribution_id']}")

    assert response.status_code == 204
    contributions = client.get("/api/v1/accounting/store").json()["goal_contributions"]
    assert set(contributions.keys()) == {other["contribution_id"]}


def test_delete_goal_contribution_404s_for_an_unknown_id(client) -> None:
    response = client.delete("/api/v1/accounting/goal-contributions/does-not-exist")
    assert response.status_code == 404


def test_goals_summary_converts_into_the_requested_display_currency(client, monkeypatch, db_session) -> None:
    monkeypatch.setattr(
        exchange_rates, "fetch_rate_history", lambda config, history_years=2, session=None: _fake_rate_history()
    )
    exchange_rates.update_rate_history_cache(accounting_api.state.config)
    _import_checking(client)
    _create_goal(db_session)
    client.post(
        "/api/v1/accounting/goal-contributions",
        json={
            "goal_id": "emergency-fund",
            "date": f"{date.today().isoformat()}T00:00:00",
            "amount": 500.0,
            "currency": "USD",
        },
    )
    summary = client.get("/api/v1/accounting/goals/summary", params={"display_currency": "EUR"}).json()
    # 1 EUR = 2 USD (smoothed), so 500 USD converts to 250 EUR.
    assert summary["balances"]["emergency-fund"] == pytest.approx(250.0)
    # 3000 USD income -> 1500 EUR, minus 250 EUR contributed = 1250 EUR unallocated.
    assert summary["unallocated"] == pytest.approx(1250.0)


def test_run_contribution_automations_writes_a_contribution_once_due(client, db_session) -> None:
    _import_checking(client)
    _create_goal(db_session)
    _add_contribution_automation(client)
    response = client.post("/api/v1/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-10"})
    assert response.status_code == 200
    written = response.json()
    assert len(written) == 1
    assert written[0]["goal_id"] == "emergency-fund"
    assert written[0]["amount"] == pytest.approx(500.0)
    assert written[0]["origin"] == "automation"

    summary = client.get("/api/v1/accounting/goals/summary", params={"as_of": "2026-06-30"}).json()
    assert summary["balances"]["emergency-fund"] == pytest.approx(500.0)


def test_run_contribution_automations_funds_two_schedules_on_one_goal_separately(client, db_session) -> None:
    """A goal may legitimately carry several contribution schedules; only withdrawals are one-per-goal.

    `run_recurring_additions` returns one entry per funded automation, but
    the handler used to resolve those entries through a `goal_id ->
    automation` map. With two schedules on one goal that map is
    non-injective: both entries resolved to whichever automation came last,
    minted the same `contribution_id`, and the second silently overwrote
    the first — so one schedule's money vanished even though it had already
    been counted against the unallocated balance.
    """
    _import_checking(client)
    _create_goal(db_session)
    first = _add_contribution_automation(client, value=300.0)
    second = _add_contribution_automation(client, value=200.0)

    written = client.post("/api/v1/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-10"}).json()

    # One contribution per schedule, each carrying its own automation's amount —
    # and each under an id derived from its *own* automation, which is the bug
    # this pins: a `goal_id`-keyed map collapsed both onto one id.
    assert len(written) == 2
    first_contribution_id = f"auto:{first['automation_id']}:2026-06-05"
    second_contribution_id = f"auto:{second['automation_id']}:2026-06-05"
    assert {row["contribution_id"] for row in written} == {first_contribution_id, second_contribution_id}
    by_id = {row["contribution_id"]: row for row in written}
    assert by_id[first_contribution_id]["amount"] == pytest.approx(300.0)
    assert by_id[second_contribution_id]["amount"] == pytest.approx(200.0)

    summary = client.get("/api/v1/accounting/goals/summary", params={"as_of": "2026-06-30"}).json()
    assert summary["balances"]["emergency-fund"] == pytest.approx(500.0)


def test_run_contribution_automations_is_idempotent_within_the_same_month(client, db_session) -> None:
    _import_checking(client)
    _create_goal(db_session)
    _add_contribution_automation(client)
    client.post("/api/v1/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-10"})
    second_run = client.post("/api/v1/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-20"})
    assert second_run.json() == []

    summary = client.get("/api/v1/accounting/goals/summary", params={"as_of": "2026-06-30"}).json()
    assert summary["balances"]["emergency-fund"] == pytest.approx(500.0)  # not double-funded


def test_run_contribution_automations_supports_a_weekly_schedule(client, db_session) -> None:
    _import_checking(client)
    _create_goal(db_session)
    _add_contribution_automation(client, start_date="2026-06-01", frequency="weekly", value=100.0)
    first = client.post("/api/v1/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-01"})
    assert len(first.json()) == 1
    # Same week — already funded, so a second call the same week is a no-op...
    same_week = client.post("/api/v1/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-05"})
    assert same_week.json() == []
    # ...but the next week's occurrence is a brand-new, separately-funded contribution.
    next_week = client.post("/api/v1/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-08"})
    assert len(next_week.json()) == 1

    summary = client.get("/api/v1/accounting/goals/summary", params={"as_of": "2026-06-30"}).json()
    assert summary["balances"]["emergency-fund"] == pytest.approx(200.0)


def test_run_contribution_automations_stops_after_the_end_date(client, db_session) -> None:
    _import_checking(client)
    _create_goal(db_session)
    _add_contribution_automation(client, start_date="2026-06-01", frequency="daily", end_date="2026-06-03", value=50.0)
    client.post("/api/v1/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-03"})
    past_end = client.post("/api/v1/accounting/goals/run-recurring-additions", params={"as_of": "2026-06-10"})
    assert past_end.json() == []  # already funded through the end date — nothing new to add

    summary = client.get("/api/v1/accounting/goals/summary", params={"as_of": "2026-06-30"}).json()
    assert summary["balances"]["emergency-fund"] == pytest.approx(50.0)


def test_reordering_contributions_rejects_a_withdrawal_id(client, db_session) -> None:
    """Both directions share one table, and each reorder renumbers only its own direction's rows.

    A withdrawal's id names nothing in the contribution ordering, so it is
    refused outright rather than quietly ignored — the same invariant the
    whole-list `PUT`'s direction check used to enforce, now a consequence of
    the id set having to match exactly.
    """
    _create_goal(db_session)
    contribution = _add_contribution_automation(client)
    withdrawal = _add_withdrawal(client)

    response = _reorder(client, "contributions", [contribution["automation_id"], withdrawal["automation_id"]])

    assert response.status_code == 400
    assert withdrawal["automation_id"] in response.json()["detail"]


def test_reordering_withdrawals_rejects_a_contribution_id(client, db_session) -> None:
    _create_goal(db_session)
    contribution = _add_contribution_automation(client)
    withdrawal = _add_withdrawal(client)

    response = _reorder(client, "withdrawals", [withdrawal["automation_id"], contribution["automation_id"]])

    assert response.status_code == 400


def test_reordering_one_direction_leaves_the_other_untouched(client, db_session) -> None:
    _create_goal(db_session)
    first = _add_contribution_automation(client, value=100.0)
    second = _add_contribution_automation(client, value=200.0)
    withdrawal = _add_withdrawal(client)

    assert _reorder(client, "contributions", [second["automation_id"], first["automation_id"]]).status_code == 200

    stored = _stored_automations(client)
    assert stored[second["automation_id"]]["priority"] == 0
    assert stored[first["automation_id"]]["priority"] == 1
    # The withdrawal row shares the table and was never named — still there, still a withdrawal, still first.
    assert stored[withdrawal["automation_id"]]["direction"] == "withdrawal"
    assert stored[withdrawal["automation_id"]]["priority"] == 0


def test_post_goal_automation_creates_one_with_a_server_generated_id(client, db_session) -> None:
    _create_goal(db_session)
    response = client.post(
        "/api/v1/accounting/goal-automations/contributions",
        json={
            "goal_id": "emergency-fund",
            "start_date": "2026-06-05",
            "frequency": "monthly",
            "mode": "fixed_amount",
            "value": 500.0,
            "currency": "USD",
        },
    )
    assert response.status_code == 201
    automation = response.json()
    assert automation["automation_id"]
    assert automation["goal_id"] == "emergency-fund"
    assert automation["priority"] == 0
    # The `Location` drops the `contributions` segment the create posted to:
    # direction is a field on the automation, not part of its address.
    assert response.headers["Location"].endswith(f"/goal-automations/{automation['automation_id']}")
    followed = client.get(response.headers["Location"])
    assert followed.status_code == 200
    assert followed.json() == automation


def test_post_goal_automation_appends_after_existing_ones_by_priority(client, db_session) -> None:
    _create_goal(db_session)
    payload = {
        "goal_id": "emergency-fund",
        "start_date": "2026-06-05",
        "frequency": "monthly",
        "mode": "fixed_amount",
        "value": 500.0,
        "currency": "USD",
    }
    first = client.post("/api/v1/accounting/goal-automations/contributions", json=payload).json()
    second = client.post("/api/v1/accounting/goal-automations/contributions", json=payload).json()
    assert first["automation_id"] != second["automation_id"]
    assert first["priority"] == 0
    assert second["priority"] == 1


def test_patch_goal_automation_edits_one_without_touching_another(client, db_session) -> None:
    _create_goal(db_session)
    payload = {
        "goal_id": "emergency-fund",
        "start_date": "2026-06-05",
        "frequency": "monthly",
        "mode": "fixed_amount",
        "value": 500.0,
        "currency": "USD",
    }
    first = client.post("/api/v1/accounting/goal-automations/contributions", json=payload).json()
    second = client.post("/api/v1/accounting/goal-automations/contributions", json=payload).json()

    response = client.patch(
        f"/api/v1/accounting/goal-automations/{first['automation_id']}",
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

    additions = {a["automation_id"]: a for a in client.get("/api/v1/accounting/store").json()["goal_automations"]}
    assert additions[first["automation_id"]]["value"] == pytest.approx(750.0)
    assert additions[second["automation_id"]]["value"] == pytest.approx(500.0)  # untouched


def test_patch_goal_automation_404s_for_an_unknown_id(client, db_session) -> None:
    _create_goal(db_session)
    response = client.patch(
        "/api/v1/accounting/goal-automations/nope",
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


def test_patch_goal_automation_404s_for_a_withdrawal_id(client, db_session) -> None:
    """A withdrawal's id names no contribution, and the PATCH path only writes contributions.

    The handler hard-codes `direction="contribution"`, so a
    direction-blind existence check let a withdrawal id through and
    silently rewrote the row: it gained a funding schedule moving money
    the opposite way, and left the drawdown order it was the only entry in.
    Withdrawal ids are client-minted and guessable (`withdrawal:{goal_id}`),
    so this was reachable by anyone holding a goal id.
    """
    _create_goal(db_session)
    _add_withdrawal(client)

    response = client.patch(
        "/api/v1/accounting/goal-automations/withdrawal:emergency-fund",
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
    withdrawals = client.get("/api/v1/accounting/store").json()["goal_automations"]
    still_a_withdrawal = [a for a in withdrawals if a["automation_id"] == "withdrawal:emergency-fund"]
    assert len(still_a_withdrawal) == 1
    assert still_a_withdrawal[0]["direction"] == "withdrawal"


def test_delete_goal_automation_removes_only_that_one(client, db_session) -> None:
    _create_goal(db_session)
    payload = {
        "goal_id": "emergency-fund",
        "start_date": "2026-06-05",
        "frequency": "monthly",
        "mode": "fixed_amount",
        "value": 500.0,
        "currency": "USD",
    }
    first = client.post("/api/v1/accounting/goal-automations/contributions", json=payload).json()
    second = client.post("/api/v1/accounting/goal-automations/contributions", json=payload).json()

    response = client.delete(f"/api/v1/accounting/goal-automations/{first['automation_id']}")
    assert response.status_code == 204
    remaining = {a["automation_id"] for a in client.get("/api/v1/accounting/store").json()["goal_automations"]}
    assert remaining == {second["automation_id"]}


def test_delete_goal_automation_that_is_already_gone_gets_404(client) -> None:
    assert client.delete("/api/v1/accounting/goal-automations/nope").status_code == 404


_BIG_EXPENSE_CSV = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    "DEBIT,06/20/2026,Big unexpected expense,-4000.00,SALE,-4000.00,,\n"
)


def test_run_withdrawal_automation_draws_down_a_goal_when_unallocated_goes_negative(client, db_session) -> None:
    account = _create_account(client, name="Chase Checking", kind="checking", institution="Chase")
    response = client.post(
        "/api/v1/accounting/import",
        files={"file": ("Chase9579.csv", _BIG_EXPENSE_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Chase Checking",
        },
    )
    assert response.status_code == 200
    _create_goal(db_session)
    client.post(
        "/api/v1/accounting/goal-contributions",
        json={"goal_id": "emergency-fund", "date": "2026-06-01T00:00:00", "amount": 1000.0, "currency": "USD"},
    )
    _add_withdrawal(client)

    response = client.post("/api/v1/accounting/goals/run-withdrawal-automation", params={"as_of": "2026-06-25"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["withdrawals"]) == 1
    assert body["withdrawals"][0]["amount"] == pytest.approx(-1000.0)
    # unallocated was -4000 (0 income - 4000 expense) - 1000 (already in the goal) = -5000 shortfall to cover,
    # but the goal only had 1000 to give, so a residual shortfall remains.
    assert body["remaining_shortfall"] == pytest.approx(4000.0)


def test_reordering_withdrawals_never_conflicts(client, db_session) -> None:
    """Reordering withdrawal priorities is a pure last-write-wins ordering op with no version of its own.

    A per-row `expected_version` governs only the row it names (`PATCH /goals/{goal_id}`); nothing
    store-wide governs anything, so repeatedly reordering — even right after a goal edit bumped that
    goal's own row version — simply takes the last order, never a 409.
    """
    _create_goal(db_session)
    client.patch(
        "/api/v1/accounting/goals/emergency-fund",
        json={
            "name": "Emergency Fund",
            "target_amount": 20000.0,
            "target_currency": "USD",
            "target_date": "2028-01-01T00:00:00",
            "color": "#123456",
            "expected_version": 1,
        },
    )
    entry = _add_withdrawal(client)
    first = _reorder(client, "withdrawals", [entry["automation_id"]])
    second = _reorder(client, "withdrawals", [entry["automation_id"]])
    assert first.status_code == 200
    assert second.status_code == 200


def test_reordering_contributions_renumbers_priority_from_list_position(client, db_session) -> None:
    _create_goal(db_session)
    first = _add_contribution_automation(client, value=100.0)
    second = _add_contribution_automation(client, value=200.0)
    third = _add_contribution_automation(client, value=300.0)

    response = _reorder(
        client, "contributions", [third["automation_id"], first["automation_id"], second["automation_id"]]
    )

    assert response.status_code == 200
    # Returned in the submitted order, renumbered 0-based — the same convention
    # `POST /goal-automations/contributions` appends with.
    assert [(a["automation_id"], a["priority"]) for a in response.json()] == [
        (third["automation_id"], 0),
        (first["automation_id"], 1),
        (second["automation_id"], 2),
    ]
    stored = _stored_automations(client)
    assert stored[third["automation_id"]]["priority"] == 0
    assert stored[first["automation_id"]]["priority"] == 1
    assert stored[second["automation_id"]]["priority"] == 2
    # Nothing but `priority` moved: a reorder carries no fields to move.
    assert stored[third["automation_id"]]["value"] == pytest.approx(300.0)


def test_reordering_the_same_order_twice_is_idempotent(client, db_session) -> None:
    _create_goal(db_session)
    first = _add_contribution_automation(client)
    second = _add_contribution_automation(client)
    order = [second["automation_id"], first["automation_id"]]

    assert _reorder(client, "contributions", order).json() == _reorder(client, "contributions", order).json()


def test_reordering_rejects_an_order_missing_a_persisted_automation(client, db_session) -> None:
    """A short list is a deletion in disguise, which is what the id-set check exists to refuse.

    The whole-list `PUT` this replaced treated an omitted entry as "delete
    it", so a reorder request built from a stale list silently dropped rows.
    """
    _create_goal(db_session)
    first = _add_contribution_automation(client)
    second = _add_contribution_automation(client)

    response = _reorder(client, "contributions", [first["automation_id"]])

    assert response.status_code == 400
    assert second["automation_id"] in response.json()["detail"]
    assert set(_stored_automations(client)) == {first["automation_id"], second["automation_id"]}


def test_reordering_rejects_an_order_naming_an_unknown_automation(client, db_session) -> None:
    """And a long list is an insertion in disguise — creates have their own route."""
    _create_goal(db_session)
    first = _add_contribution_automation(client)

    response = _reorder(client, "contributions", [first["automation_id"], "addition:does-not-exist"])

    assert response.status_code == 400
    assert set(_stored_automations(client)) == {first["automation_id"]}


def test_reordering_rejects_the_same_automation_twice(client, db_session) -> None:
    _create_goal(db_session)
    first = _add_contribution_automation(client)
    second = _add_contribution_automation(client)

    response = _reorder(
        client, "contributions", [first["automation_id"], first["automation_id"], second["automation_id"]]
    )

    assert response.status_code == 400


def test_reordering_rejects_moving_a_remainder_off_the_bottom(client, db_session) -> None:
    """`mode="remainder"` means "whatever is left after every other rule ran", so it can only be last.

    The same invariant the single-row `PATCH` checks — a reorder must not be
    able to reach a state a field edit is refused for.
    """
    _create_goal(db_session)
    fixed = _add_contribution_automation(client, value=100.0)
    remainder = _add_contribution_automation(client, mode="remainder")

    response = _reorder(client, "contributions", [remainder["automation_id"], fixed["automation_id"]])

    assert response.status_code == 400
    assert "remainder" in response.json()["detail"]
    stored = _stored_automations(client)
    assert stored[remainder["automation_id"]]["priority"] == 1  # still last


def test_reordering_accepts_a_remainder_that_stays_last(client, db_session) -> None:
    _create_goal(db_session)
    first = _add_contribution_automation(client, value=100.0)
    second = _add_contribution_automation(client, value=200.0)
    remainder = _add_contribution_automation(client, mode="remainder")

    response = _reorder(
        client, "contributions", [second["automation_id"], first["automation_id"], remainder["automation_id"]]
    )

    assert response.status_code == 200
    assert [a["automation_id"] for a in response.json()][-1] == remainder["automation_id"]


def test_reordering_an_empty_direction_is_a_no_op(client, db_session) -> None:
    _create_goal(db_session)
    assert _reorder(client, "contributions", []).json() == []


def test_post_withdrawal_automation_derives_its_id_from_the_goal_and_appends(client, db_session) -> None:
    _create_goal(db_session)
    _create_goal(db_session, goal_id="house")

    first = client.post("/api/v1/accounting/goal-automations/withdrawals", json={"goal_id": "emergency-fund"})
    second = client.post("/api/v1/accounting/goal-automations/withdrawals", json={"goal_id": "house"})

    assert first.status_code == 201
    assert first.json() == {
        "automation_id": "withdrawal:emergency-fund",
        "goal_id": "emergency-fund",
        "direction": "withdrawal",
        "priority": 0,
        "start_date": None,
        "frequency": None,
        "end_date": None,
        "mode": None,
        "value": None,
        "currency": None,
    }
    assert second.json()["priority"] == 1
    # Same `Location` shape as a contribution create: the direction picks the
    # collection posted to, not the created row's address.
    assert first.headers["Location"].endswith("/goal-automations/withdrawal:emergency-fund")
    assert client.get(first.headers["Location"]).json() == first.json()


def test_re_adding_a_goal_to_the_drawdown_order_keeps_its_place(client, db_session) -> None:
    """The id is derived from the goal, so a repeat add is a replace — and must not shove it to the bottom."""
    _create_goal(db_session)
    _create_goal(db_session, goal_id="house")
    _add_withdrawal(client, "emergency-fund")
    _add_withdrawal(client, "house")

    again = client.post("/api/v1/accounting/goal-automations/withdrawals", json={"goal_id": "emergency-fund"})

    assert again.status_code == 200  # replaced, not created
    assert "Location" not in again.headers
    assert again.json()["priority"] == 0
    assert len([a for a in _stored_automations(client).values() if a["direction"] == "withdrawal"]) == 2


def test_leaving_the_drawdown_order_is_a_plain_delete(client, db_session) -> None:
    _create_goal(db_session)
    _create_goal(db_session, goal_id="house")
    emergency = _add_withdrawal(client, "emergency-fund")
    house = _add_withdrawal(client, "house")

    assert client.delete(f"/api/v1/accounting/goal-automations/{emergency['automation_id']}").status_code == 204

    remaining = _stored_automations(client)
    assert set(remaining) == {house["automation_id"]}
    # And the survivor can still be reordered on its own afterwards.
    assert _reorder(client, "withdrawals", [house["automation_id"]]).status_code == 200


def test_simulate_contribution_flags_exceeding_unallocated(client, db_session) -> None:
    _import_checking(client)
    _create_goal(db_session)
    response = client.post(
        "/api/v1/accounting/goals/simulate-contribution",
        json={"goal_id": "emergency-fund", "date": "2026-06-15", "amount": 5000.0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["exceeds_unallocated"] is True
    assert body["unallocated_as_of_date"] == pytest.approx(3000.0)


def test_simulate_contribution_within_unallocated_does_not_flag(client, db_session) -> None:
    _import_checking(client)
    _create_goal(db_session)
    response = client.post(
        "/api/v1/accounting/goals/simulate-contribution",
        json={"goal_id": "emergency-fund", "date": "2026-06-15", "amount": 500.0},
    )
    assert response.json()["exceeds_unallocated"] is False
