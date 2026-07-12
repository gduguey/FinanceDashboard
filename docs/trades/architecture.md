# Architecture

This repo is a personal portfolio dashboard built around one idea: **store
what happened, replay everything else.** The ledger is the only source of
truth; positions, gains, charts, and tax estimates are all derived by
walking that history forward.

## How the pieces fit together

```
┌─────────────────────────────────────────────────────────────────┐
│  Front end                                                      │
│  web/ (React) — notebooks/ (Jupyter + Plotly) currently broken, │
│  see README.md's "Option A"                                     │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│  trades/api/            FastAPI app: auth.py/webhooks.py         │
│                          (Clerk) + routers/ (JSON endpoints)      │
│  trades/dashboard/      aggregates ledger + market data         │
└────────────────────────────┬────────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
┌───────▼───────┐   ┌────────▼────────┐   ┌───────▼────────┐
│  ledger/      │   │  market_data/   │   │  brokers/ibkr/ │
│  replay, lots │   │  prices, CPI,   │   │  Flex API sync │
│  metrics, nav │   │  HYSA rates     │   │  → Postgres    │
│  taxes, cf    │   │  (data/, cache) │   │  ledger_events │
└───────────────┘   └─────────────────┘   └────────────────┘
        │                    │                    │
        └────────────────────┼────────────────────┘
                             │
              Postgres (ledger, per-user settings/secrets)
              + data/ (gitignored — shared price/CPI/HYSA caches only)
```

The web dashboard reads the per-user ledger from Postgres and the shared
price/CPI/HYSA caches from `data/`, both through the same `trades.*`
modules the API calls.

## Module map

```
src/trades/
  config.py           every tunable parameter, as fields on frozen config objects
  models.py           pydantic schemas — canonical column names live here once
  broker_credentials.py  per-user, per-broker credentials, encrypted in Postgres
                       (generic over broker; `brokers/ibkr/credentials.py` is the
                       IBKR-specific adapter over it)
  dashboard/          API-facing aggregation (composes ledger + market_data)
    settings.py       user-editable settings (allocation targets, tax toggles)
    valuation.py      price lookup wiring, daily portfolio values
    overview.py       headline cards (XIRR, TWR, dollar alpha, …)
    charts.py         dollar chart, growth-of-$100, monthly P&L, max drawdown
    holdings.py       lots table, allocation view, data quality
    cash_sitting.py   idle-cash detection + missed-earnings estimate;
                       cash_received_counterfactual for the cash-over-time chart
    tax.py            tax summary, liquidation estimate
  ledger/             pure domain logic — no I/O, no broker awareness
    replay.py         walk the ledger → open/closed lots + cash
    lots.py           FIFO consumption, splits, dividend accrual
    metrics.py        XIRR, lot returns, symbol metrics, max drawdown
    nav.py            NAV per unit, TWR, growth-of-100, period P&L
    counterfactuals.py  HYSA/benchmark/decision replays
    taxes.py          wash sales, annual report, liquidation preview
  market_data/        external reference data (fetch + cache)
    prices.py         Yahoo Finance daily closes
    cpi.py            FRED CPI index
    hysa_rates.py     apyarchives.com HYSA APY history
    symbol_search.py  live Yahoo symbol search (not cached)
  brokers/
    ibkr/             IBKR Flex Web Service → ledger
      api.py          network + XML parsing; save_raw_statement archives
                       every fetch verbatim before anything is parsed
      credentials.py  the IBKR-specific adapter over broker_credentials.py
      models.py       pydantic schemas for IBKR's raw XML shapes
      preprocessing.py  IBKR rows → LedgerEvent
      main.py         sync, rebuild, load/write the Postgres-backed ledger
  api/                the one FastAPI app; auth.py/webhooks.py (Clerk
                       session verification and invite provisioning) plus
                       routers/ (dashboard, market_data, settings, sync);
                       also mounts `accounting.api`'s router (see
                       `/docs/architecture.md` at the repo root)
```

Deep dives by topic:

