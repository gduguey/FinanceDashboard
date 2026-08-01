"""Starting, reporting and finishing a background sync run — everything about `trades.sync_runs` but the routes.

## Why a thread pool and not a broker

`deploy/` is one container: `alembic upgrade head && exec uvicorn`, no
worker service, no Redis, no Celery. Introducing one for a job that runs
when a person clicks Sync would be a second thing to deploy, monitor and
restart for a workload of a few runs a day, so the runner lives inside the
API process — a small `ThreadPoolExecutor` owned by the app's own lifespan.

Deliberately **not** Starlette's `BackgroundTasks`, which is the obvious
choice and the wrong one: it is awaited inside the ASGI call, so the
connection that returned `202` stays occupied for the whole sync and a
graceful shutdown blocks behind it. A task submitted here is detached from
the request that started it the moment the response is written.

The sync itself is I/O — it spends most of its time waiting on IBKR — so
threads are the right shape, and the pool is small because the container is
capped at one CPU.

## What a restart does, and why the sweep is not optional

An in-process runner cannot survive `docker stop`. A run that was `running`
when the process died stays `running` for ever, and because
`uq_sync_runs_active_user` treats that as a sync in flight, **that user
could never start another one**. So `fail_interrupted_runs` runs at startup
and closes any run left open. It is safe precisely because the deployment is
a single container with a single uvicorn worker: nothing else can own a live
run at the moment this process starts. That assumption is written down here
rather than left implicit, because it is the one thing a second replica
would break.

## What the runner must not change

`brokers.ibkr.main.sync_ibkr_account` commits per successful step and rolls
back per failed one, on purpose, so a partly-successful pull keeps what it
managed to write. Moving the work off the request path must not quietly turn
that into all-or-nothing, so the runner calls the same function through the
same session semantics and records a failed leg in `steps` — the run still
ends `succeeded`, exactly as the synchronous endpoint still answered 200.
`failed` is reserved for the runner itself not finishing.

Progress updates therefore go through **their own short session**, committed
immediately. Writing them on the sync's session would either roll them back
with a failed step or commit mid-sync and reset the transaction-local RLS
setting under the reads that follow (see `db.session.set_rls_user`).
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from db.session import create_one_shot_engine, session_scope
from db.settings import DatabaseSettings
from trades.db.models import ACTIVE_SYNC_RUN_STATES, SyncRun

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MAX_CONCURRENT_SYNCS = 2
"""How many syncs may run at once across every user in this process.

Two, not one: a second user must not queue behind the first for what can be
several minutes of waiting on IBKR. Not many more either, and the bound is
the connection pool rather than the CPU. A run holds one connection for as
long as it takes, out of `pool_size = 5` plus `max_overflow = 5`
(`db.settings.AppRuntimeDatabaseSettings`) shared with every request the
process is serving — so two long-lived syncs leave eight, and raising this
without raising those would starve the requests instead. Each progress
update takes a further connection, but only for one statement.

