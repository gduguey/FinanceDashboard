"""`app` and private helpers shared by 2+ of `trades.api`'s routers.

`app` lives here (rather than in `trades.api.api`) so every router can
import it without a circular import back through the module that
includes them. A helper only ever called from within a single router's
own file stays defined there instead — see that router module for those.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from sqlalchemy.orm import Session

from trades.api.api_models import SyncProgress
from trades.brokers.ibkr import api as ibkr_api
from trades.brokers.ibkr import main
from trades.config import AppConfig

if TYPE_CHECKING:
    import uuid

    import polars as pl

app = FastAPI(title="Investments API")
app.state.config = AppConfig()
app.state.sync_progress = SyncProgress(step="Idle", percent=0.0, done=True)


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


def _report_sync_progress(step: str, percent: float) -> None:
    app.state.sync_progress = SyncProgress(step=step, percent=percent, done=False)


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
    return cast("date", ledger["event_datetime"].dt.date().min())


def _to_display_zone(value: datetime, config: AppConfig) -> datetime:
    """Attach the display timezone to a naive-UTC datetime, for human-facing output.

    Parameters
    ----------
    value
        A naive UTC datetime (the storage format everywhere in this app).
    config
        Application configuration; `config.timezone.local_zone` is read.

    Returns
    -------
    datetime.datetime
        `value` converted to `config.timezone.local_zone` and made
        tz-aware, so its `isoformat()` carries a real UTC offset.
    """
    return value.replace(tzinfo=UTC).astimezone(ZoneInfo(config.timezone.local_zone))


def _last_synced_iso(user_id: uuid.UUID) -> str | None:
    config = _config()
    last_synced = ibkr_api.last_synced_at(config, user_id)
    return _to_display_zone(last_synced, config).isoformat() if last_synced else None
