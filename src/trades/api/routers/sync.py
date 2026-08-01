"""Sync endpoints — mirrors `trades.brokers.ibkr`: pulling a fresh statement into the ledger.

A sync is a **resource**, not a request that takes a long time to return.
`POST /sync-runs` answers `202 Accepted` with a `Location` pointing at the
run it created, a background runner does the work, and `GET` on that address
reports where it has got to and, once it is done, what it did.

That replaces `POST /sync`, which ran the whole IBKR pull inside the request
and answered 200 when it finished, reporting progress into an in-process
dict (known gap 2). The dict was already wrong with more than one uvicorn
worker — a poll landing on a worker that had never run the sync reported
nothing — and the per-user `threading.Lock` beside it had exactly the same
defect. Both are gone; `trades.sync_runs` and its partial unique index do
the same two jobs in the database, where every worker can see them.

The `303 See Other` once proposed alongside this is dropped permanently, not
deferred: there is no created resource to redirect *to* that the `Location`
does not already name, and `fetch()` follows a redirect invisibly, so the
SPA could not have distinguished it from the 200 it already got.
"""

from __future__ import annotations

import io
import logging
import uuid
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from db.current_user import get_current_user_id
from db.session import get_db, session_scope, set_rls_user
from http_api.locations import ACCEPTED_WITH_LOCATION, location_of
from http_api.pagination import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX
from trades import dashboard
from trades.api import sync_runs
from trades.api.api_models import SyncResult, SyncRunPage, SyncRunResource, SyncStep
from trades.api.dependencies import _config, _last_synced_iso
from trades.brokers.ibkr import main
from trades.brokers.ibkr.credentials import BROKER_DISPLAY_NAME, resolve_ibkr_credentials
from trades.config import AppConfig
from trades.utils.statement_archive import StatementArchive

