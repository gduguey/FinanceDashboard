# Finance Dashboard

A personal finance app built around one idea: store what happened, replay
everything else. Two independent modules share the same backend/database:

- **`trades`** — syncs brokerage account history (IBKR today;
  `trades.broker_credentials` is generic over broker, so a second one
  later reuses the same storage/UI), replays an append-only ledger into
  positions and gains, and compares performance against benchmarks and
  counterfactuals.
- **`accounting`** — tracks day-to-day cash accounts (checking, savings,
  credit cards), imported from bank exports and statement PDFs,
  categorized, and rolled up into net worth, an income statement, budgets,
  and goals.

Both are exposed through the same FastAPI backend and React frontend, and
both persist to the same Postgres database (see
[docs/architecture.md](docs/architecture.md) for how the two modules and
the one API app fit together) — but neither depends on the other, and
`accounting` works fully without ever connecting a broker.

- **Jupyter notebooks** (`notebooks/`) — good for one-off, exploratory
  analysis.
- **Web dashboard** (`src/trades/api/` + `src/accounting/api/` + `web/`)
  — a FastAPI backend and React frontend, good for day-to-day glancing,
  and the only place broker credentials are entered.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages the Python install and
  virtualenv — you don't need Python or pip set up yourself first)
- [Node.js](https://nodejs.org/) 20+ and npm — only needed for the web
  dashboard, not the notebooks
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) —
  the easiest way to get a Postgres database running locally (see
  "Setting up Postgres" below). Everything in this app — the trade/cash
  ledger, every `accounting` table — is stored in Postgres.
- A broker's API credentials (IBKR's Flex Web Service today) — entered
  through the web dashboard's Settings page once it's running, stored
  encrypted in Postgres. See "Setting up Postgres" first.

## Setup

Clone the repo, then install the Python side:

```bash
uv sync --extra api
```

This installs the runtime deps, the dev tools (pytest, ruff, mypy,
jupyter), and FastAPI/uvicorn for the web dashboard's API server, all in
one go. `uv sync --no-dev` additionally skips the dev tools, for a
runtime-only install.

### Setting up Postgres

Follow these steps in order, from the repo root.

**1. Start Postgres.** This downloads and starts a Postgres database
running in the background on your machine, listening on `localhost:5433`:

```bash
docker run -d \
  --name finance-postgres-dev \
  -e POSTGRES_USER=finance \
  -e POSTGRES_PASSWORD=changeme123 \
  -e POSTGRES_DB=finance_dev \
  -p 5433:5432 \
  -v financedashboard_pgdata:/var/lib/postgresql/data \
  postgres:16-alpine
```

`5433` on the host, not Postgres' own default `5432`, so the container
can't collide with a Postgres already installed natively on your machine
— `5432` is still the port *inside* the container, hence the `5433:5432`
mapping. Every URL below therefore uses `:5433`. Replace `changeme123`
with any password you like — just reuse the exact same one in the steps
below. (If this is the first time you're running it, Docker downloads the
Postgres image first, which can take a minute.)

**2. Create the test database.** The test suite never touches
`finance_dev`; it gets its own database on the same container:

```bash
docker exec finance-postgres-dev createdb -U finance finance_test
```

**3. Create a `.env` file** in the repo root (a plain text file — plenty
of text editors, or `nano .env` from a terminal) with this line, using the
same password you picked in step 1:

```
DATABASE_URL=postgresql+psycopg://finance:changeme123@localhost:5433/finance_dev
```

