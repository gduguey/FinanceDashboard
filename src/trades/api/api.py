"""JSON-over-HTTP view of the dashboard, computed entirely by `dashboard.py`/`ledger.*`.

Every endpoint (across `trades.api.routers.*`) calls `dashboard.py` (which
composes `ledger.*` and `market_data.*`) and serializes the result — no
aggregation happens in the API layer itself, matching the split
documented in docs/trades/architecture.md.

GET endpoints only ever read what's already cached on disk — they never
make a network call, with one exception: `GET /api/symbols/search` is a
live Yahoo Finance lookup for the benchmark picker's search box, which by
its nature needs a live answer rather than a cached one. `POST /sync` is
the endpoint that touches the network for the app's own data as a whole
(an IBKR pull plus a price/CPI/HYSA-rate cache refresh); that's what makes
the frontend's "Sync" button a real, explicit action instead of something
that silently happens on every page load. `POST
/api/symbols/{symbol}/ensure-priced` is the narrow exception to that: it
refreshes a single symbol's price cache on the spot, so picking a new
benchmark takes effect without waiting for a full sync.

This module only wires routers onto `app` (defined in `trades.api.dependencies`,
so every router can import it without a circular import back through this
module) and serves the built frontend — it holds no endpoints itself.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from accounting.api import router as accounting_router
from trades.api.auth import require_clerk_session
from trades.api.dependencies import app
from trades.api.routers import dashboard, market_data, settings, sync

# Every `/api/...` route across both modules requires a valid Clerk session
# (see trades.api.auth) — applied here, at the one place that wires routers
# onto `app`, rather than on each router individually, so a new router can
# never be mounted unprotected by omission.
_authenticated = [Depends(require_clerk_session)]
app.include_router(accounting_router, dependencies=_authenticated)
app.include_router(dashboard.router, dependencies=_authenticated)
app.include_router(settings.router, dependencies=_authenticated)
app.include_router(market_data.router, dependencies=_authenticated)
app.include_router(sync.router, dependencies=_authenticated)

# Same layout in the Docker image (built by the frontend-builder stage into
# web/dist/) and in a local dev checkout (built by hand via `npm run build`)
# — both put this file at src/trades/api/api.py, three levels under the repo root.
_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

if _FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="frontend-assets")

    @app.get("/{full_path:path}")
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
