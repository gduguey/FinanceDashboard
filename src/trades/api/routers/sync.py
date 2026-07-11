"""Sync endpoints — mirrors `trades.brokers.ibkr`: pulling a fresh statement and refreshing market/rate caches."""

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
from trades import dashboard
from trades.api.api_models import SyncProgress, SyncResult, SyncStep
from trades.api.dependencies import (
    _config,
    _first_event_date,
    _last_synced_iso,
    _report_sync_progress,
    app,
)
from trades.brokers.ibkr import main
from trades.brokers.ibkr.credentials import resolve_ibkr_credentials
from trades.config import AppConfig
from trades.market_data import cpi as cpi_module
from trades.market_data import hysa_rates as hysa_rates_module
from trades.market_data import prices
from trades.utils.statement_archive import DEFAULT_USER_ID, StatementArchive

router = APIRouter()

# Lock to prevent concurrent syncs: /api/sync writes to caches and the ledger,
# so concurrent requests would step on each other's writes.
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
    """Run every leg of a sync independently — one failing never skips the rest.

    A bad IBKR token shouldn't also block a benchmark price refresh that
    has nothing to do with IBKR; each leg below is caught on its own, so
    e.g. market prices and savings rates still update even if IBKR itself
    is down. `steps` in the return value reports each leg's own
    success/failure — that's what the UI shows, not just one overall
    pass/fail.

    Returns
    -------
    SyncResult
        `synced_at`, `new_event_count`, `total_event_count`,
        `symbols_refreshed`, and `steps` — each leg's own `label`/`ok`/`error`.
    """
    steps: list[SyncStep] = []

    _report_sync_progress("Connecting to IBKR", 0.0)
    sync_result = None
    try:
        credentials = resolve_ibkr_credentials(session, user_id)
        sync_result = main.sync_ibkr_account(credentials, config, session, on_progress=_report_sync_progress)
        steps.append(SyncStep(label="Portfolio data", ok=True))
    except requests.exceptions.RequestException:
        # Not str(error): a request-level failure's own message includes the
        # full IBKR request URL, which embeds the token as a query param (see
        # trades.brokers.ibkr.api._send_flex_request) — must never reach the client.
        steps.append(SyncStep(label="Portfolio data", ok=False, error="Could not reach IBKR"))
    except Exception as error:  # noqa: BLE001 — one leg's failure must never abort the rest
        steps.append(SyncStep(label="Portfolio data", ok=False, error=str(error)))

    raw_ledger = main.load_ledger(session)
    benchmark_symbol = dashboard.resolved_benchmark_symbol(config)
    today = datetime.now(tz=UTC).date()
    if raw_ledger.is_empty():
        # Nothing's ever been synced successfully — nothing to backfill
        # held-symbol prices for, but the benchmark/CPI/HYSA legs below are
        # still worth attempting on their own.
        held_symbols: list[str] = []
        first_event = today
    else:
        held_symbols = sorted(set(raw_ledger["symbol"].unique().to_list()) - {config.ledger.cash_symbol})
        first_event = _first_event_date(raw_ledger)
    raw_symbols = sorted({*held_symbols, benchmark_symbol})

    _report_sync_progress("Updating price history", 65.0)
    try:
        prices.update_price_caches(raw_symbols, since=first_event, as_of=today, config=config)
        steps.append(SyncStep(label="Market prices", ok=True))
    except Exception as error:  # noqa: BLE001
        steps.append(SyncStep(label="Market prices", ok=False, error=str(error)))

    _report_sync_progress("Updating benchmark prices", 80.0)
    try:
        prices.update_price_cache(benchmark_symbol, since=first_event, as_of=today, config=config, adjusted=True)
        steps.append(SyncStep(label="Benchmark prices", ok=True))
    except Exception as error:  # noqa: BLE001
        steps.append(SyncStep(label="Benchmark prices", ok=False, error=str(error)))

    _report_sync_progress("Updating CPI index", 90.0)
    try:
        cpi_module.update_cpi_cache(config)
        steps.append(SyncStep(label="Inflation data", ok=True))
    except Exception as error:  # noqa: BLE001
        steps.append(SyncStep(label="Inflation data", ok=False, error=str(error)))

    _report_sync_progress("Updating savings rates", 95.0)
    try:
        hysa_rates_module.update_hysa_rates_cache(config)
        steps.append(SyncStep(label="Savings rates", ok=True))
    except Exception as error:  # noqa: BLE001
        steps.append(SyncStep(label="Savings rates", ok=False, error=str(error)))

    return SyncResult(
        synced_at=_last_synced_iso(),
        new_event_count=sync_result.new_event_count if sync_result else 0,
        total_event_count=sync_result.total_event_count if sync_result else raw_ledger.height,
        symbols_refreshed=raw_symbols,
        steps=steps,
    )


@router.post("/api/sync")
def sync(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> SyncResult:
    """Pull the latest IBKR statement and refresh the price/CPI/HYSA-rate caches.

    Refreshes the raw price cache for every symbol ever held plus the
    benchmark symbol, the adjusted (dividend-reinvested) cache for the
    benchmark symbol only (adjusted prices are for benchmark
    counterfactuals, never for pricing your own positions), the CPI
    cache, and every bank's HYSA rate history. Reports progress to
    `app.state.sync_progress` throughout, readable via `GET
    /api/sync/progress` — the IBKR pull is the one step slow enough that a
    bare spinner isn't good enough feedback.

    Concurrent requests are serialized by a lock to prevent cache and ledger
    corruption from simultaneous writes.

    Returns
    -------
    SyncResult
        `synced_at`, `new_event_count`, `total_event_count`, `symbols_refreshed`,
        and `steps` — each leg's own `label`/`ok`/`error`, since one
        failing (e.g. a bad IBKR token) no longer aborts the rest.
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
