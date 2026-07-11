"""Sync endpoints — mirrors `trades.brokers.ibkr`: pulling a fresh statement into the ledger."""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import UTC, datetime
from threading import Lock
from typing import Annotated, cast

import requests
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from db.current_user import get_current_user_id
from db.session import get_db
from trades.api.api_models import SyncProgress, SyncResult, SyncStep
from trades.api.dependencies import _config, _last_synced_iso, _report_sync_progress, app
from trades.brokers.ibkr import main
from trades.brokers.ibkr.credentials import BROKER_DISPLAY_NAME, resolve_ibkr_credentials
from trades.config import AppConfig
from trades.utils.statement_archive import DEFAULT_USER_ID, StatementArchive

router = APIRouter()

# Lock to prevent concurrent syncs: /api/sync writes to the ledger, so
# concurrent requests would step on each other's writes.
_sync_lock = Lock()


@router.get("/api/statements/export")
def get_statements_export() -> Response:
    """Zip every raw Flex statement archived from a sync (verbatim XML, as received) for download.

    Returns
    -------
    fastapi.Response
        A `.zip` attachment, one entry per archived statement, empty if
        nothing has ever been synced.
    """
    buffer = io.BytesIO()
    archive = StatementArchive(_config().ibkr.raw_statement_dir, f"statements/{DEFAULT_USER_ID}/ibkr")
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for relative_path, data in archive.read_all():
            zip_file.writestr(relative_path, data)
    filename = f"trades-statements-{datetime.now(tz=UTC).date().isoformat()}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/sync/progress")
def get_sync_progress() -> SyncProgress:
    """Return the current (or most recently finished) sync's progress.

    Polled by the frontend's progress bar while a sync is running.
    `POST /api/sync` runs in FastAPI's thread pool (it's a plain `def`,
    not `async def`), so this GET is served concurrently on its own
    thread rather than queued behind the sync request.

    Returns
    -------
    SyncProgress
        `step`, `percent`, `done`, `error`.
    """
    return cast("SyncProgress", app.state.sync_progress)


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

    _report_sync_progress("Connecting to IBKR", 0.0)
    sync_result = None
    try:
        credentials = resolve_ibkr_credentials(session, user_id)
        sync_result = main.sync_ibkr_account(credentials, config, session, on_progress=_report_sync_progress)
        steps.append(SyncStep(label=f"{BROKER_DISPLAY_NAME} data", ok=True))
    except requests.exceptions.RequestException:
        # Not str(error): a request-level failure's own message includes the
        # full IBKR request URL, which embeds the token as a query param (see
        # trades.brokers.ibkr.api._send_flex_request) — must never reach the client.
        steps.append(SyncStep(label=f"{BROKER_DISPLAY_NAME} data", ok=False, error="Could not reach IBKR"))
    except Exception as error:  # noqa: BLE001 — report the failure as a step, not a 500
        steps.append(SyncStep(label=f"{BROKER_DISPLAY_NAME} data", ok=False, error=str(error)))

    if sync_result is not None:
        new_event_count = sync_result.new_event_count
        total_event_count = sync_result.total_event_count
    else:
        # IBKR itself never produced a fresh count — fall back to whatever
        # the ledger already holds from a previous sync.
        new_event_count = 0
        total_event_count = main.load_ledger(session).height

    return SyncResult(
        synced_at=_last_synced_iso(),
        new_event_count=new_event_count,
        total_event_count=total_event_count,
        steps=steps,
    )


@router.post("/api/sync")
def sync(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> SyncResult:
    """Pull the latest IBKR statement into the ledger.

    Price, benchmark, CPI, and HYSA-rate cache refreshes no longer happen
    here — they run on their own cron schedule instead. Reports progress
    to `app.state.sync_progress` throughout, readable via `GET
    /api/sync/progress` — the IBKR pull can take a while, so a bare
    spinner isn't good enough feedback.

    Concurrent requests are serialized by a lock to prevent ledger
    corruption from simultaneous writes.

    Returns
    -------
    SyncResult
        `synced_at`, `new_event_count`, `total_event_count`, and `steps`
        — the one IBKR leg's own `label`/`ok`/`error`.
    """
    with _sync_lock:
        try:
            result = _run_sync(_config(), session, user_id)
        except Exception as error:
            # Only reachable for something outside every leg's own
            # try/except in _run_sync — each expected failure mode is
            # already caught there and reported per-step instead.
            app.state.sync_progress = SyncProgress(step="Sync failed", percent=100.0, done=True, error=str(error))
            raise

        app.state.sync_progress = SyncProgress(step="Done", percent=100.0, done=True)
        return result
