"""JSON-over-HTTP view of the accounting module: this file wires up the sub-routers, `routers.*` serialize.

Mounted as a router onto the same FastAPI app `trades.api` already runs
(see that module's own `app.include_router` call), so the web dashboard's
one dev server serves both. `state` (see `accounting.api.dependencies`)
lives off the shared `app` object, so accounting stays importable — and
testable — without ever importing `trades.api` itself, keeping the
one-directional coupling (`accounting` reads `trades`, never the reverse)
intact at the API layer too.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from accounting.api.dependencies import _stash_expected_store_version
from accounting.api.routers import dashboard, exchange_rates, goals, imports, llm, postings, store

# `_stash_expected_store_version` runs for every accounting endpoint so the
# `X-Expected-Store-Version` header stays a declared, documented part of the
# API. Nothing reads the stashed value back any more — the whole-store version
# check went with `accounting.store.save_store` — so the header is accepted
# and ignored; see `accounting.store.get_store_version`.
router: APIRouter = APIRouter(prefix="/api/accounting", dependencies=[Depends(_stash_expected_store_version)])
router.include_router(store.router)
router.include_router(exchange_rates.router)
router.include_router(imports.router)
router.include_router(postings.router)
router.include_router(llm.router)
router.include_router(dashboard.router)
router.include_router(goals.router)
