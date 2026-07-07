# Architecture

This repo is two independent backend packages, one FastAPI process, and
one React frontend. This page is the short version of how those pieces
fit together; each package's own `docs/trades/architecture.md` /
`docs/accounting/architecture.md` covers its internals in depth.

## Two modules, one shared idea

- **`trades`** — a brokerage portfolio dashboard. Syncs trade history from
  IBKR, replays it into positions and gains, compares performance against
  benchmarks and counterfactuals.
- **`accounting`** — a day-to-day cash-accounts tracker. Imports bank/card
  CSVs and statement PDFs, categorizes them, rolls them up into net worth,
  budgets, and goals.

Both are built around the same idea — **store what happened, replay
everything else** — but they don't share code, models, or a database.
Each owns its own ledger, its own schema, its own `data/` subfolder. They
could be deleted independently without breaking the other's tests.

## One FastAPI app, not two

There is exactly one `FastAPI()` instance in this repo, created in
`trades/api.py`:

```python
# src/trades/api.py
app = FastAPI(title="Investments API")
...
app.include_router(accounting_router)   # from accounting.api
```

`accounting/api.py` never constructs its own `FastAPI()` — it only
defines `router = APIRouter(prefix="/api/accounting")`, an ordinary
FastAPI router with no opinion about which app it ends up mounted on.
`trades.api` imports that router and mounts it onto its own `app` at
import time. The result, at runtime, is one process, one port, one
`uvicorn trades.api:app` — every `/api/...` and `/api/accounting/...`
route is served by the same app, the same event loop, the same process.

This isn't an accident of convenience; it's deliberate insurance.
Because `accounting.api` never reaches into `trades.api`'s own app object
(it keeps its own module-level `_State`, not `app.state`), it stays
importable and testable with zero dependency on `trades` ever having
been set up — `accounting`'s own test suite never imports `trades.api` at
all. If a reason ever came up to run `accounting` as a genuinely separate
process (`uvicorn accounting.api:app`), that would work too — but nobody
should read the current setup as "two services that happen to talk to
each other." It's one service, and the separability is a safety property,
not a deployment plan.

The React frontend (`web/`) matches this: one dev server, one build, one
set of routes, talking to that one API. There's no frontend-level notion
of "the accounting app" versus "the trades app" — a page just calls
whichever `/api/...` path it needs.

## The one coupling between the modules

`accounting` is allowed to read from `trades`; `trades` never reads from
`accounting`. Concretely, this is exactly one function
(`accounting.api._external_investment_values_usd`) doing exactly one
thing: reading the *running* `trades.api` app's own `app.state.config`
and replaying its ledger to answer "what is the tracked portfolio worth
as of this date" — used only for an `Account` of `kind="external_investment"`
whose `external_ref` field is set to `"trades"` (a choice made once, when
that account is created — see the in-app Guide's Investments tab). An
`external_investment` account left as `external_ref=None` is tracked
manually instead, exactly like any other account, and never touches
`trades` at all.

Everywhere else, the two modules are strangers on purpose. If `trades`
were deleted entirely, `accounting` would still run, still pass its own
tests, and still be fully usable — any `external_investment` account
would just fall back to being valued from its own postings, the same as
a manually-tracked one already is.

## Where to look next

| Question | Doc |
|---|---|
| How `trades` is organized internally | [trades/architecture.md](trades/architecture.md) |
| How `accounting` is organized internally | [accounting/architecture.md](accounting/architecture.md) |
| Plain-language walkthrough for someone using the app | in-app Guide (`web/src/pages/GuidePage.tsx`) |
