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
app.include_router(accounting_router)  # from accounting.api
```

`accounting/api.py` never constructs its own `FastAPI()` — it only
defines `router = APIRouter(prefix="/api/v1/accounting")`, an ordinary
FastAPI router with no opinion about which app it ends up mounted on.
`trades.api` imports that router and mounts it onto its own `app` at
import time. The result, at runtime, is one process, one port, one
`uvicorn trades.api:app` — every `/api/...` and `/api/v1/accounting/...`
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
`accounting`. Concretely, this is exactly two functions in
`accounting/api/routers/dashboard.py` —
`_external_investment_values_usd` and `_benchmark_apy_pct` — doing exactly
two things: reading the *running* `trades.api` app's own `app.state.config`
to (1) replay its ledger and answer "what is the tracked portfolio worth
as of this date" (used only for an `Account` of `kind="external_investment"`
whose `broker_connection_id` names one of the user's
`trades.broker_connections` rows — a choice made once, when that account is
created, see the in-app Guide's Investments tab), and (2) look up its
published HYSA benchmark rate for the interest-summary view. An
`external_investment` account left with `broker_connection_id=None` is
tracked manually instead, exactly like any other account, and never touches
`trades` at all.

That link is the one place the two schemas touch, and it is a real foreign
key rather than the string `external_ref = "trades"` it replaces (DB-audit
move #1): a brokerage account naming a connection that was never created,
or that has since been deleted, is not a state the database can hold.
`ON DELETE SET NULL` means removing the connection degrades the account to
a manually-valued one rather than stranding it. The price is that
`accounting`'s DDL now names `trades.broker_connections` — the schemas are
no longer creatable in isolation, even though the Python packages still
are: `accounting` imports nothing from `trades`, and the one bit it reads
across the seam (`repositories.accounts.broker_connection_exists`) goes
through the qualified table name, not an import.

Everywhere else, the two modules are strangers on purpose.

## Local (function-level) imports: when they're justified

`ruff`'s `PLC0415` (`import-outside-top-level`) is enabled repo-wide via
`select = ["ALL"]` in `pyproject.toml`, with no per-file exemption — every
`import`/`from` statement that isn't at module scope has to carry its own
`# noqa: PLC0415`, and each one is expected to have a real reason.
"Slightly faster" is explicitly not one: Python caches every module in
`sys.modules` after its first import, so a second `import` of the same
module — top-of-file or inside a function, doesn't matter — is a cache
hit either way. For a long-lived server process (this app's actual shape:
one Docker container staying up, not a CLI tool or serverless function
restarting per invocation), moving an import into a function doesn't
avoid the one-time cost of that first import, it only defers *when* it's
paid — and if the function runs on most requests anyway, that's not a
real saving. As of this writing there are exactly 11 local imports in the
whole repo (`src/` and `tests/` combined, verified via
`ruff check . --select PLC0415 --ignore-noqa`, which bypasses every
`# noqa` to catch anything that might otherwise hide from a plain
`ruff check`), and each falls into one of two legitimate categories:

- **Enforcing the module boundary above** — the six `from trades import
  ...` lines inside `_external_investment_values_usd`/`_benchmark_apy_pct`.
  A top-level import here would make `accounting.api.routers.dashboard`
  (and therefore all of `accounting.api`, since every router is imported
  at app-construction time) hard-fail if `trades` isn't installed —
  exactly the dependency this doc's whole point is that `accounting`
  shouldn't have.
- **Making a third-party SDK gracefully optional** — the four imports in
  `accounting/llm/gemini.py`/`mistral.py` (`from google import genai`,
  `from mistralai.client import Mistral`), each inside a
  `try: ... except ImportError: raise LLMProviderError(...)`. This is a
  structural requirement, not a preference: a top-level import can't be
  caught the same way, since a missing package would raise at *module*
  import time (crashing the whole app at startup) rather than only when
  that specific provider is actually used.

A local import with neither reason — e.g. one existed for
`accounting.importers.paystub.extract_paystub_pdf_text`'s `import
pdfplumber`, with no error handling and no boundary to enforce — gets
moved to the top of its file. The bar for a new one: if the reason isn't
"this specific module must not become a hard, unconditional dependency"
or "this specific `ImportError` needs to become a different, catchable
exception," it belongs at the top.

## Where to look next

| Question | Doc |
|---|---|
| How `trades` is organized internally | [trades/architecture.md](trades/architecture.md) |
| How `accounting` is organized internally | [accounting/architecture.md](accounting/architecture.md) |
| Plain-language walkthrough for someone using the app | in-app Guide (`web/src/pages/GuidePage.tsx`) |
