# Market data

`src/trades/market_data/` fetches and caches reference data the ledger
replay needs but doesn't own: prices for valuing holdings, CPI for
inflation comparison, and HYSA rates for the cash counterfactual.

Nothing here knows about brokers or the ledger. `dashboard/valuation.py`
wires price lookups; `dashboard/charts.py` pulls CPI and HYSA series.

## Overview

| Module | Source | Cached? | Update strategy |
|--------|--------|---------|-----------------|
| `prices.py` | Yahoo Finance chart API | Yes — per symbol | Incremental (missing ranges only) |
| `cpi.py` | FRED CSV export | Yes — one file | Full re-fetch each sync |
| `hysa_rates.py` | apyarchives.com (scraped) | Yes — one file | Full re-fetch each sync |
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
- After the first backfill, a daily sync typically costs one small request
  per symbol.
- Rows are validated through `PriceObservation` before writing.
- Writes are atomic (temp file + rename).
- `price_as_of(df, date)` returns the most recent close on or before the
  target date — never interpolated.

One row per **trading day**, not calendar day.

---

## CPI (`cpi.py`)

The Consumer Price Index from FRED's public CSV export — no API key needed.

Unlike prices, the **whole series is re-fetched** on every
`update_cpi_cache` call. FRED revises seasonal adjustments on
already-published months, so an incremental fetch could miss a revision.
The full series is small (a few hundred KB), so re-fetching is cheap.

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

The whole file is re-fetched and overwritten on each sync (a bank's
published history could be corrected upstream).

### Dashboard usage

The user picks a bank (or sets a fixed rate override) in dashboard settings.
`dashboard/settings.py` builds a `rate_lookup(date) → float` callable that
feeds the HYSA counterfactual in `ledger/counterfactuals.py`.

When the tax toggle is on, the published rate is wrapped through
`ledger/taxes.after_tax_rate_lookup` so HYSA comparisons use an after-tax
rate.

---

## Symbol search (`symbol_search.py`)

Live ticker search against Yahoo Finance's search endpoint. **Not cached**
— it's an on-demand lookup for the web dashboard's benchmark picker.

Returns `{symbol, name, exchange}` dicts in Yahoo's relevance order. Only
used by `GET /api/symbols/search` in `api.py`.

---

## Syncing market data

The web dashboard's **Sync** button (`POST /sync` in `api.py`) refreshes
everything in one action:

1. IBKR Flex Query pull → `ledger.csv`
2. Price caches for all held symbols (+ benchmark)
3. CPI cache (full re-fetch)
4. HYSA rates cache (full re-fetch)

Individual symbol pricing can also be refreshed on demand via
`POST /api/symbols/{symbol}/ensure-priced` (e.g. after picking a new
benchmark).

GET endpoints never fetch from the network — they read whatever is already
cached.
