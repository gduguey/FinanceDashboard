# IBKR Portfolio

A personal finance dashboard, built one package at a time. `trades` enriches a broker trade export, syncs trade/position/cash data
from IBKR, fetches and caches daily price history, and compares per-trade
returns against a HYSA benchmark. Future packages (budget, accounting, ...)
will live alongside it and feed the same dashboard.

The same logic is exposed two ways today:

- a **Jupyter notebook** (`notebooks/portfolio.ipynb`) that renders
  Plotly charts inline — good for one-off, exploratory analysis;
- a **local web dashboard** (`src/trades/api.py` + `web/`) — a FastAPI
  backend and a React frontend, good for day-to-day glancing.

Both read the same on-disk cache under `data/` and call the same
`trades.*` modules; neither owns the actual logic (see
`docs/architecture.md`).

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
("Trade History API", exposing Trades + Open Positions + Cash Report) — see
`docs/ibkr_flex_api.md` for exactly how to set that query up and where to
find the token/query ID. Without a `.env`, everything still works against
whatever's already cached in `data/` — you just can't pull anything new.

## Keeping the data fresh

Whichever front end you use, the numbers come from two on-disk caches:

- `data/brokers/ibkr/` — your trade/position/cash history, pulled from
  IBKR's Flex Web Service
- `data/prices/` — daily close prices per symbol, pulled from Yahoo Finance

Both are safe to refresh as often as you like (deduped/idempotent — see
`docs/architecture.md`). Do it either by:

- running `notebooks/ibkr_sync.ipynb` (IBKR only; run
  `prices.update_price_caches` yourself for prices, as `portfolio.ipynb` does), or
- clicking **Sync** in the web dashboard, which does both in one action.

Run this regularly if you're actively trading — IBKR's Flex Query is scoped
to a rolling window on their side, so a sync you skip for too long can leave
a permanent gap (`docs/ibkr_flex_api.md` covers backfilling one if it happens).

## Option A: the notebook

```bash
uv run jupyter lab notebooks/portfolio.ipynb
```

Run it top to bottom. It loads whatever's cached under `data/`, tops up the
price cache for any symbols you hold, and renders the investment-schedule
charts, the trade-level returns table, and the annualized-return-vs-HYSA
curve inline as Plotly figures. Nothing here fetches from IBKR — for that,
run `notebooks/ibkr_sync.ipynb` first (see above).

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
in the page header pulls the latest IBKR trade history and refreshes every
symbol's price cache, then the whole page refreshes with the new numbers.

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
  config.py         every tunable parameter, as fields on frozen config objects
  models.py         pydantic schemas — the only place external data gets validated
  preprocessing.py  map each broker's native trade shape onto the canonical schema
  transactions.py   enrich -> aggregate a canonical-shape trade DataFrame; schedule/pie helpers
  prices.py         Yahoo Finance chart API client + on-disk price cache
  returns.py        total/annualized return, HYSA benchmark, alpha, trend fit
  visualization.py  every Plotly chart, for the notebook
  api.py            every JSON endpoint, for the web dashboard (needs the `api` extra)
  brokers/
    ibkr.py         IBKR Flex Web Service client + local trade/position/cash cache
notebooks/
  portfolio.ipynb   the analysis notebook described above
  ibkr_sync.ipynb   the IBKR-only sync notebook described above
web/                the React frontend for the web dashboard
data/               gitignored — your trade/position/cash/price caches live here
docs/               architecture, IBKR Flex API, price API, and return-math deep-dives
```

`docs/architecture.md` is the deeper read — module map, config conventions,
the canonical-trade-schema pattern, and how the price/IBKR caches are kept
safe to rebuild. Start there if you're adding a new data source or broker.

## Dev

```bash
uv run pytest         # requires the `api` extra installed (see Setup) for tests/test_api.py
uv run ruff check .

cd web && npm run build   # typechecks + production-builds the frontend
```