if TYPE_CHECKING:
    from collections.abc import Callable

    from trades.db.models import SyncRun

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/statements/export")
def get_statements_export(user_id: Annotated[uuid.UUID, Depends(get_current_user_id)]) -> Response:
    """Zip every raw Flex statement archived from a sync (verbatim XML, as received) for download.

    Returns
    -------
    fastapi.Response
        A `.zip` attachment, one entry per archived statement, empty if
        nothing has ever been synced.
    """
    buffer = io.BytesIO()
    archive = StatementArchive(_config().ibkr.raw_statement_dir, f"statements/{user_id}/ibkr")
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for relative_path, data in archive.read_all():
            zip_file.writestr(relative_path, data)
    filename = f"trades-statements-{datetime.now(tz=UTC).date().isoformat()}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _run_sync(
    config: AppConfig, session: Session, user_id: uuid.UUID, on_progress: Callable[[str, float], None]
) -> SyncResult:
    """Pull the latest IBKR statement into the ledger.

    Unchanged in substance by the move to a background runner, deliberately.
    Price, benchmark, CPI, and HYSA-rate cache refreshes used to run here
    too; they're now standalone cron jobs, so this is just the one IBKR leg.
    `steps` still reports it as a list (of one) for the UI, and a failure
    here is caught rather than raised so the *run* still completes with the
    failure recorded in `steps` instead — the partial-success contract the
    synchronous endpoint had.

    Returns
    -------
    SyncResult
        `synced_at`, `new_event_count`, `total_event_count`, and `steps`
        — the one IBKR leg's own `label`/`ok`/`error`.
    """
    steps: list[SyncStep] = []

    on_progress("Connecting to IBKR", 0.0)
    sync_result = None
    try:
        credentials = resolve_ibkr_credentials(session, user_id)
        sync_result = main.sync_ibkr_account(credentials, config, session, user_id, on_progress=on_progress)
        steps.append(SyncStep(label=f"{BROKER_DISPLAY_NAME} data", ok=True))
    except requests.exceptions.RequestException:
        # Roll back first: a failure part-way through `sync_ibkr_account`'s
        # writes leaves the session's transaction aborted, which would make the
        # `load_ledger`/`load_settings` reads below fail too.
        session.rollback()
        # Message only, never the exception: a request-level failure's own text
        # includes the full IBKR request URL, which embeds the token as a query
        # param (see trades.brokers.ibkr.api._send_flex_request) — it must reach
        # neither the client nor the server logs.
        logger.error(  # noqa: TRY400 — message-only on purpose; the exception text embeds the IBKR token
            "IBKR sync: request to IBKR failed for user %s (detail omitted — contains the token)", user_id
        )
        steps.append(SyncStep(label=f"{BROKER_DISPLAY_NAME} data", ok=False, error="Could not reach IBKR"))
    except Exception as error:
        session.rollback()  # same reason as above — keep the session usable for the fallback reads
        # Log the full traceback: these are the token-free failures (parse, archive, DB, gap check)
        # a bare failed step would otherwise hide, leaving a sync that silently does nothing.
        logger.exception("IBKR sync failed for user %s", user_id)
        steps.append(SyncStep(label=f"{BROKER_DISPLAY_NAME} data", ok=False, error=str(error)))

    # sync_ibkr_account ends the transaction either way — _write_ledger commits
    # on success, the except branches roll back on failure — and that resets the
    # transaction-local app.current_user_id GUC to '', so the RLS-scoped reads
    # below would cast ''::uuid and 500. Re-establish it first (see
    # db.session.set_rls_user). Still needed on a background session: the reset
    # is a property of the transaction, not of the request.
    set_rls_user(session, user_id, background=True)

    if sync_result is not None:
        new_event_count = sync_result.new_event_count
        total_event_count = sync_result.total_event_count
    else:
        # IBKR itself never produced a fresh count — fall back to whatever
        # the ledger already holds from a previous sync.
        new_event_count = 0
        total_event_count = main.load_ledger(session, user_id).height

    local_zone = dashboard.resolved_local_zone(config, dashboard.load_settings(session, user_id))
    return SyncResult(
        synced_at=_last_synced_iso(user_id, local_zone),
        new_event_count=new_event_count,
        total_event_count=total_event_count,
        steps=steps,
    )


