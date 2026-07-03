# The IBKR Flex Web Service

`src/trades/brokers/ibkr.py` pulls a Flex Query via IBKR's Flex Web
Service — an XML API, separate from the interactive TWS API and the
Yahoo price API in `prices.py`. The query ("Trade History API" in this
account) includes Trades, Cash Report, Open Positions, and (once enabled
in IBKR's web UI) Cash Transactions.

## Credentials

From `.env`, via `IbkrFlexCredentials` (`pydantic-settings`, never
hardcoded): `IBKR_FLEX_WEB_SERVICE_TOKEN` (Account Management → Reports →
Settings → Flex Web Service) and `IBKR_QUERY_ID` (the saved query's
numeric ID, from Reports → Flex Queries). `.env` is gitignored; the token
is a `SecretStr` so it never leaks into a log line or repr.

## The two-step protocol

1. **SendRequest** — `GET {send_request_url}?v=3&t={token}&q={query_id}`
   exchanges the token + query ID for a reference code and statement URL.
2. **GetStatement** — poll `GET {url}?v=3&t={token}&q={reference_code}`
   until the report is ready; while IBKR is still generating it, this
   returns an error instead of data.

`fetch_flex_statement` runs both steps and returns the raw
`<FlexQueryResponse>` XML. A `User-Agent: Java` header is required — IBKR
rejects the request without one.

## Error codes

- **Retry after `server_busy_retry_seconds`** (1001, 1004–1009, 1019,
  1021): transient, "not ready yet" / "under load".
- **Retry after `throttled_retry_seconds`** (1018): too many requests.
- **Everything else raises `FlexApiError` immediately** (1003, 1010–1017,
  1020: unavailable statement, bad/expired/inactive query or token) — a
  real configuration problem, not something retrying fixes.

Polling stops and raises after `config.max_poll_attempts`.

## Why this is a sync, not just a fetch

The query's **Period** (currently "Last 365 Calendar Days", set in IBKR's
web UI — the API has no from/to-date parameters) bounds every call to a
window; it never means "since I last asked." Two consequences in
`sync_ibkr_account`:

- **A pull missed for longer than the window covers is a permanent gap.**
  Before merging a new pull in, `sync_ibkr_account` checks that its
  `fromDate` connects to the last cached ledger event's date with no
  unaccounted weekday in between, raising `TradeHistoryGapError` if not
  (a holiday can false-positive this — cheap insurance, never a silent
  loss). Backfill a gap with a one-off custom-date-range query, then sync
  again.
- **Ledger events dedupe by `event_id`** (`ibkr:{transactionID}`, or
  `:fee` suffixed for the commission event — see
  `preprocessing.standardize_ibkr_ledger`), so re-syncing overlapping
  history only adds what's genuinely new.

`<OpenPosition>`/`<CashReportCurrency>` rows aren't parsed into anything
today — they're a state snapshot, not an event — but stay in
`raw_statements/` verbatim for future reconciliation work.

## Storage

`data/brokers/ibkr/`:

- `raw_statements/{timestamp}.xml` — every fetched statement, verbatim,
  forever. This is the real source of truth.
- `ledger.csv` — the deduplicated event ledger (see docs/architecture.md,
  "The ledger").

Writes are atomic (temp file + rename); a network response is validated
through `IbkrTrade`/`IbkrCashTransaction`, then through `LedgerEvent`
once `preprocessing.py` maps it onto the ledger shape, before it can
reach disk.

## Why the ledger is disposable

An atomic write only protects against a crash mid-write — not a logic bug
that produces a wrong-but-complete result and overwrites the only copy.
So `sync_ibkr_account` archives the raw XML to `raw_statements/` *before*
parsing anything; `ledger.csv` is just a rebuildable cache of that
archive. `rebuild_from_raw_statements(config)` re-parses every archived
statement and regenerates `ledger.csv` from scratch — if it's ever wrong,
corrupted, or missing, the fix is "fix the code, then rebuild," never
"hope a backup exists."
