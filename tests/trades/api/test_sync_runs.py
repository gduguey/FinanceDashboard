"""The background runner itself: what a run records, and what it must not turn into.

Separate from `test_api.py`'s route tests because these need sessions that
really commit. The runner opens its own session on its own thread — the
request that started it is long closed — so anything written inside the
`db_session` fixture's never-committed transaction would be invisible to it.

The property most worth protecting here is one the move to a background
runner could easily have broken by accident.
`brokers.ibkr.main.sync_ibkr_account` commits per successful step and rolls
back per failed one, deliberately, so a partly-successful pull keeps what it
managed to write; `known-gaps.md`'s gap 1 names that as one of the two
blockers against a single request-scoped transaction. A runner that wrapped
the whole thing in one unit of work, or that treated a failed broker leg as a
failed run, would have converted partial success into all-or-nothing without
any test noticing — the endpoint's status code was the only thing that used
to say so, and there is no status code any more.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest
import requests
from sqlalchemy.orm import Session

import db.models
import db.session as session_module
from trades.api import sync_runs
from trades.api.routers import sync as sync_router
from trades.config import AppConfig
from trades.db.models import SyncRun

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine


@pytest.fixture(autouse=True)
def _runner_uses_the_test_engine(monkeypatch: pytest.MonkeyPatch, _db_engine: Engine) -> None:
    """Point `session_scope` — which the runner opens for itself — at the test database."""
    monkeypatch.setattr(session_module, "get_engine", lambda: _db_engine)


@pytest.fixture
def committed_user_id(_db_engine: Engine) -> Iterator[uuid.UUID]:
    """A `User` row a second connection can see, and its runs cleaned up afterwards.

    Yields
    ------
    uuid.UUID
    """
    user_id = uuid.uuid4()
    with Session(_db_engine) as setup:
        setup.add(db.models.User(id=user_id, email=f"{user_id}@example.com"))
        setup.commit()
    yield user_id
    with Session(_db_engine) as teardown:
        teardown.query(SyncRun).filter_by(user_id=user_id).delete()
        teardown.query(db.models.User).filter_by(id=user_id).delete()
        teardown.commit()


@pytest.fixture
def config(tmp_path: Any) -> AppConfig:
    """A configuration whose caches are throwaway, so no test touches the real ones.

    Returns
    -------
    AppConfig
    """
    return AppConfig(
        ibkr={"cache_dir": tmp_path / "ibkr"},
        prices={"cache_dir": tmp_path / "prices"},
        cpi={"cache_dir": tmp_path / "cpi"},
        hysa_rates={"cache_dir": tmp_path / "hysa_rates"},
    )


def _queue(engine: Engine, user_id: uuid.UUID) -> uuid.UUID:
    """Insert a `queued` run the way `POST /sync-runs` does, and commit it.

    Returns
    -------
    uuid.UUID
    """
    with Session(engine) as session:
        return sync_runs.start_run(session, user_id).id


def _read(engine: Engine, run_id: uuid.UUID) -> SyncRun:
    """Read one run back on its own connection.

    Returns
    -------
    SyncRun
    """
    with Session(engine) as reader:
        run = reader.get(SyncRun, run_id)
        assert run is not None
        return run


def test_a_completed_run_records_its_result_on_the_row(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    committed_user_id: uuid.UUID,
    config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Progress and result land on one row, so a client polling to completion already holds the outcome."""
    monkeypatch.setattr(
        sync_router,
        "_run_sync",
        lambda _config, _session, _user_id, on_progress: _fake_result(on_progress),
    )
    run_id = _queue(_db_engine, committed_user_id)

    sync_router._execute_run(config, committed_user_id, run_id)

    run = _read(_db_engine, run_id)
    assert run.state == "succeeded"
    assert run.step == "Done"
    assert run.percent == pytest.approx(100.0)
    assert run.error is None
    assert run.finished_at is not None
    assert run.new_event_count == 7
    assert run.total_event_count == 12
    assert run.steps == [{"label": "IBKR data", "ok": True, "error": None}]
    # Stamped when the runner picked the run up, not when the row was created:
    # the gap between the two is how long it waited for a free pool slot,
    # which is the only thing distinguishing a slow sync from a queued one.
    assert run.started_at is not None
    assert run.finished_at >= run.started_at


def _fake_result(on_progress: Any, *, ok: bool = True) -> Any:
    """Stand in for a real pull, reporting progress the way the real one does.

    Returns
    -------
    SyncResult
    """
    from trades.api.api_models import SyncResult, SyncStep  # noqa: PLC0415 — keeps the module's imports about the runner

    on_progress("Connecting to IBKR", 0.0)
    on_progress("Merging into ledger", 55.0)
    return SyncResult(
        synced_at="2026-08-01T09:00:00+00:00",
        new_event_count=7 if ok else 0,
        total_event_count=12,
        steps=[SyncStep(label="IBKR data", ok=ok, error=None if ok else "Could not reach IBKR")],
    )