| Doc | What it covers |
|-----|----------------|
| [ledger.md](ledger.md) | Event types, replay, lots, cashflows |
| [metrics_and_benchmarks.md](metrics_and_benchmarks.md) | XIRR, TWR, NAV, counterfactuals |
| [market_data.md](market_data.md) | Price, CPI, and HYSA data sources |
| [cash_sitting.md](cash_sitting.md) | Idle-cash detection, missed-earnings estimate, cash-over-time chart |
| [ibkr_flex_api.md](ibkr_flex_api.md) | Syncing from Interactive Brokers |
| [glossary.md](glossary.md) | Plain-language definitions of dashboard terms |

## Core conventions

### New data source → canonical schema, always

If you add a broker, API, or file format that overlaps with an existing
concept:

- **Don't let native field names leak past the reader module.** Everything
  downstream (`ledger/`, `dashboard/`, `api/`) stays ignorant of which
  broker or API anything came from.
- **The canonical name is declared once** on the pydantic model in
  `models.py` (e.g. `LedgerEvent`). A model's field names *are* its column
  names — no parallel schema class to keep in sync.
- **Add a `standardize_{source}_...` function** in the broker's
  `preprocessing.py` that maps native shapes onto the canonical schema and
  validates through the matching pydantic model before returning.

See [ledger.md](ledger.md) for the concrete IBKR example.

### Cache raw, derive everything else

When caching fetched external data:

1. Save the **raw response verbatim**, timestamped, never overwritten
   (see `brokers/ibkr/api.py`'s `save_raw_statement`, under
   `raw_statements/`).
2. Treat derived data (the Postgres-backed ledger, price CSVs) as
   **disposable caches** — cheap to delete and regenerate
   (`rebuild_from_raw_statements`).

Atomic writes (temp file + rename) protect against crashes, not logic bugs
that overwrite good data with wrong-but-complete results. IBKR's Flex Query
has a limited retrieval window; a bad overwrite may not be re-fetchable.

### Pure logic vs I/O

| Layer | I/O? | Examples |
|-------|------|----------|
| `ledger/*`, `dashboard/*` | No | replay, metrics, chart series |
| `market_data/*`, `brokers/*` | Yes | fetch, parse, cache, persist to Postgres |
| `api/` | Reads/writes Postgres via the layers above | JSON serialization + Clerk auth |

`dashboard/` composes `ledger.*` and `market_data.*` into the exact shapes
the API serves. `api/` itself does no aggregation.

## Configuration

Every tunable value lives on a frozen config object in `config.py`
(`AppConfig` bundles them all). Functions that need a value take the config
object as a required argument — no bare module-level constants with silent
fallbacks.

A missing input is an error, not a silent fallback: if a price is
unavailable, the function raises rather than dropping the row.

User-editable dashboard settings (target allocation, tax regime, benchmark
override) are per-user, in Postgres — see "Data layout" below.

## Validation boundary

Pydantic models exist only at the edges, where untrusted data enters:

- `LedgerEvent` — one ledger row
- `PriceObservation`, `CpiObservation`, `HysaRateObservation` — market data rows
- `IbkrTrade`, `IbkrCashTransaction` — raw IBKR XML rows

Once past validation, data is a plain typed polars DataFrame for the rest
of the pipeline.

## Data layout

```
data/                     (gitignored) — shared, global caches only; nothing
                           per-user lives here
  prices/
    {SYMBOL}.csv              raw daily close
    {SYMBOL}.adjusted.csv     dividend/split-adjusted close
  cpi/{series_id}.csv         CPI index (re-fetched whole each update)
  hysa_rates/rates.csv        HYSA APY history (re-fetched whole each update)
  brokers/ibkr/
    raw_statements/{timestamp}.xml   every fetch, verbatim, never overwritten —
                                      on R2 (if configured) this same archive is
                                      keyed by `statements/{user_id}/ibkr/...`;
                                      the local-disk fallback used here is not
                                      currently per-user-scoped
```

The ledger itself (every synced IBKR event), broker connections/credentials,
and user-editable dashboard preferences (target allocation, HYSA/benchmark
overrides, tax settings) are all per-user, in Postgres — never under
`data/` (`trades.brokers.ibkr.main.load_ledger`/`_write_ledger`,
`trades.broker_credentials`, `trades.dashboard.settings.load_settings`/
`save_settings`).

`.env` and everything under `data/` are gitignored.

## Guidance for AI assistants

`AGENTS.md` (`CLAUDE.md` is a symlink) carries standing conventions.
Read it before adding a new broker, external API, or cached data source.
