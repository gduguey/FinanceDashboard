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

from fastapi import APIRouter

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

router: APIRouter = APIRouter(prefix="/api/accounting")
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