def test_progress_is_visible_to_another_connection_while_the_run_is_going(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    committed_user_id: uuid.UUID,
    config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of moving progress out of `app.state`: any worker can read it, not just the one that ran the sync.

    Asserted by reading the row from a *different* connection at the moment
    the runner reports a step — which is exactly what a poll landing on
    another uvicorn worker does, and exactly what the in-process dict could
    not serve.
    """
    seen: list[tuple[str, float]] = []

    def observe(_config: AppConfig, _session: Session, _user_id: uuid.UUID, on_progress: Any) -> Any:
        on_progress("Fetching statement", 30.0)
        mid = _read(_db_engine, run_id)
        seen.append((mid.step, mid.percent))
        return _fake_result(on_progress)

    monkeypatch.setattr(sync_router, "_run_sync", observe)
    run_id = _queue(_db_engine, committed_user_id)

    sync_router._execute_run(config, committed_user_id, run_id)

    assert seen == [("Fetching statement", 30.0)]


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(requests.exceptions.ConnectionError("no route to host"), id="ibkr-unreachable"),
        pytest.param(ValueError("IBKR Flex API error 1018: too many requests"), id="ibkr-rejects"),
    ],
)
def test_a_failed_broker_leg_still_completes_the_run(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    committed_user_id: uuid.UUID,
    config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    """Partial success survives the move off the request path, which is the thing most at risk here.

    The synchronous endpoint answered 200 with a failed step, because a sync
    commits what each step managed and rolls back only the step that failed.
    The run is the unit that finished, so it is `succeeded` with the failure
    recorded in `steps` — `failed` would have quietly redefined a partly
    successful pull as a total loss.
    """

    def failing_pull(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(sync_router.main, "sync_ibkr_account", failing_pull)
    monkeypatch.setattr(sync_router, "resolve_ibkr_credentials", lambda _session, _user_id: object())
    monkeypatch.setattr(sync_router.main, "load_ledger", lambda _session, _user_id: _EmptyLedger())
    monkeypatch.setattr(sync_router, "_last_synced_iso", lambda _user_id, _zone: None)
    run_id = _queue(_db_engine, committed_user_id)

    sync_router._execute_run(config, committed_user_id, run_id)

    run = _read(_db_engine, run_id)
    assert run.state == "succeeded", "a failed broker leg was reported as a failed run"
    assert run.error is None
    assert run.steps[0]["ok"] is False
    assert run.steps[0]["error"]


class _EmptyLedger:
    """The one attribute `_run_sync`'s fallback reads off a loaded ledger."""

    height = 0


def test_a_runner_crash_closes_the_run_instead_of_wedging_the_slot(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    committed_user_id: uuid.UUID,
    config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception escaping the run body must land the row in a terminal state.

    `uq_sync_runs_active_user` counts `queued` and `running` as a sync in
    flight, so a run left open by a crash would refuse this user every future
    sync. `failed` is reserved for exactly this — the runner not finishing —
    and never for a broker leg that did.
    """

    def explode(*_args: object, **_kwargs: object) -> None:
        message = "something nobody anticipated"
        raise RuntimeError(message)

    monkeypatch.setattr(sync_router, "_run_sync", explode)
    run_id = _queue(_db_engine, committed_user_id)

    sync_router._execute_run(config, committed_user_id, run_id)

    run = _read(_db_engine, run_id)
    assert run.state == "failed"
    assert run.error == "something nobody anticipated"
    assert run.finished_at is not None

    # And the slot is genuinely free again, which is the consequence that matters.
    assert _queue(_db_engine, committed_user_id) != run_id


def test_a_second_run_while_one_is_active_is_refused_by_the_database(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    committed_user_id: uuid.UUID,
) -> None:
    """Two separate connections, so this is the index refusing it and not a Python-side check.

    The per-process `threading.Lock` this replaced could only ever serialize
    requests that happened to land on the same worker.
    """
    first = _queue(_db_engine, committed_user_id)

    with Session(_db_engine) as second, pytest.raises(sync_runs.SyncAlreadyRunningError) as exc_info:
        sync_runs.start_run(second, committed_user_id)

    assert exc_info.value.run_id == first


def test_two_different_users_can_sync_at_once(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    committed_user_id: uuid.UUID,
) -> None:
    """The index is per user, so one person's sync must never block anyone else's."""
    other_user_id = uuid.uuid4()
    with Session(_db_engine) as setup:
        setup.add(db.models.User(id=other_user_id, email=f"{other_user_id}@example.com"))
        setup.commit()
    try:
        assert _queue(_db_engine, committed_user_id) != _queue(_db_engine, other_user_id)
    finally:
        with Session(_db_engine) as teardown:
            teardown.query(SyncRun).filter_by(user_id=other_user_id).delete()
            teardown.query(db.models.User).filter_by(id=other_user_id).delete()
            teardown.commit()


def test_the_startup_sweep_closes_runs_a_dead_process_left_open(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    committed_user_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without this, one restart mid-sync would wedge that user's slot for ever.

    The sweep is the price of running the job inside the API process, and it
    is safe only because the deployment is a single container with a single
    uvicorn worker — nothing else can own a live run when this one boots.
    `create_one_shot_engine` is pointed at the test database here for the
    same reason the real one connects as the superuser: it has to see every
    user's rows at once, which Row-Level Security would otherwise hide.
    """
    monkeypatch.setattr(sync_runs, "create_one_shot_engine", lambda _url: _db_engine)
    monkeypatch.setattr(sync_runs, "DatabaseSettings", _StubSettings)
    monkeypatch.setattr(_db_engine, "dispose", lambda: None)
    run_id = _queue(_db_engine, committed_user_id)

    closed = sync_runs.fail_interrupted_runs()

    assert closed >= 1
    run = _read(_db_engine, run_id)
    assert run.state == "failed"
    assert run.error is not None
    assert "restart" in run.error

    # The user can start again, which is the whole reason the sweep exists.
    assert _queue(_db_engine, committed_user_id) != run_id


class _StubSettings:
    """Stands in for `DatabaseSettings`, whose only use here is the URL the stub engine ignores."""

    database_url = "postgresql+psycopg://unused/unused"