One user cannot occupy both slots: `uq_sync_runs_active_user` already limits
them to one run at a time.
"""


class SyncAlreadyRunningError(RuntimeError):
    """A second sync was refused because this user already has a `queued` or `running` one.

    Raised from the database's own partial unique index rather than from a
    check-then-insert, so it is a real guarantee rather than a narrow race.
    """

    def __init__(self, run_id: uuid.UUID) -> None:
        self.run_id = run_id
        """The run already in flight, so the caller can point the client at it."""
        super().__init__(f"a sync run is already in flight for this user: {run_id}")


def start_run(session: Session, user_id: uuid.UUID) -> SyncRun:
    """Claim this user's one active sync slot by inserting a `queued` run.

    Parameters
    ----------
    session
        The request's session; committed here, so the runner (on another
        thread, with its own session) can see the row it is about to be
        handed.
    user_id
        Whose sync this is.

    Returns
    -------
    SyncRun
        The newly created run.

    Raises
    ------
    SyncAlreadyRunningError
        If this user already has a run in flight. Detected by the insert
        failing `uq_sync_runs_active_user`, not by looking first — a check
        before an insert is exactly the race the index exists to remove.
    sqlalchemy.exc.IntegrityError
        Re-raised untouched when the failed insert was *not* that conflict.
        Reporting an unrelated constraint violation as "a sync is already
        running" would send the caller looking in the wrong place.
    """
    run = SyncRun(user_id=user_id, state="queued", step="Queued", percent=0.0)
    session.add(run)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        existing = session.execute(
            select(SyncRun.id).where(SyncRun.user_id == user_id, SyncRun.state.in_(ACTIVE_SYNC_RUN_STATES))
        ).scalar_one_or_none()
        if existing is None:
            # Not the uniqueness conflict — some other constraint failed, and
            # burying it under "already running" would be a lie.
            raise
        raise SyncAlreadyRunningError(existing) from error
    return run


def load_run(session: Session, run_id: uuid.UUID) -> SyncRun | None:
    """Read one run, or `None` if this user has no run by that id.

    No `user_id` predicate, deliberately: `trades.sync_runs` carries a
    Row-Level Security policy, so another tenant's run is not merely
    filtered out here, it is invisible to the statement. A handler turns the
    `None` into a 404, which is also the right answer for a run that never
    existed — the two are indistinguishable to the caller on purpose.

    Returns
    -------
    SyncRun or None
    """
    return session.get(SyncRun, run_id)


def recent_runs(session: Session, limit: int, offset: int) -> tuple[list[SyncRun], int]:
    """Read this user's runs, newest first, plus how many there are in total.

    Ordered by `id` rather than by `created_at`: the primary key is a UUIDv7
    (`db.base.UUID7_DEFAULT`), so it is already time-ordered, and ordering on
    it is both a total order and one the index can serve. Two runs created in
    the same clock tick would tie on a timestamp.

    Returns
    -------
    tuple[list[SyncRun], int]
        The page, and the total number of runs.
    """
    total = session.execute(select(func.count()).select_from(SyncRun)).scalar_one()
    rows = session.execute(select(SyncRun).order_by(SyncRun.id.desc()).limit(limit).offset(offset)).scalars().all()
    return list(rows), total


def _apply(user_id: uuid.UUID, run_id: uuid.UUID, **values: object) -> None:
    """Write one set of columns onto a run, in its own committed transaction.

    Its own session on purpose — see this module's docstring. A progress
    update sharing the sync's transaction would be rolled back along with a
    failed step, and committing on that session mid-sync would drop the
    transaction-local `app.current_user_id` under the reads that follow.
    """
    with session_scope(user_id) as session:
        session.execute(update(SyncRun).where(SyncRun.id == run_id).values(**values))
        session.commit()


def begin_run(user_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Mark a run as picked up by the runner, and stamp when.

    Separate from the first `report_progress` so `started_at` is written
    exactly once, by the transition that owns it, rather than conditionally
    on every progress tick. The gap between `created_at` and this is how
    long the run waited for a free pool slot, which is the only thing that
    distinguishes a slow sync from a queued one.
    """
    _apply(user_id, run_id, state="running", started_at=datetime.now(tz=UTC).replace(tzinfo=None))


def report_progress(user_id: uuid.UUID, run_id: uuid.UUID, step: str, percent: float) -> None:
    """Record where a run has got to, readable by any worker and any request."""
    _apply(user_id, run_id, step=step, percent=percent)


def finish_run(
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    synced_at: str | None,
    new_event_count: int,
    total_event_count: int,
    steps: list[dict[str, object]],
) -> None:
    """Close a run that completed, whatever its individual legs reported.

    `succeeded` even when a leg failed: the run is the unit that finished,
    and a failed IBKR pull is recorded in `steps`, which preserves the
    partial-success contract the synchronous endpoint had.
    """
    _apply(
        user_id,
        run_id,
        state="succeeded",
        step="Done",
        percent=100.0,
        finished_at=datetime.now(tz=UTC).replace(tzinfo=None),
        synced_at=synced_at,
        new_event_count=new_event_count,
        total_event_count=total_event_count,
        steps=steps,
    )


