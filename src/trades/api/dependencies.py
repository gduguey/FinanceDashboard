"""`app` and private helpers shared by 2+ of `trades.api`'s routers.

`app` lives here (rather than in `trades.api.api`) so every router can
import it without a circular import back through the module that
includes them. A helper only ever called from within a single router's
own file stays defined there instead — see that router module for those.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from sqlalchemy.orm import Session

from trades.api.api_models import SyncProgress
from trades.api.auth import validate_clerk_settings
from trades.brokers.ibkr import api as ibkr_api
from trades.brokers.ibkr import main
from trades.config import AppConfig

if TYPE_CHECKING:
    import uuid
    from collections.abc import AsyncIterator

    import polars as pl


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Validate `CLERK_SECRET_KEY` at process startup rather than on the first authenticated request."""
    validate_clerk_settings()
    yield


app = FastAPI(title="Investments API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)
app.state.config = AppConfig()
app.state.sync_progress = {}
"""`dict[uuid.UUID, SyncProgress]` — each user's own sync progress, keyed by
their own id. Per-user rather than one shared value: two different people
syncing around the same time must never see each other's step/percent/error
(see `_report_sync_progress` and `trades.api.routers.sync.get_sync_progress`).
A user absent from this dict has simply never triggered a sync yet."""


def _config() -> AppConfig:
    """Return the configuration attached to the running app.

    Held on `app.state` instead of a module-level variable so tests can
    swap it per-test through the app instance itself, rather than through
    mutable state shared across every import of this module.

    Returns
    -------
    AppConfig
        The application configuration currently attached to `app.state`.
    """
    return cast("AppConfig", app.state.config)


def _report_sync_progress(user_id: uuid.UUID, step: str, percent: float) -> None:
    """Record `user_id`'s current sync step and progress, readable via `GET /api/v1/trades/sync/progress`."""
    cast("dict[uuid.UUID, SyncProgress]", app.state.sync_progress)[user_id] = SyncProgress(
        step=step, percent=percent, done=False
    )


def _load_ledger(session: Session, user_id: uuid.UUID) -> pl.DataFrame:
    """Load the cached ledger. Read-only — never touches the network.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose ledger to load.

    Returns
    -------
    polars.DataFrame
        The full ledger, in chronological order.

    Raises
    ------
    HTTPException
        If no ledger has been cached yet (404).
    """
    ledger = main.load_ledger(session, user_id)
    if ledger.is_empty():
        message = "No ledger cached yet. Hit Sync to pull it from IBKR."
        raise HTTPException(status_code=404, detail=message)
    return ledger


def _first_event_date(ledger: pl.DataFrame) -> date:
    """Return the date of the ledger's earliest event.

    Returns
    -------
    datetime.date
    """
    return cast("date", ledger["event_datetime"].dt.date().min())


def _to_display_zone(value: datetime, local_zone: str) -> datetime:
    """Attach a display timezone to a naive-UTC datetime, for human-facing output.

    Parameters
    ----------
    value
        A naive UTC datetime (the storage format everywhere in this app).
    local_zone
        An IANA zone name — the caller's resolved
        `trades.dashboard.settings.resolved_local_zone`, not read here, so
        this stays a plain per-user-agnostic helper.

    Returns
    -------
    datetime.datetime
        `value` converted to `local_zone` and made tz-aware, so its
        `isoformat()` carries a real UTC offset.
    """
    return value.replace(tzinfo=UTC).astimezone(ZoneInfo(local_zone))


def _last_synced_iso(user_id: uuid.UUID, local_zone: str) -> str | None:
    """Return this user's most recent IBKR sync time, in `local_zone`, or `None` if they've never synced.

    Parameters
    ----------
    user_id
        Whose last sync time to look up.
    local_zone
        An IANA zone name — see `_to_display_zone`.

    Returns
    -------
    str or None
    """
    last_synced = ibkr_api.last_synced_at(_config(), user_id)
    return _to_display_zone(last_synced, local_zone).isoformat() if last_synced else None
