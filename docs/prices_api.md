# The Yahoo Finance price API

`prices.py` pulls daily closes from Yahoo Finance's chart endpoint — the
same undocumented, unauthenticated JSON API that backs the charts on
finance.yahoo.com and that the `yfinance` package wraps. We call it directly
with `requests` instead of depending on `yfinance`, since we only need daily
closes and it saves a fairly heavy transitive dependency.

There is no API key, no signup, and no documented rate limit or SLA — it's
free because it's not officially a public API. Treat it accordingly: fine
for a personal dashboard, not something to build a paid product on without
switching to a real data vendor.

## Request

```
GET https://query2.finance.yahoo.com/v8/finance/chart/{symbol}
    ?period1={unix_seconds_start}&period2={unix_seconds_end}&interval=1d
```

- `period1`/`period2` are Unix seconds (UTC), built by `_to_unix()`.
  `period2` is the requested end date plus one day, so the end date itself
  is always included (Yahoo's range is effectively exclusive at the edge).
- `interval=1d` asks for one row per trading day.
- A `User-Agent` header is required — the default `requests`/`urllib`
  user agent gets blocked; `REQUEST_HEADERS` sets a generic browser-like one.

We use the `query2` host specifically. In testing, `query1.finance.yahoo.com`
returned `429 Too Many Requests` consistently from this environment while
`query2` responded normally — both serve the same API, and which one is
more rate-limited seems to vary. `CHART_URL` is one constant, so switching
host (or adding fallback/retry across hosts) is a one-line change if `query2`
degrades too.

## Response shape

```json
{
  "chart": {
    "result": [{
      "timestamp": [1767225000, 1767311400, ...],
      "indicators": {"quote": [{"close": [640.4, 645.1, null, ...]}]}
    }],
    "error": null
  }
}
```

- `timestamp[i]` pairs with `indicators.quote[0].close[i]`.
- `close` entries can be `null` (e.g. a requested day with no trade data);
  `fetch_price_history` drops those rather than storing a fake price.
- Timestamps mark the trading session (around 13:30–14:30 UTC for NYSE,
  during market hours), so converting with `datetime.fromtimestamp(ts,
  tz=UTC).date()` reliably lands on the correct trading day — US market
  hours never cross a UTC midnight boundary.
- If `chart.result` is missing or empty (bad symbol, no data in range),
  `fetch_price_history` raises `ValueError` with whatever `chart.error` says
  rather than silently returning nothing.

## Why the cache design keeps this cheap

Because there's no documented rate limit, the safest assumption is "as few
requests as possible, as small as possible." `update_price_cache` only ever
fetches the date range actually missing from the on-disk cache (see
`docs/architecture.md`), so after the first backfill, a daily rerun costs
one small request per symbol — a handful of rows, not the full history.