def fail_run(user_id: uuid.UUID, run_id: uuid.UUID, error: str) -> None:
    """Close a run whose *runner* did not finish — never a run whose broker leg merely failed."""
    _apply(
        user_id,
        run_id,
        state="failed",
        step="Sync failed",
        percent=100.0,
        error=error,
        finished_at=datetime.now(tz=UTC).replace(tzinfo=None),
    )


def fail_interrupted_runs() -> int:
    """Close every run left `queued` or `running` by a process that is no longer here.

    Called once at application startup, and **required** rather than
    tidiness. `uq_sync_runs_active_user` counts a `running` row as a sync in
    flight, so a container restart part-way through a sync would leave that
    user unable to ever start another one — a permanent, silent wedge caused
    by a restart at the wrong moment.

    Correct only because the deployment is a single container running a
    single uvicorn worker (`deploy/Dockerfile`), so no other process can own
    a live run at the moment this one starts. A second replica would need a
    heartbeat or a lease instead, and this is the function that would have to
    change.

    Connects as the migration-owning superuser rather than through
    `session_scope`, because it has to reach every user's rows at once and
    Row-Level Security would hide all of them — the same deliberate bypass
    `db.backup` and the price-sync user enumeration make, through the same
    unpooled one-shot engine.

    Returns
    -------
    int
        How many runs were closed, for the startup log.
    """
    # database_url has no default (see DatabaseSettings) — pydantic-settings fills it from
    # DATABASE_URL at runtime, but mypy has no pydantic plugin configured here to know that,
    # so it sees a required constructor argument never passed.
    engine = create_one_shot_engine(DatabaseSettings().database_url)  # type: ignore[call-arg]
    try:
        with engine.begin() as connection:
            result = connection.execute(
                update(SyncRun)
                .where(SyncRun.state.in_(ACTIVE_SYNC_RUN_STATES))
                .values(
                    state="failed",
                    step="Sync failed",
                    percent=100.0,
                    error="Interrupted by a server restart. Start a new sync.",
                    finished_at=datetime.now(tz=UTC).replace(tzinfo=None),
                )
            )
    finally:
        engine.dispose()
    closed = result.rowcount
    if closed:
        logger.warning("Closed %d sync run(s) left open by a previous process", closed)
    return closed


class SyncRunner:
    """The pool background syncs run on, owned by the app's lifespan.

    A class rather than a module-level executor so that its lifetime is the
    application's, explicitly: created on startup, shut down on teardown,
    and never quietly resurrected by an import.
    """

    def __init__(self) -> None:
        self._pool: ThreadPoolExecutor | None = None

    def start(self) -> None:
        """Open the pool. Called from the lifespan handler, before the first request."""
        self._pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_SYNCS, thread_name_prefix="sync-run")

    def shutdown(self) -> None:
        """Wait for in-flight syncs and close the pool.

        Waits rather than cancels: a sync that is part-way through has
        already committed the steps it finished, and abandoning it would
        leave the run row saying `running` for the sweep to close on the next
        boot. Letting it finish is both faster and truthful.
        """
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def submit(self, job: Callable[[], None]) -> None:
        """Hand `job` to the pool.

        Raises
        ------
        RuntimeError
            If the pool is not open, which means the lifespan handler did not
            run — a misconfiguration rather than a request-time failure, and
            one that must not be papered over by running the sync inline on
            the request thread.
        """
        if self._pool is None:
            message = "the sync runner is not started; trades.api.dependencies' lifespan handler owns it"
            raise RuntimeError(message)
        self._pool.submit(job)


runner = SyncRunner()
"""The one runner for this process. Started and stopped by `trades.api.dependencies`' lifespan."""
