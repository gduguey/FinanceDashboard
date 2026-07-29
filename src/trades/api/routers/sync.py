"""Sync endpoints — mirrors `trades.brokers.ibkr`: pulling a fresh statement into the ledger."""

from __future__ import annotations

import io
import logging
import uuid
import zipfile
from datetime import UTC, datetime
from threading import Lock
from typing import Annotated, cast

import requests
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from db.current_user import get_current_user_id
from db.session import get_db, set_rls_user
from trades import dashboard
from trades.api.api_models import SyncProgress, SyncResult, SyncStep
from trades.api.dependencies import _config, _last_synced_iso, _report_sync_progress, app
from trades.brokers.ibkr import main
from trades.brokers.ibkr.credentials import BROKER_DISPLAY_NAME, resolve_ibkr_credentials
from trades.config import AppConfig
from trades.utils.statement_archive import StatementArchive

logger = logging.getLogger(__name__)

router = APIRouter()

# One lock per user, created on first use — not a single shared lock.
# /api/sync writes only to that user's own ledger rows, so two different
# users syncing at the same time never touch the same data and must never
# block each other; the same user opening two tabs and clicking Sync twice
# still needs serializing against their own concurrent writes, which is
# what each user's own lock is for. `_sync_locks_guard` protects the dict
# itself from a create-race between two concurrent first-ever requests for
# a user who has no lock yet — it is never held for the sync itself.
_sync_locks: dict[uuid.UUID, Lock] = {}
_sync_locks_guard = Lock()


def _lock_for_user(user_id: uuid.UUID) -> Lock:
    """Return this user's own sync lock, creating it on first use.

    Returns
    -------
    threading.Lock
    """
    with _sync_locks_guard:
        if user_id not in _sync_locks:
            _sync_locks[user_id] = Lock()
        return _sync_locks[user_id]


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


@router.get("/sync/progress")
def get_sync_progress(user_id: Annotated[uuid.UUID, Depends(get_current_user_id)]) -> SyncProgress:
    """Return the current (or most recently finished) sync's progress — this user's own, never anyone else's.

    Polled by the frontend's progress bar while a sync is running.
    `POST /api/sync` runs in FastAPI's thread pool (it's a plain `def`,
    not `async def`), so this GET is served concurrently on its own
    thread rather than queued behind the sync request.

    Returns
    -------
    SyncProgress
        `step`, `percent`, `done`, `error` — `"Idle"`/`0.0`/`True`/`None`
        if this user has never triggered a sync.
    """
    sync_progress_by_user = cast("dict[uuid.UUID, SyncProgress]", app.state.sync_progress)
    return sync_progress_by_user.get(user_id, SyncProgress(step="Idle", percent=0.0, done=True))


def _run_sync(config: AppConfig, session: Session, user_id: uuid.UUID) -> SyncResult:
    """Pull the latest IBKR statement into the ledger.

    Price, benchmark, CPI, and HYSA-rate cache refreshes used to run here
    too; they're now standalone cron jobs, so this is just the one IBKR
    leg. `steps` still reports it as a list (of one) for the UI, and a
    failure here is caught rather than raised so the endpoint always
    returns 200 with the failure recorded in `steps` instead.

    Returns
    -------
    SyncResult
        `synced_at`, `new_event_count`, `total_event_count`, and `steps`
        — the one IBKR leg's own `label`/`ok`/`error`.
    """
    steps: list[SyncStep] = []

    def on_progress(step: str, percent: float) -> None:
        """Report this sync's progress under the acting user's own id."""
        _report_sync_progress(user_id, step, percent)

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

    # sync_ibkr_account ends the request's transaction either way — _write_ledger
    # commits on success, the except branches roll back on failure — and that
    # resets the transaction-local app.current_user_id GUC to '', so the
    # RLS-scoped reads below would cast ''::uuid and 500. Re-establish it first
    # (see db.session.set_rls_user).
    set_rls_user(session, user_id)

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


@router.post("/sync")
def sync(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> SyncResult:
    """Pull the latest IBKR statement into the ledger.

    Price, benchmark, CPI, and HYSA-rate cache refreshes no longer happen
    here — they run on their own cron schedule instead. Reports progress to
    this user's own entry in `app.state.sync_progress` throughout, readable
    via `GET /api/sync/progress` — the IBKR pull can take a while, so a
    bare spinner isn't good enough feedback.

    Concurrent requests for the *same* user are serialized by that user's
    own lock, to prevent ledger corruption from simultaneous writes — see
    `_lock_for_user`. Two different users syncing at the same time never
    wait on each other: their syncs write to different, non-overlapping
    ledger rows.

    Returns
    -------
    SyncResult
        `synced_at`, `new_event_count`, `total_event_count`, and `steps`
        — the one IBKR leg's own `label`/`ok`/`error`.
    """
    sync_progress_by_user = cast("dict[uuid.UUID, SyncProgress]", app.state.sync_progress)
    with _lock_for_user(user_id):
        try:
            result = _run_sync(_config(), session, user_id)
        except Exception as error:
            # Only reachable for something outside every leg's own
            # try/except in _run_sync — each expected failure mode is
            # already caught there and reported per-step instead.
            sync_progress_by_user[user_id] = SyncProgress(
                step="Sync failed", percent=100.0, done=True, error=str(error)
            )
            raise

        sync_progress_by_user[user_id] = SyncProgress(step="Done", percent=100.0, done=True)
        return result
