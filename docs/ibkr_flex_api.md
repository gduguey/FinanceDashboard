# The IBKR Flex Web Service

`src/trades/brokers/ibkr.py` pulls the "Trade History API" Flex Query
(Cash Report + Open Positions + Trades sections) via IBKR's Flex Web
Service — an XML API, separate from both the interactive TWS API and the
Yahoo price API in `prices.py`.

## Credentials

Two secrets, read from `.env` (see `IbkrFlexCredentials` in `config.py`,
which wraps `pydantic-settings` — never hardcoded, never a default):

- `IBKR_FLEX_WEB_SERVICE_TOKEN` — from Account Management → Reports →
  Settings → Flex Web Service.
- `IBKR_QUERY_ID` — the numeric ID of the saved Activity Flex Query
  ("Trade History API" in this account), from Reports → Flex Queries.

`.env` is gitignored; the token is held as a `SecretStr` so it never leaks
into a log line, a repr, or a notebook cell's accidental output.

## The two-step protocol

1. **SendRequest** — exchange the token + query ID for a reference code:
   `GET {send_request_url}?v=3&t={token}&q={query_id}`. Response is a small
   `<FlexStatementResponse>` XML with either `<Status>Success</Status>` plus
   `<ReferenceCode>`/`<Url>`, or `<Status>Fail</Status>` plus
   `<ErrorCode>`/`<ErrorMessage>`.
2. **GetStatement** — poll the `<Url>` from step 1 with the reference code
   until the report is ready: `GET {url}?v=3&t={token}&q={reference_code}`.
   While IBKR is still generating the report, this returns another
   `<FlexStatementResponse>` error instead of data — polling is required.

`fetch_flex_statement` runs both steps and returns the final
`<FlexQueryResponse>` XML. A `User-Agent: Java` header is required — IBKR
rejects the request without one.

## Error codes

IBKR documents ~20 error codes for this service. `ibkr.py` treats them in
three groups:

- **Retry after `server_busy_retry_seconds`** (1001, 1004–1009, 1019, 1021):
  "not ready yet" / "under load" — expected, transient.
- **Retry after `throttled_retry_seconds`** (1018): too many requests from
  this token.
- **Everything else raises `FlexApiError` immediately** (1003 statement
  unavailable, 1010 legacy query, 1011 inactive account, 1012 expired
  token, 1013 IP restriction, 1014/1015/1016/1017/1020 invalid
  query/token/account/reference/request). These indicate a real
  configuration problem, not something retrying will fix.

Polling stops and raises after `config.max_poll_attempts`.

## Why this is a sync, not just a fetch

The Flex Query itself is configured (in IBKR's web UI, not by this code —
the API has no from/to-date parameters for a saved query) with a **Period**
— currently **Last 365 Calendar Days**, previously "Last Business Day"
during initial development. Either way, every call returns a bounded
window of activity, not "everything since the beginning": IBKR's Period
options never mean "since I last asked." That has two consequences baked
into `sync_ibkr_account`, and they hold regardless of which Period is
currently configured:

- **A pull missed for longer than the window covers is a permanent gap**,
  not something you can ask IBKR for later through this same query (a
  365-day window makes this far less likely day-to-day than the original
  1-day window did, but it's the same failure mode if the query goes
  unrun for over a year, or gets reconfigured back to something narrower).
  So before merging a new pull in, `sync_ibkr_account` checks that the new
  pull's `fromDate` connects to the last cached trade date with no
  unaccounted-for weekday in between, and raises `TradeHistoryGapError` if
  it doesn't. (A public holiday will still trip this as a false positive —
  cheap insurance, since the failure mode is "you have to double-check,"
  never "a trade silently vanished.") If that happens, backfill the gap
  with a one-off custom-date-range Flex Query before syncing again.
- **Trades are deduplicated by `transactionID`** (IBKR's stable, always-
  present per-trade identifier), so re-running a sync — even one that
  re-fetches a year of overlapping history — only ever adds what's
  genuinely new.

Cash and position rows are the opposite: they're an intentional time
series, one snapshot per pull, each tagged with `pulled_at` (IBKR's own
`whenGenerated` timestamp for that statement, not local wall-clock time) —
appended every time, never deduplicated, so you can see the account's
value over time.

## Storage

`data/brokers/ibkr/`:

- `raw_statements/{timestamp}.xml` — every fetched statement, verbatim,
  forever. This is the actual source of truth; see below.
- `trades.csv` — full deduplicated trade history, one row per `transactionID`.
- `position_snapshots.csv` — every pull's open positions, tagged with `pulled_at`.
- `cash_snapshots.csv` — every pull's cash balance, tagged with `pulled_at`.

Same rules as the Yahoo price cache in `prices.py`: writes are atomic
(temp file + rename), nothing is edited in place, and a network response is
validated through `IbkrTrade`/`IbkrPosition`/`IbkrCashBalance` before it can
reach disk.

## The raw archive, and why the derived CSVs are disposable

Atomic writes protect against a *crash* mid-write — they don't protect
against a *logic* bug in the merge or parsing code writing a fully-formed,
fully-committed, wrong result. Since `trades.csv` etc. are each a single
file that gets overwritten on every sync, a bug like that would otherwise
be unrecoverable: there'd be no earlier good copy left to fall back to, and
(especially with a narrow Period configured) no guarantee IBKR would still
hand back the lost data on a re-fetch.

So `sync_ibkr_account` saves the raw `FlexQueryResponse` XML to
`raw_statements/` — unmodified, never overwritten — *before* it attempts
to parse or merge anything. `trades.csv`, `position_snapshots.csv`, and
`cash_snapshots.csv` are just a cache of that archive, rebuildable at any
time with `rebuild_from_raw_statements(config)`, which re-parses every
archived statement and regenerates all three files from scratch. If a
derived file is ever found to be wrong, corrupted, or missing, the fix is
"fix the code, then call `rebuild_from_raw_statements`" — never "hope a
backup exists."