**4. Add a second line to that same `.env` file** — a second, more
restricted database user the running app actually connects as day to day
(this user doesn't exist yet; step 7 below creates it automatically).
Pick any password for it, different from step 1's:

```
DATABASE_URL_APP=postgresql+psycopg://app_runtime:another-password-here@localhost:5433/finance_dev
```

**5. Add a third line pointing at the test database** from step 2 — same
container and same user as `DATABASE_URL`, different database. `uv run
pytest` reads this variable and only this one; without it, 21 test files
fail immediately with a pydantic `ValidationError` (`TestDatabaseSettings`
in [src/db/settings.py](src/db/settings.py) deliberately has no fallback
to `DATABASE_URL`, so a missing value can never silently point the suite
at your dev data):

```
DATABASE_URL_TEST=postgresql+psycopg://finance:changeme123@localhost:5433/finance_test
```

**6. Generate an encryption key** (encrypts broker/API credentials before
they're stored) and add it as a fourth line in `.env`:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy what that command prints, and add it to `.env` as:

```
APP_SECRETS_ENCRYPTION_KEY=paste-what-the-command-printed-here
```

**7. Build the database schema:**

```bash
uv run alembic upgrade head
```

This creates every table the app needs in `finance_dev`, plus the
restricted `app_runtime` database user from step 4. `finance_test` is not
migrated and doesn't need to be — pytest builds its schema from the
SQLAlchemy models itself at the start of each run (see "Dev" below).

At the end of this, `.env` has four lines (`DATABASE_URL`/
`DATABASE_URL_APP`/`DATABASE_URL_TEST`/`APP_SECRETS_ENCRYPTION_KEY`) and a
Postgres container is running with both databases — everything either
option below needs. See [src/db/README.md](src/db/README.md) for more
detail on what each of these values actually does.

Once the app is running (Option A or B below), add a broker connection
from the web dashboard's Settings page — see
[docs/trades/ibkr_flex_api.md](docs/trades/ibkr_flex_api.md) for exactly
where to find IBKR's token/query ID.

### Archiving raw statements to S3-compatible storage (optional)

Every raw broker/bank statement ever imported gets archived verbatim
(never overwritten) before anything derives data from it. By default —
with no further setup — this is written to local disk, under `data/`.

Optionally, set these five in `.env`/`.env.docker` to archive to an
S3-compatible bucket instead:

```
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET_NAME=...
R2_ENDPOINT_URL=...
```

These are named `R2_*` because this project's own deployment uses
Cloudflare R2, but the code underneath just talks plain S3 — point
`R2_ENDPOINT_URL` at any S3-compatible provider (AWS S3 itself, MinIO,
Backblaze B2, ...) with that provider's own access key/secret, and it
works the same way; nothing about the code is Cloudflare-specific. If any
of the five are left unset, archiving falls back to local disk under
`data/` automatically — no error, no extra step required.

## Keeping the data fresh

- **Trade/cash ledger** — lives in Postgres, per user, refreshed by
  clicking **Sync** in the web dashboard, which pulls that user's latest
  IBKR history. This is the only manually-triggered piece.
- `data/trades/prices/` — daily close prices per symbol, pulled from
  Yahoo Finance, cached on disk. Shared across every user, refreshed by a
  standalone cron job (`trades.market_data.price_sync`) several times a
  day — not by clicking Sync.
- `data/trades/cpi/` — CPI index from FRED, cached on disk. Shared,
  refreshed daily by a standalone cron job (`trades.market_data.daily_sync`).
- `data/trades/hysa_rates/` — HYSA APY history from apyarchives.com,
  cached on disk. Shared, refreshed by that same daily cron job.

Clicking **Sync** in the web dashboard only pulls that one signed-in
user's IBKR history — the three cache refreshes above run on their own
schedule regardless of whether anyone clicks Sync. Run Sync regularly if
you're actively trading — IBKR's Flex Query is scoped to a rolling window
on their side, so a sync you skip for too long can leave a permanent gap
([docs/trades/ibkr_flex_api.md](docs/trades/ibkr_flex_api.md) covers
backfilling one if it happens).

## Option A: the notebooks

```bash
uv run jupyter lab
```

**Currently broken.** `portfolio.ipynb`/`prices_sync.ipynb` still call
`trades.brokers.ibkr.main.load_ledger(config)`, a single-argument,
config-based signature from before this app's Postgres/multi-user
migration — `load_ledger` now takes `(session, user_id)` instead. These
notebooks need updating to open a session and pass a real user id before
Option A is usable again; until then, use Option B.

## Option B: the web dashboard

Two processes, run in separate terminals from the repo root:

```bash
# Terminal 1 — API server (http://localhost:8000)
uv run uvicorn trades.api:app --reload --port 8000

# Terminal 2 — frontend (http://localhost:5173)
cd web
npm install    # first time only
npm run dev
```

Open **http://localhost:5173**. The dev server proxies `/api/*` to the
FastAPI server on :8000, so no CORS setup is needed. Every route is
gated behind Clerk sign-in (`CLERK_PUBLISHABLE_KEY`/`CLERK_SECRET_KEY` —
see [docs/architecture.md](docs/architecture.md)); sign-up is invite-only,
so the first account has to be created directly in the Clerk Dashboard. Once signed in, the **Settings** page is where you
add/verify/delete your own broker credentials; the **Sync** button in the
page header pulls your latest IBKR history into Postgres, then the whole
page refreshes with the new numbers. Price/CPI/HYSA-rate caches are
shared across every user and refresh on their own cron schedule, not from
this button — see "Keeping the data fresh" above.

To kill a running API server:

```bash
# Find PID of the running API
lsof -i :8000
# Kill it
kill <PID>
```

## Deploying

Three scripts under `deploy/` drive the production/staging VM, alongside
the `Dockerfile` and `docker-compose*.yml`/`Caddyfile*` they use; all of
it is committed (they contain no credentials — real secrets live in the
gitignored `.env.docker`/`.env.staging` files they reference, still kept
at the repo root):

- **`deploy/deploy.sh <ssh-host>`** — deploys `main` to production:
  `git pull`, rebuild, `docker compose -f deploy/docker-compose.yml up -d
  --build` (which itself runs `alembic upgrade head` before starting the
  app — see `deploy/Dockerfile`).
- **`deploy/deploy-staging.sh <ssh-host> [branch]`** — same idea, but to a
  separate staging stack/clone on the same VM, for a branch that isn't
  `main` yet.
- **`deploy/reset-staging.sh <ssh-host>`** — wipes staging's
  database/volumes back to empty and rebuilds; never touches production.

## Repo layout

```
src/trades/
  config.py           every tunable parameter, as fields on frozen config objects
  models.py           pydantic schemas — canonical column names live here once
  api/                the one FastAPI app; auth.py/webhooks.py (Clerk session
                      verification and invite provisioning) plus routers/
                      (dashboard, market_data, settings, sync)
  dashboard/           API-facing aggregation (composes ledger + market_data)
  ledger/              replay, lots, metrics, NAV, counterfactuals, taxes
  market_data/         prices, CPI, HYSA rates, symbol search
  brokers/ibkr/        IBKR Flex Web Service -> ledger
  broker_credentials.py  per-user, per-broker credentials, encrypted in Postgres
  db/                  SQLAlchemy models/queries for the `trades` schema
src/accounting/
  config.py           accounting-specific tunables (store/ledger/overrides paths)
  models.py           pydantic schemas — Account, Posting, Category, TransferRule, …
  api/                 FastAPI app + routers, mounted onto the same app as trades
  dashboard/           net worth and income-statement aggregation
  ledger/              replay, categorization, currency conversion, transfers
  importers/           bank CSV/PDF -> canonical postings (Chase, SoFi, canonical/ fallback)
  db/                  SQLAlchemy models/queries for the `accounting` schema
src/db/               shared Postgres layer: connection/session, users/secrets,
                      Row-Level Security, encryption, backups — see src/db/README.md
src/migration/        Alembic migrations (schema, RLS policies, role setup)
notebooks/
  trades/
    portfolio.ipynb     analysis notebook (assumes syncing already done)
    ibkr_sync.ipynb     sync IBKR trade/cash history
    prices_sync.ipynb   sync Yahoo Finance price caches
    cpi_sync.ipynb      sync FRED CPI series
    hysa_sync.ipynb     sync HYSA rate history
  accounting/            (empty for now)
web/                  React frontend
data/                 gitignored — market-data caches, under data/trades/{prices,cpi,hysa_rates}/
docs/                 architecture deep-dives (see below), under docs/trades/ and docs/accounting/
```

## Documentation

| Doc | What it covers |
|-----|----------------|
| [architecture.md](docs/architecture.md) | How `trades`, `accounting`, and `db` fit into the one FastAPI app |
| [trades/architecture.md](docs/trades/architecture.md) | Module map, conventions, data layout |
| [trades/ledger.md](docs/trades/ledger.md) | Event types, replay, lots, cashflows |
| [trades/metrics_and_benchmarks.md](docs/trades/metrics_and_benchmarks.md) | XIRR, TWR, NAV, counterfactuals |
| [trades/market_data.md](docs/trades/market_data.md) | Yahoo prices, FRED CPI, HYSA rates |
| [trades/cash_sitting.md](docs/trades/cash_sitting.md) | Idle-cash detection and suggestions |
| [trades/ibkr_flex_api.md](docs/trades/ibkr_flex_api.md) | Syncing from Interactive Brokers |
| [trades/glossary.md](docs/trades/glossary.md) | Plain-language definitions of dashboard terms |
| [accounting/architecture.md](docs/accounting/architecture.md) | Module map, canonical ledger schema, core conventions |
| [accounting/categorization.md](docs/accounting/categorization.md) | Categories, tags, rules vs. category patterns vs. AI suggestions, splitting, transfer/duplicate detection |
| [accounting/planning.md](docs/accounting/planning.md) | Budgets and goals, including recurring/withdrawal automations |
| [accounting/currency-handling.md](docs/accounting/currency-handling.md) | Multi-currency conversion, adding a new supported currency |
| [accounting/adding-accounts.md](docs/accounting/adding-accounts.md) | Teaching the app a new bank's export format |
| [accounting/canonical-csv-import.md](docs/accounting/canonical-csv-import.md) | The no-code fallback CSV importer for a bank with no dedicated standardizer |
| [db/README.md](src/db/README.md) | The shared Postgres layer: roles, RLS, encryption, backups |

Start with [architecture.md](docs/architecture.md), then
[trades/architecture.md](docs/trades/architecture.md) or
[accounting/architecture.md](docs/accounting/architecture.md) if you're
adding a new data source or broker.

## Dev

### The test database

`uv run pytest` runs against `finance_test` (step 2 of "Setting up
Postgres"), never `finance_dev`. `tests/conftest.py` enforces that from
both ends: the engine comes from `DATABASE_URL_TEST` alone, and
`DATABASE_URL`/`DATABASE_URL_APP` are stripped from the environment *and*
from the settings classes' `.env` fallback for the whole run, so a test
that forgets to override the `get_db` dependency fails loudly instead of
mutating dev data.

The schema is rebuilt from the SQLAlchemy models at the start of every
session — the `trades` and `accounting` schemas are dropped and recreated
— and each individual test runs inside a transaction that is rolled back
afterwards. So `finance_test` needs to exist, but nothing in it needs to
be preserved; drop and recreate it any time. Alembic is exercised
separately by `tests/db/test_rls_coverage.py`, which provisions and drops
its own scratch database (this needs the `finance` user's CREATEDB
privilege, which the container's superuser has); that module skips itself
if `DATABASE_URL_TEST` is unset, everything else just fails.

### The gates

All of these run in CI on every PR, and all of them have to pass. Run
them from the repo root:

```bash
# Backend — .github/workflows/backend.yml
uv run ruff check .
uv run ruff format --check .      # `uv run ruff format .` to actually fix it
uv run mypy                       # trades, accounting, and db are fully typed and mypy-clean (see pyproject.toml)
uv run pytest                     # needs DATABASE_URL_TEST, and the `api` extra (see Setup) for tests/*/api/

# Frontend — .github/workflows/frontend.yml
cd web && npm run lint && npm run format:check && npm run check && npm run typecheck && npm run test && npm run build
```

`npm run check` is biome's import-organization assist, separate from
`lint`; `npm run format` (no `:check`) rewrites files instead of just
reporting.

Third gate: `web/src/types/schema.ts` is generated from the backend's
OpenAPI schema and never hand-edited, so CI fails on any drift between
them (`.github/workflows/openapi-types.yml`). After changing any FastAPI
route or response model, regenerate and commit the result:

```bash
uv run python -m trades.api.export_openapi        # writes web/openapi.json
cd web && npm run generate:schema && npx biome format --write src/types/schema.ts
git diff --exit-code -- web/src/types/schema.ts   # what CI asserts
```
