"""JSON-over-HTTP view of the dashboard, computed entirely by `dashboard.py`/`ledger.*`.

Every endpoint (across `trades.api.routers.*`) calls `dashboard.py` (which
composes `ledger.*` and `market_data.*`) and serializes the result — no
aggregation happens in the API layer itself, matching the split
documented in docs/trades/architecture.md.

GET endpoints only ever read what's already cached on disk — they never
make a network call, with one exception: `GET /api/v1/trades/symbols/search` is a
live Yahoo Finance lookup for the benchmark picker's search box, which by
its nature needs a live answer rather than a cached one. `POST /sync` is
the endpoint that touches the network for the app's own data as a whole
(an IBKR pull plus a price/CPI/HYSA-rate cache refresh); that's what makes
the frontend's "Sync" button a real, explicit action instead of something
that silently happens on every page load. `POST
/api/v1/trades/symbols/{symbol}/ensure-priced` is the narrow exception to that: it
refreshes a single symbol's price cache on the spot, so picking a new
benchmark takes effect without waiting for a full sync.

This module only wires routers onto `app` (defined in `trades.api.dependencies`,
so every router can import it without a circular import back through this
module) and serves the built frontend — it holds no endpoints itself.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from accounting.api import install_error_handlers as install_accounting_error_handlers
from accounting.api import router as accounting_router
from db.current_user import get_current_user_id
from trades.api.auth import require_clerk_session, resolve_current_user_id
from trades.api.dependencies import app
from trades.api.routers import broker_connections, dashboard, market_data, settings, sync
from trades.api.webhooks import router as webhooks_router

# Every `/api/...` route across both modules requires a valid Clerk session
# (see trades.api.auth) — applied here, at the one place that wires routers
# onto `app`, rather than on each router individually, so a new router can
# never be mounted unprotected by omission.
_authenticated = [Depends(require_clerk_session)]

# The URL prefix this module's own routes live under is declared once, here,
# rather than repeated in every `@router.get(...)` path across
# `trades.api.routers.*` — the same shape `accounting.api.api` uses for its
# own routers. A router file therefore spells only the part of the path that
# is about the resource it serves, and moving the whole module's routes is
# this one string.
#
# `/api/v1/trades` mirrors `/api/v1/accounting` — see that prefix's own note
# in `accounting.api.api` for why the version sits under `/api` and why the
# namespace is per-module rather than resource-first. `/health`, `/docs`,
# `/redoc` and `/openapi.json` below stay unversioned: none of them is part of
# the API contract a client codes against.
_trades_router = APIRouter(prefix="/api/v1/trades")
_trades_router.include_router(dashboard.router)
_trades_router.include_router(settings.router)
_trades_router.include_router(broker_connections.router)
_trades_router.include_router(market_data.router)
_trades_router.include_router(sync.router)

app.include_router(accounting_router, dependencies=_authenticated)
app.include_router(_trades_router, dependencies=_authenticated)

# Accounting's routes promise a 409 on a stale optimistic-concurrency write,
# and FastAPI hangs exception handlers off the application rather than off an
# `APIRouter` — so mounting the router is only half of mounting the module.
# The handler itself is defined in `accounting.api.api`, beside the routes
# whose contract it is, not here.
install_accounting_error_handlers(app)

# Deliberately unauthenticated — see trades.api.webhooks' own docstring for
# why (Clerk's own servers call this, never a signed-in browser).
app.include_router(webhooks_router)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, bool]:
    """Liveness probe for `deploy/Dockerfile`'s `HEALTHCHECK` and both `docker-compose*.yml`'s own.

    Deliberately unauthenticated and separate from `/docs` below (which
    Docker's healthcheck used to poll, before that route required a Clerk
    session too) — a healthcheck that itself needed a Clerk session would
    always report unhealthy.

    Returns
    -------
    dict[str, bool]
    """
    return {"ok": True}


# `trades.api.dependencies` disables FastAPI's own auto-registered
# /docs, /redoc, /openapi.json (`docs_url`/`redoc_url`/`openapi_url=None`)
# so these hand-registered equivalents can require the same Clerk session
# as every other route here — the schema/UI would otherwise leak this
# app's endpoint shape (and confirm which routes exist) to anyone, signed
# in or not.
@app.get("/openapi.json", include_in_schema=False, dependencies=_authenticated)
def openapi_schema() -> JSONResponse:
    """Return `app`'s OpenAPI schema. Not the default endpoint's route — see the note above.

    Returns
    -------
    fastapi.responses.JSONResponse
    """
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False, dependencies=_authenticated)
def swagger_ui() -> HTMLResponse:
    """Serve Swagger UI against the protected `/openapi.json` above.

    Returns
    -------
    fastapi.responses.HTMLResponse
    """
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Swagger UI")


@app.get("/redoc", include_in_schema=False, dependencies=_authenticated)
def redoc_ui() -> HTMLResponse:
    """Serve ReDoc against the protected `/openapi.json` above.

    Returns
    -------
    fastapi.responses.HTMLResponse
    """
    return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")


# db.current_user.get_current_user_id's own body always raises (see its
# docstring) — this override is what makes db.session.get_db actually
# resolve the real, Clerk-session-derived user for every request, without
# db or db.session ever importing anything Clerk-specific themselves.
app.dependency_overrides[get_current_user_id] = resolve_current_user_id

# Same layout in the Docker image (built by the frontend-builder stage into
# web/dist/) and in a local dev checkout (built by hand via `npm run build`)
# — both put this file at src/trades/api/api.py, three levels under the repo root.
_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

if _FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="frontend-assets")

    # include_in_schema=False keeps this out of the generated OpenAPI document.
    # It is not part of the API contract, and because the route only exists when
    # web/dist/ happens to be built, including it made the generated schema — and
    # so the checked-in TypeScript client — depend on whether the machine that
    # regenerated it had run `npm run build`. The drift gate could then pass or
    # fail for reasons unrelated to the API.
    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str) -> FileResponse:
        """Serve the built React app for anything no route above matched.

        Registered last on purpose: Starlette matches routes in registration
        order, so every `/api/...` route (and `/docs`, `/openapi.json`)
        defined earlier is tried first. Falls back to `index.html` for any
        path that isn't a real file in `web/dist/` — e.g. a hard refresh on
        `/settings` — so the frontend's client-side router gets a chance to
        handle it instead of a bare 404.

        Returns
        -------
        FileResponse
            The requested static file if it exists under `web/dist/`,
            otherwise `index.html` so client-side routing can take over.
        """
        candidate = (_FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_relative_to(_FRONTEND_DIST) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIST / "index.html")
