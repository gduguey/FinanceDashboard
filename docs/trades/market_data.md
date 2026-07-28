# Market data

`src/trades/market_data/` fetches and caches reference data the ledger
replay needs but doesn't own: prices for valuing holdings, CPI for
inflation comparison, and HYSA rates for the cash counterfactual.

Nothing here knows about brokers or the ledger. `dashboard/valuation.py`
wires price lookups; `dashboard/charts.py` pulls CPI and HYSA series.

## Overview

| Module | Source | Cached? | Update strategy |
|--------|--------|---------|-----------------|
| `prices.py` | Yahoo Finance chart API | Yes — per symbol | Incremental (missing ranges only), a few times a day around market close |
| `cpi.py` | FRED CSV export | Yes — one file | Full re-fetch, daily |
| `hysa_rates.py` | apyarchives.com (scraped) | Yes — one file | Full re-fetch, daily |
| `symbol_search.py` | Yahoo Finance search API | No | Live on each request |

All cached data lives under `data/` (gitignored). See
[architecture.md](architecture.md) for the full layout.

---

## Prices (`prices.py`)

Daily close prices from Yahoo Finance's public chart endpoint — the same
undocumented JSON API that backs finance.yahoo.com. Called directly with
`requests` (no `yfinance` dependency).

There is no API key and no documented rate limit. Fine for a personal
dashboard; not suitable for a paid product without switching to a real
data vendor.

### Two series per symbol

Each symbol gets **two separate cache files**:

| File | Used for |
|------|----------|
| `{SYMBOL}.csv` | Raw daily close — pricing your own positions |
| `{SYMBOL}.adjusted.csv` | Dividend/split-adjusted close — benchmark counterfactuals |

Mixing raw and adjusted silently corrupts every return, so they are
deliberately separate files, not a column flag on one file.

### Request

```
GET https://query2.finance.yahoo.com/v8/finance/chart/{symbol}
    ?period1={unix_seconds_start}&period2={unix_seconds_end}&interval=1d
```

- `period1`/`period2` are Unix seconds (UTC). `period2` is the end date
  plus one day so the end date itself is included.
- A `User-Agent` header is required — the default `requests` user agent
  gets blocked.

We use the `query2` host; `query1` returned `429` consistently in testing.

### Cache mechanics

- `update_price_cache` / `update_adjusted_price_cache` fetch only the
  date range missing from the on-disk cache (`_missing_ranges`).
- After the first backfill, each cron run typically costs one small
  request per symbol (see "Syncing market data" below).
- Rows are validated through `PriceObservation` before writing.
- Writes are atomic (temp file + rename).
- `price_as_of(df, date)` returns the most recent close on or before the
  target date — never interpolated.

One row per **trading day**, not calendar day.

---

## CPI (`cpi.py`)

The Consumer Price Index from FRED's public CSV export — no API key needed.

Unlike prices, the **whole series is re-fetched** on every
`update_cpi_cache` call — run once a day by `trades.market_data.daily_sync`,
not on every price refresh. FRED revises seasonal adjustments on
already-published months, so an incremental fetch could miss a revision.
The full series is small (a few hundred KB), so re-fetching daily is cheap.

### Cache

One file: `data/cpi/{series_id}.csv` with columns `observation_date`,
`value`.

`cpi_as_of(df, date)` mirrors `price_as_of`: rollback to the most recent
observation on or before the target date.

On the dashboard, CPI appears on the growth-of-$100 chart, indexed to 100
at the chart start so it can be compared with portfolio and benchmark
performance.

---

## HYSA rates (`hysa_rates.py`)

Historical high-yield savings account APY rates, scraped from
[apyarchives.com](https://apyarchives.com).

apyarchives.com is a Next.js app. The full rate history for every bank is
embedded in the server-rendered HTML as React Server Components payload
(`self.__next_f.push([1, "..."])` chunks) — no JavaScript execution
needed, just finding and parsing that embedded JSON.

### Cache

One file: `data/hysa_rates/rates.csv` with columns `bank_id`, `bank_name`,
`rate_date`, `apy_pct`.

A rate only gets a new row on the date it **changed**, not one row per day.
Looking up "the rate on day X" means rolling back to the most recent row on
or before X — same pattern as `price_as_of`.

The whole file is re-fetched and overwritten once a day, alongside CPI, by
`trades.market_data.daily_sync` (a bank's published history could be
corrected upstream).

### Dashboard usage

The user picks a bank (or sets a fixed rate override) in dashboard settings.
`dashboard/settings.hysa_rate_lookup` builds a `(date) → float` callable
that feeds the HYSA counterfactual in `ledger/counterfactuals.py`.

When the tax toggle is on, the published rate is wrapped through
`ledger/taxes.after_tax_rate_lookup` so HYSA comparisons use an after-tax
rate.

---

## Symbol search (`symbol_search.py`)

Live ticker search against Yahoo Finance's search endpoint. **Not cached**
— it's an on-demand lookup for the web dashboard's benchmark picker.

Returns `{symbol, name, exchange}` dicts in Yahoo's relevance order. Only
used by `GET /api/symbols/search` in `api/routers/market_data.py`.

---

## Syncing market data

There is no longer a single "Sync everything" action. The web dashboard's
**Sync** button (`POST /api/sync` in `trades/api/routers/sync.py`) now only
pulls that signed-in user's latest IBKR Flex Query statement into their own
Postgres-backed ledger — it's a manual, on-demand, per-user action because
that's the one leg worth watching a progress bar for.

Market data refreshes automatically instead, on cron, with no button:

- **`trades.market_data.price_sync.run_price_sync`** — price caches for
  every held symbol plus the benchmark (raw + adjusted). Run at 20:30,
  21:15, and 22:00 UTC — spanning US markets' 4pm ET close in both
  daylight time (20:00 UTC) and standard time (21:00 UTC), plus a buffer
  for Yahoo to finalize the number — not continuously (checking more
  often than the data actually changes is wasted, and checking *during*
  market hours risks caching a still-moving, not-yet-final price — see
  `_SETTLEMENT_BUFFER_DAYS` in `prices.py`). The raw-close cache is
  incremental (only the missing gap gets fetched, plus a trailing
  buffer window that's always re-checked); the adjusted-close cache is
  fully re-fetched every run instead, since Yahoo retroactively
  recalculates historical adjusted values whenever a symbol pays a new
  dividend or splits, and an incremental fetch would never notice.
- **`trades.market_data.daily_sync.run_daily_market_data_sync`** — the
  CPI cache and every bank's HYSA rate history, at 4:00 UTC (both full
  re-fetches, cheap enough daily, no precise publication time worth
  chasing for either).

Every one of these also backs up its cache file to R2 (or local disk)
after each successful write, and transparently restores from that backup
if the file's ever found corrupted on disk (see `trades.utils.cache_backup`).

Exchange rates (`accounting.market_data.fx_sync`) run separately, at
14:00, 14:45, and 15:30 UTC — see
[currency-handling.md](../accounting/currency-handling.md). All of this
is wired up as `crontab` entries on the deploy VM, outside of anything
`docker compose up` schedules on its own.

Individual symbol pricing can also be refreshed on demand via
`POST /api/symbols/{symbol}/ensure-priced` (e.g. after picking a new
benchmark).

GET endpoints never fetch from the network — they read whatever is already
cached.