def _execute_run(config: AppConfig, user_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Run one sync to completion on a background thread, reporting into its row throughout.

    Opens its own session rather than borrowing the request's, which is long
    closed by the time this runs, and takes `session_scope`'s background
    statement-timeout bound because a broker pull is exactly the work that
    bound exists for.

    The outer `except` catches everything, which is the one place in this
    file that is justified: an exception escaping here would leave the row
    saying `running`, and `uq_sync_runs_active_user` would then refuse this
    user every future sync until a restart swept it.
    """

    def on_progress(step: str, percent: float) -> None:
        """Report this run's progress into its own row, on its own transaction."""
        sync_runs.report_progress(user_id, run_id, step, percent)

    try:
        sync_runs.begin_run(user_id, run_id)
        with session_scope(user_id) as session:
            result = _run_sync(config, session, user_id, on_progress)
        sync_runs.finish_run(
            user_id,
            run_id,
            synced_at=result.synced_at,
            new_event_count=result.new_event_count,
            total_event_count=result.total_event_count,
            steps=[step.model_dump() for step in result.steps],
        )
    except Exception as error:
        # Only reachable for something outside every leg's own try/except in
        # `_run_sync` — each expected failure mode is already caught there and
        # reported per-step instead.
        logger.exception("Sync run %s failed outside any step for user %s", run_id, user_id)
        sync_runs.fail_run(user_id, run_id, str(error))


def _resource(run: SyncRun) -> SyncRunResource:
    """Project one stored run onto its wire shape.

    Returns
    -------
    SyncRunResource
    """
    return SyncRunResource(
        id=run.id,
        # The column's CHECK constraint restates the same Literal the field
        # declares (`db.models.SyncRunState`), so a row can hold nothing else.
        state=run.state,  # type: ignore[arg-type]
        step=run.step,
        percent=run.percent,
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
        synced_at=run.synced_at,
        new_event_count=run.new_event_count,
        total_event_count=run.total_event_count,
        steps=[SyncStep(**step) for step in run.steps],
    )


@router.post("/sync-runs", status_code=202, responses=ACCEPTED_WITH_LOCATION)
def post_sync_run(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> SyncRunResource:
    """Start a sync and answer immediately with the run that will report on it.

    `202`, not `200`: the pull has not happened when this returns, and the
    IBKR leg alone can take minutes. `Location` names the run, which is where
    a client watches it.

    A second start while one is in flight is a `409` whose own `Location`
    points at the run already going — so a client that lost track of a run
    is handed it back rather than only refused. That refusal comes from
    `uq_sync_runs_active_user`, a partial unique index, not from a check in
    this handler: the per-process lock it replaced could only serialize the
    worker it happened to live in.

    Returns
    -------
    SyncRunResource
        The `queued` run, with nothing filled in yet but its identity.

    Raises
    ------
    HTTPException
        409 if this user already has a sync in flight, or 503 if the run
        could not be handed to the runner — in which case the run it just
        created is closed rather than left claiming the user's one slot.
    """
    try:
        run = sync_runs.start_run(session, user_id)
    except sync_runs.SyncAlreadyRunningError as error:
        raise HTTPException(
            status_code=409,
            detail="A sync is already running for this account.",
            headers={"Location": str(request.url_for("get_sync_run", run_id=str(error.run_id)))},
        ) from error

    try:
        sync_runs.runner.submit(lambda: _execute_run(_config(), user_id, run.id))
    except Exception as error:
        # `start_run` has already committed a `queued` row, and
        # `uq_sync_runs_active_user` counts that as a sync in flight. Letting
        # this escape would leave the row behind and block every future sync
        # for this user until the next restart's sweep — a permanent
        # consequence from a transient cause, and the likeliest cause is
        # exactly transient: `submit` raises once `runner.shutdown()` has run,
        # which is a window a graceful shutdown really passes through while
        # the server is still accepting requests.
        logger.exception("Could not hand sync run %s to the runner for user %s", run.id, user_id)
        sync_runs.fail_run(user_id, run.id, "Could not start the sync. Try again.")
        raise HTTPException(status_code=503, detail="Could not start a sync right now. Try again.") from error

    location_of(request, response, "get_sync_run", run_id=str(run.id))
    return _resource(run)


@router.get("/sync-runs/{run_id}")
def get_sync_run(run_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]) -> SyncRunResource:
    """Report one sync run — its progress while it is going, its result once it is done.

    Returns
    -------
    SyncRunResource

    Raises
    ------
    HTTPException
        404 if no such run belongs to this user. Another tenant's run is
        invisible rather than forbidden (Row-Level Security), so it answers
        the same way a run that never existed does.
    """
    run = sync_runs.load_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such sync run.")
    return _resource(run)


@router.get("/sync-runs")
def get_sync_runs(
    session: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, description="How many runs to return, newest first.")] = PAGE_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0, description="How many runs to skip.")] = 0,
) -> SyncRunPage:
    """List this user's sync runs, newest first.

    The reason this exists rather than only the item `GET`: a browser reload
    part-way through a sync loses the run id the `POST` returned, and with no
    way to ask "what is my latest run" the progress bar would simply die for
    the rest of that sync. A sync history falls out of it for free.

    Bounded like every other collection here — a page is capped at
    `PAGE_LIMIT_MAX`, and a larger `limit` is clamped rather than rejected.

    Returns
    -------
    SyncRunPage
    """
    limit = min(limit, PAGE_LIMIT_MAX)
    runs, total = sync_runs.recent_runs(session, limit, offset)
    return SyncRunPage(
        items=[_resource(run) for run in runs],
        window_unit="run",
        total=total,
        limit=limit,
        offset=offset,
    )
