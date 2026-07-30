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

from http import HTTPStatus
from mimetypes import guess_type
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

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

# Best ratio first: the loop below takes the first variant the client accepts
# *and* the build actually produced, so ordering this tuple is what expresses
# the preference. Only these two exist on disk — see web/build/precompress.ts.
_CONTENT_ENCODINGS: tuple[tuple[str, str], ...] = (("br", ".br"), ("gzip", ".gz"))

# Vite fingerprints every filename under assets/ with a content hash, so a
# given URL's bytes can never change. `immutable` is what stops a browser
# revalidating them on a reload it did not need to.
_IMMUTABLE = "public, max-age=31536000, immutable"


def _acceptable_encodings(header: str | None) -> set[str]:
    """Return the content codings the client is willing to receive.

    Parameters
    ----------
    header:
        Raw `Accept-Encoding` value, or None when the request omitted it.

    Returns
    -------
    set[str]
        Coding names with a non-zero q-value. `q=0` is an explicit refusal,
        not a weak preference, so it must not be treated as acceptance —
        `gzip;q=0` means "anything but gzip".
    """
    if not header:
        return set()
    accepted: set[str] = set()
    for part in header.split(","):
        token, _, params = part.strip().partition(";")
        name = token.strip().lower()
        if not name:
            continue
        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.strip().partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > 0:
            accepted.add(name)
    return accepted


def _identity_content_type(filename: str) -> str:
    """Return the media type of `filename` ignoring any compression suffix.

    A precompressed `index-abc123.js.br` is still JavaScript; the brotli
    framing is transport, declared by `Content-Encoding`. Guessing from the
    variant's own name would answer `application/octet-stream` (or worse,
    something brotli-specific) and the browser would refuse to execute it.

    Returns
    -------
    str
        A `Content-Type` value, with the charset appended for text media
        types exactly as Starlette's own `Response` would have added it.
    """
    media_type = guess_type(filename)[0] or "text/plain"
    # Mirrors starlette.responses.Response.init_headers, which appends the
    # charset for text/* itself — without this the variant would be served
    # with a subtly different Content-Type than the uncompressed file.
    if media_type.startswith("text/"):
        return f"{media_type}; charset=utf-8"
    return media_type


def _negotiated_variant(path: Path, accept_encoding: str | None) -> tuple[Path, str | None]:
    """Pick the on-disk file to serve for `path` and the coding it is in.

    Returns
    -------
    tuple[pathlib.Path, str | None]
        The file to send and its `Content-Encoding`, or (`path`, None) when no
        variant is both acceptable to the client and present in the build.
        A missing variant is never an error — a build that skipped
        precompression serves the uncompressed file to everyone.
    """
    accepted = _acceptable_encodings(accept_encoding)
    for encoding, suffix in _CONTENT_ENCODINGS:
        if encoding not in accepted and "*" not in accepted:
            continue
        candidate = path.with_name(path.name + suffix)
        if candidate.is_file():
            return candidate, encoding
    return path, None


class _PrecompressedStaticFiles(StaticFiles):
    """`StaticFiles` that prefers the `.br`/`.gz` sibling built alongside a file.

    Subclassing rather than replacing it keeps everything Starlette already
    gets right — path-traversal rejection, ETag and `If-None-Match` handling,
    range requests — and adds only the negotiation. Each variant carries its
    own ETag because it is genuinely a different entity; `Vary` is what keeps
    a shared cache from serving one client's brotli to a client that cannot
    decode it.
    """

    def __init__(self, *, directory: Path, cache_control: str) -> None:
        super().__init__(directory=directory)
        self._cache_control = cache_control

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Serve `path`, substituting a precompressed variant when acceptable.

        Returns
        -------
        starlette.responses.Response
        """
        accepted = _acceptable_encodings(Headers(scope=scope).get("accept-encoding"))
        response: Response | None = None
        for encoding, suffix in _CONTENT_ENCODINGS:
            if encoding not in accepted and "*" not in accepted:
                continue
            try:
                candidate = await super().get_response(path + suffix, scope)
            except StarletteHTTPException:
                # No such variant. Try the next coding, then the original.
                continue
            if candidate.status_code >= HTTPStatus.BAD_REQUEST:
                continue
            # Stated here rather than inherited. Starlette derives both
            # headers from `mimetypes`, whose `encodings_map` happens to know
            # `.gz` and `.br` today and would answer `None` for a coding added
            # later — so this handler says which coding it chose rather than
            # depending on a table it does not own.
            #
            # Applied to a 206 as well as a 200 for the same reason: a byte
            # range of a variant is still the variant's bytes, and a client
            # resuming a download needs to be told what they are encoded as.
            if candidate.status_code in {HTTPStatus.OK, HTTPStatus.PARTIAL_CONTENT}:
                candidate.headers["content-encoding"] = encoding
                candidate.headers["content-type"] = _identity_content_type(path)
            # A 304 is passed through untouched — it carries no body, and its
            # ETag is the variant's, which is the right one for the entity
            # actually being served.
            response = candidate
            break
        if response is None:
            response = await super().get_response(path, scope)
        # Set unconditionally: the response varies by Accept-Encoding whether or
        # not *this* request got a variant, because another request for the same
        # URL would get different bytes.
        response.headers["vary"] = "accept-encoding"
        if response.status_code < HTTPStatus.BAD_REQUEST:
            response.headers["cache-control"] = self._cache_control
        return response


if _FRONTEND_DIST.is_dir():
    app.mount(
        "/assets",
        _PrecompressedStaticFiles(directory=_FRONTEND_DIST / "assets", cache_control=_IMMUTABLE),
        name="frontend-assets",
    )

    # include_in_schema=False keeps this out of the generated OpenAPI document.
    # It is not part of the API contract, and because the route only exists when
    # web/dist/ happens to be built, including it made the generated schema — and
    # so the checked-in TypeScript client — depend on whether the machine that
    # regenerated it had run `npm run build`. The drift gate could then pass or
    # fail for reasons unrelated to the API.
    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str, request: Request) -> FileResponse:
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
        target = _FRONTEND_DIST / "index.html"
        # `index.html` names the hashed asset URLs, so it is the one file that
        # must never be cached: a stale copy would point at bundles that the
        # next deploy has already deleted. It is the necessary other half of
        # serving /assets as `immutable`.
        cache_control = "no-cache"
        candidate = (_FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_relative_to(_FRONTEND_DIST) and candidate.is_file():
            target = candidate
            # Files under public/ keep their names across deploys, so they get
            # revalidation rather than the immutable treatment /assets gets.
            cache_control = "public, max-age=3600"
        served, encoding = _negotiated_variant(target, request.headers.get("accept-encoding"))
        headers = {"vary": "accept-encoding", "cache-control": cache_control}
        if encoding is None:
            return FileResponse(served, headers=headers)
        return FileResponse(
            served,
            headers={**headers, "content-encoding": encoding},
            media_type=_identity_content_type(target.name),
        )
