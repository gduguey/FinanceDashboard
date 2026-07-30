"""JSON-over-HTTP view of the accounting module: this file wires up the sub-routers, `routers.*` serialize.

Mounted as a router onto the same FastAPI app `trades.api` already runs
(see that module's own `app.include_router` call), so the web dashboard's
one dev server serves both. Mounting the module is two calls, not one:
`router` carries the routes, and `install_error_handlers` registers the
exception handlers their status-code contract depends on — FastAPI hangs
those off the application, not off an `APIRouter`.

`state` (see `accounting.api.dependencies`)
lives off the shared `app` object, so accounting stays importable — and
testable — without ever importing `trades.api` itself, keeping the
one-directional coupling (`accounting` reads `trades`, never the reverse)
intact at the API layer too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from accounting.api.routers import (
    accounts,
    bootstrap,
    budgets,
    categories,
    category_patterns,
    dashboard,
    exchange_rates,
    goals,
    imports,
    llm,
    other_assets,
    postings,
    simulator_scenarios,
    tags,
    transfer_rules,
)
from db.base import VersionConflictError

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.responses import Response

# `/api/v1/accounting`, not `/v1/accounting`: `/api` is the boundary that keeps
# a versioned API path from being swallowed by the SPA catch-all route
# (`trades.api.api`'s `/{full_path:path}`), which serves `index.html` for
# anything not matched earlier. `/v1/...` would look like a frontend route.
# The namespace is per-module rather than resource-first because `/settings`,
# `/ledger/export` and `/statements/export` all exist in both this module and
# `trades` — three real collisions a flat resource namespace could not hold.
router: APIRouter = APIRouter(prefix="/api/v1/accounting")
router.include_router(bootstrap.router)
router.include_router(categories.router)
router.include_router(tags.router)
router.include_router(transfer_rules.router)
router.include_router(category_patterns.router)
router.include_router(other_assets.router)
router.include_router(budgets.router)
router.include_router(simulator_scenarios.router)
router.include_router(accounts.router)
router.include_router(exchange_rates.router)
router.include_router(imports.router)
router.include_router(postings.router)
router.include_router(llm.router)
router.include_router(dashboard.router)
router.include_router(goals.router)


def _handle_version_conflict(_request: Request, exc: Exception) -> Response:
    """Translate a rejected optimistic-concurrency write into HTTP 409.

    One handler, not one per write path — every row-versioned accounting
    update (`PATCH /goals/{id}`, `PATCH /transfer-rules/{id}`, `PATCH
    /category-patterns/{id}`, via `db.base.check_and_bump_row_version`)
    raises this from deep inside a plain persistence function (no FastAPI
    import in any of them, deliberately), so translating it happens once
    rather than at each of those call sites.

    `exc` is annotated `Exception`, not `VersionConflictError`, because that
    is the signature Starlette's handler registry accepts — it dispatches
    here only for the class this is registered against, so the narrower type
    would be accurate but unassignable.

    Returns
    -------
    starlette.responses.Response
        A 409 whose body is the conflict message the persistence layer wrote.
    """
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def install_error_handlers(app: FastAPI) -> None:
    """Register the exception handlers `router`'s status-code contract depends on.

    Separate from `router` because FastAPI hangs exception handlers off the
    application, not off an `APIRouter` — so mounting `router` alone gives
    a 500 where the contract promises a 409. Every 409 this module
    documents comes from here, which is why this lives beside the router it
    belongs to rather than in whichever app happens to mount it: `trades.api`
    mounts accounting today, but accounting's own contract does not depend on
    that fact.

    Parameters
    ----------
    app
        The application `router` is being mounted onto.
    """
    app.add_exception_handler(VersionConflictError, _handle_version_conflict)
