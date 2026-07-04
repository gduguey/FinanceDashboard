# IBKR Portfolio

A personal portfolio dashboard built around one idea: store what happened,
replay everything else. The `trades` package syncs account history from
IBKR, caches market reference data (prices, CPI, HYSA rates), replays an
append-only ledger into positions and gains, and compares performance
against benchmarks and counterfactuals.

The same logic is exposed two ways today:

- **Jupyter notebooks** (`notebooks/`) — sync and analysis notebooks;
  good for one-off, exploratory analysis;
- a **local web dashboard** (`src/trades/api.py` + `web/`) — a FastAPI
  backend and a React frontend, good for day-to-day glancing.

Both read the same on-disk cache under `data/` and call the same
`trades.*` modules; neither owns the actual logic (see
[docs/architecture.md](docs/architecture.md)).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages the Python install and
  virtualenv — you don't need Python or pip set up yourself first)
- [Node.js](https://nodejs.org/) 20+ and npm — only needed for the web
  dashboard, not the notebook
- IBKR Flex Web Service credentials — only needed to *sync*; skip this if
  you're just poking around with whatever's already in `data/`

## Setup

Clone the repo, then install the Python side:

```bash
uv sync --extra api
```

This installs the runtime deps, the dev tools (pytest, ruff, jupyter), and
FastAPI/uvicorn for the web dashboard's API server, all in one go. If you
only ever intend to use the notebook (Option A below) you can drop
`--extra api`, but then skip Option B entirely — without it, `trades.api`
can't import and `tests/test_api.py` will fail to collect. `uv sync --no-dev`
additionally skips the dev tools, for a runtime-only install.

If you'll be syncing from IBKR, create a `.env` in the repo root (gitignored):

```
IBKR_FLEX_WEB_SERVICE_TOKEN=...
IBKR_QUERY_ID=...
```

These come from a Flex Query you configure in IBKR's Account Management UI
("Trade History API", exposing Trades + Cash Transactions) — see
[docs/ibkr_flex_api.md](docs/ibkr_flex_api.md) for exactly how to set that
query up and where to find the token/query ID. Without a `.env`, everything
still works against whatever's already cached in `data/` — you just can't
pull anything new.

## Keeping the data fresh

Whichever front end you use, the numbers come from on-disk caches:

- `data/brokers/ibkr/` — your trade/cash history, pulled from IBKR's Flex
  Web Service
- `data/prices/` — daily close prices per symbol, pulled from Yahoo Finance
- `data/cpi/` — CPI index from FRED
- `data/hysa_rates/` — HYSA APY history from apyarchives.com

All are safe to refresh as often as you like (deduped/idempotent — see
[docs/architecture.md](docs/architecture.md)). Do it either by:

- running the individual sync notebooks (`ibkr_sync.ipynb`,
  `prices_sync.ipynb`, `cpi_sync.ipynb`, `hysa_sync.ipynb`), or
- clicking **Sync** in the web dashboard, which refreshes all four in one
  action.

Run this regularly if you're actively trading — IBKR's Flex Query is scoped
to a rolling window on their side, so a sync you skip for too long can leave
a permanent gap ([docs/ibkr_flex_api.md](docs/ibkr_flex_api.md) covers
backfilling one if it happens).

## Option A: the notebooks

```bash
uv run jupyter lab
```

Run the sync notebooks first (`ibkr_sync.ipynb`, `prices_sync.ipynb`,
`cpi_sync.ipynb`, `hysa_sync.ipynb`), then open `portfolio.ipynb` for
analysis. Each sync notebook is independent — run only the ones whose
caches are stale.

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
FastAPI server on :8000, so no CORS setup is needed. The one **Sync** button
in the page header pulls the latest IBKR history and refreshes every
symbol's price cache plus CPI and HYSA rates, then the whole page refreshes
with the new numbers.

Everything you see reads from the same local caches as the notebook — the
API layer never fetches anything on its own except when you click Sync
(see `src/trades/api.py`'s docstring for why that split matters).

To kill a running API server:

```bash
# Find PID of the running API
lsof -i :8000
# Kill it
kill <PID>
```

## Repo layout

```
src/trades/
  config.py           every tunable parameter, as fields on frozen config objects
  models.py           pydantic schemas — canonical column names live here once
  dashboard/          API-facing aggregation (composes ledger + market_data)
  ledger/             replay, lots, metrics, NAV, counterfactuals, taxes
  market_data/        prices, CPI, HYSA rates, symbol search
  brokers/ibkr/       IBKR Flex Web Service → ledger
  api.py              JSON endpoints for the web dashboard (needs `api` extra)
  visualization.py    Plotly charts for the notebook
notebooks/
  portfolio.ipynb     analysis notebook (assumes syncing already done)
  ibkr_sync.ipynb     sync IBKR trade/cash history
  prices_sync.ipynb   sync Yahoo Finance price caches
  cpi_sync.ipynb      sync FRED CPI series
  hysa_sync.ipynb     sync HYSA rate history
web/                  React frontend
data/                 gitignored — caches live here
docs/                 architecture deep-dives (see below)
```

## Documentation

| Doc | What it covers |
|-----|----------------|
| [architecture.md](docs/architecture.md) | Module map, conventions, data layout |
| [ledger.md](docs/ledger.md) | Event types, replay, lots, cashflows |
| [metrics_and_benchmarks.md](docs/metrics_and_benchmarks.md) | XIRR, TWR, NAV, counterfactuals |
| [market_data.md](docs/market_data.md) | Yahoo prices, FRED CPI, HYSA rates |
| [ibkr_flex_api.md](docs/ibkr_flex_api.md) | Syncing from Interactive Brokers |
| [glossary.md](docs/glossary.md) | Plain-language definitions of dashboard terms |

Start with [architecture.md](docs/architecture.md) if you're adding a new
data source or broker.

## Dev

```bash
uv run pytest         # requires the `api` extra installed (see Setup) for tests/test_api.py
uv run ruff check .

cd web && npm run build   # typechecks + production-builds the frontend
```
