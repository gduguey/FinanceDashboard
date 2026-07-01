# Architecture

## Module map

```
src/trades/
  config.py        every tunable parameter, as fields on frozen config objects
  models.py        pydantic schemas — the only place external data gets validated
  transactions.py  load broker CSV -> enrich -> aggregate; investment schedule/pie helpers
  prices.py        Yahoo Finance chart API client + on-disk price cache
  returns.py       total/annualized return, HYSA benchmark, alpha, trend fit
  visualization.py every plotly chart; takes data, returns a Figure, nothing else
```

`transactions.py` and `returns.py` are pure logic: pandas in, pandas out, no
I/O. `prices.py` is the one module allowed to talk to the network and to
disk. `visualization.py` never aggregates data — it only renders what the
other modules already computed. This split is what would let a future
React/API layer reuse the exact same logic modules behind HTTP endpoints,
swapping only `visualization.py` for JSON responses.

The notebook (`notebooks/portfolio.ipynb`) is pure orchestration: it calls
these modules in sequence and displays the results. If you add a new metric
or chart, it belongs in one of the modules above, not in the notebook.

## Configuration

Every tunable value — the same-day merge tolerance, the price cache
location, the Yahoo request timeout, the annualization convention, the HYSA
rate — is a field on one of the frozen config objects in `config.py`
(`AggregationConfig`, `PriceApiConfig`, `ReturnsConfig`). Two rules keep this
from turning into indirection for its own sake:

- **No function has a parameter that silently falls back to a module-level
  constant.** Anything that used to be a bare `SOME_DEFAULT = ...` at the top
  of a module is now a field on a config class instead, and functions that
  need it take that config object as a required argument. The config
  classes themselves may (and do) have sensible default field values — that
  default lives in exactly one visible place, not scattered across function
  signatures.
- **A missing input is an error, not a fallback.** `build_returns_table`
  raises if a price is unavailable for a trade, rather than silently
  dropping that trade from the table; `annualized_return_pct` and
  `hysa_period_return_pct` raise on a negative `days_held` (an `as_of` date
  before the trade date is a caller mistake, not a value to quietly compute
  through). An incomplete or wrong-looking table should never pass silently.

## Validation boundary

Pydantic models only exist for the two places untrusted data enters the
system:

- `RawTrade` — one row of the broker CSV (parses `"$1,234.56"` strings,
  rejects non-positive shares/spend).
- `PriceObservation` — one (symbol, date, close) triple from the Yahoo
  Finance API (rejects non-positive closes).

Once data has passed through one of these, it lives in a plain, typed
pandas DataFrame for the rest of the pipeline. There's no pydantic model per
aggregated/return row — that would just be ceremony around data that's
already trusted.

## Same-day trade aggregation

`transactions.aggregate_same_day_trades` groups by `(trade_date, symbol)`,
sorts each group by `usd_per_share`, and chains adjacent rows into one
cluster while each next price is within
`config.same_day_price_tolerance` (default 0.01%, relative) of the previous
one. Each cluster becomes a single row: shares and USD summed,
`usd_per_share` recomputed from those sums. This is a "chain" clustering —
it will merge a slow price drift across many small fills as long as each
*consecutive* step is small, even if the first and last fill in the cluster
differ by more than the tolerance. At the scale of a single day's DRIP/limit
fills, that behavior is intentional and matches what you'd expect from one
continuous execution.

## Price cache

One flat CSV per symbol at `data/prices/{SYMBOL}.csv`, columns
`price_date, close`. This is a *cache*, not a source of truth — the source
of truth is Yahoo Finance; the cache exists so repeated notebook runs don't
re-fetch history they already have. See `docs/prices_api.md` for how the
Yahoo endpoint itself works (request shape, response format, rate-limit
behavior).

Design constraints and how they're met:

- **Never mutate stored rows in place.** `update_price_cache` computes a new
  DataFrame in memory (existing rows + freshly fetched rows, deduplicated by
  date) and only then calls `_write_cache_atomic`, which writes to a `.tmp`
  file and `Path.replace()`s it over the real file. A crash mid-write can
  never leave a half-written or corrupted cache.
- **Validate before it's stored.** Every row coming back from the API is
  parsed through `PriceObservation` before it's eligible to be written —
  malformed API responses (zero/negative close, bad timestamp) raise instead
  of silently entering the cache.
- **Cheap and fast.** `_missing_ranges` compares the cache's existing
  min/max date against the `[since, as_of]` window the caller asked for, and
  only fetches the gap(s) at the front and/or back. A daily notebook run
  after the first one typically fetches a single day per symbol, not the
  whole history.
- **One row per trading day**, not per calendar day — weekends/holidays are
  simply absent, matching what the exchange actually produced. `price_as_of`
  handles the lookup side of this: it returns the most recent close *on or
  before* a target date, so pricing a trade on a Saturday still resolves to
  Friday's close.

Prices are Yahoo's unadjusted daily close (price return, not total return —
dividends aren't folded in). That's a deliberate simplification, documented
here rather than silently baked into the numbers.
