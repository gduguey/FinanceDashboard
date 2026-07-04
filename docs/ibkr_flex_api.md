# IBKR Flex Web Service

`src/trades/brokers/ibkr/` pulls account history from Interactive
Brokers' Flex Web Service — an XML API separate from the TWS API and from
the market-data modules in `market_data/`.

The module's job: fetch raw statements, archive them verbatim, map IBKR's
native rows onto the canonical ledger schema, and merge into the local
cache. Everything downstream (`ledger/`, `dashboard/`, `api.py`) reads
`ledger.csv` and never touches IBKR directly.

For ledger event types and replay logic, see [ledger.md](ledger.md).

---

## Module layout

```
brokers/ibkr/
  api.py            network I/O, XML parsing, raw statement archival
  models.py         pydantic schemas for IBKR's raw XML shapes
  preprocessing.py  IBKR rows → LedgerEvent
  main.py           sync orchestration, ledger load/save, rebuild
```

| Module | Responsibility |
|--------|----------------|
| `api.py` | SendRequest/GetStatement protocol, error handling, `parse_statement()` |
| `models.py` | `IbkrTrade`, `IbkrCashTransaction` — field aliases match XML attribute names |
| `preprocessing.py` | `statement_to_ledger()` — maps trades + cash transactions to `LedgerEvent` |
| `main.py` | `sync_ibkr_account()`, `load_ledger()`, `rebuild_from_raw_statements()` |

---

## Credentials

From `.env` (gitignored), via `IbkrFlexCredentials`:

```
IBKR_FLEX_WEB_SERVICE_TOKEN=...
IBKR_QUERY_ID=...
```

- **Token** — Account Management → Reports → Settings → Flex Web Service
- **Query ID** — the numeric ID of your saved Flex Query (Reports → Flex
  Queries)

The token is a `SecretStr` so it never leaks into logs or reprs.

---

## Flex Query setup

Configure a Flex Query in IBKR's Account Management UI with at least:

- **Trades** — BUY/SELL fills and commissions
- **Cash Transactions** — deposits, withdrawals, dividends, withholding,
  fees

Optional sections (parsed into the raw archive but not yet mapped to
ledger events):

- **Open Positions** — state snapshot, not an event
- **Cash Report** — balance snapshot, not an event

Set the query's **Period** to "Last 365 Calendar Days" (or similar rolling
window). The API has no from/to-date parameters — the window is fixed in
IBKR's web UI.

---

## The two-step protocol

Implemented in `api.py` → `fetch_flex_statement()`:

1. **SendRequest**
   ```
   GET {send_request_url}?v=3&t={token}&q={query_id}
   ```
   Returns a reference code and statement URL.

2. **GetStatement** — poll until ready:
   ```
   GET {url}?v=3&t={token}&q={reference_code}
   ```
   Returns the full `<FlexQueryResponse>` XML when generation completes.

A `User-Agent: Java` header is required — IBKR rejects requests without
one.

### Error codes

| Category | Codes | Action |
|----------|-------|--------|
| Retry (server busy) | 1001, 1004–1009, 1019, 1021 | Wait `server_busy_retry_seconds`, retry |
| Retry (throttled) | 1018 | Wait `throttled_retry_seconds`, retry |
| Fatal | 1003, 1010–1017, 1020 | Raise `FlexApiError` immediately |

Polling stops after `config.max_poll_attempts`.

---

## What gets mapped

### From `<Trade>` (`preprocessing._standardize_ibkr_trades`)

| IBKR row | Ledger event(s) |
|----------|-----------------|
| BUY fill | `BUY` — principal at `trade_price × quantity` |
| SELL fill | `SELL` |
| Non-zero commission | Separate `FEE` row (`event_id` suffixed `:fee`) |
| BUY with DRIP note code | `BUY` + `meta["drip_reinvestment"] = "true"` |
| BUY/SELL (Ca.) cancellation | Dropped |

`amount` on BUY/SELL is principal only. Commission never inflates cost
basis.

### From `<CashTransaction>` (`preprocessing._standardize_ibkr_cash_transactions`)

| IBKR type | Ledger event |
|-----------|--------------|
| Deposits / Withdrawals | DEPOSIT / WITHDRAWAL |
| Dividends, Payment In Lieu, Broker/Bond Interest Received | DIVIDEND |
| Withholding Tax | WITHHOLDING |
| Broker Interest Paid, Other Fees, Commission Adjustments | FEE |

Dividend tax character is tagged in `meta` at mapping time:

| IBKR type | `meta` tag |
|-----------|------------|
| Broker/Bond Interest Received | `"interest"` |
| Payment In Lieu Of Dividends | `"substitute_payment"` |
| Ordinary dividends | `"qualified"` when applicable |

This keeps `ledger/taxes.py` free of IBKR-specific strings.

### Not mapped yet

- **SPLIT** — no corporate-actions section is pulled
- **Open Positions / Cash Report** — snapshots, archived in raw XML only

---

## Sync flow

`main.py` → `sync_ibkr_account()`:

```
1. fetch_flex_statement()     → raw XML
2. save_raw_statement()       → data/brokers/ibkr/raw_statements/{timestamp}.xml
3. parse_statement()          → IbkrTrade + IbkrCashTransaction DataFrames
4. statement_to_ledger()      → LedgerEvent DataFrame (validated)
5. merge with existing ledger → dedupe by event_id, sort chronologically
6. write ledger.csv           → atomic replace
```

### Gap detection

The Flex Query's Period bounds every call to a rolling window — it never
means "since I last asked." If a sync is missed for longer than the window
covers, `sync_ibkr_account()` raises `TradeHistoryGapError` before merging.

**Fix:** run a one-off custom-date-range query in IBKR's UI to backfill the
gap, then sync again.

### Dedup

Ledger events dedupe by `event_id`:

- Trade principal: `ibkr:{transactionID}`
- Trade fee: `ibkr:{transactionID}:fee`
- Cash transactions: `ibkr:cash:{transactionID}`

Re-syncing overlapping history only adds genuinely new events.

---

## Storage

```
data/brokers/ibkr/
  raw_statements/{timestamp}.xml   every fetch, verbatim, never overwritten
  ledger.csv                       deduplicated event ledger (rebuildable)
```

### Why the ledger is disposable

An atomic write protects against a crash mid-write — not a logic bug that
produces a wrong-but-complete result and overwrites the only copy.

So:

1. **Raw XML is archived first**, before any parsing.
2. **`ledger.csv` is a cache** derived from that archive.

If the ledger is ever wrong, corrupted, or missing:

```
rebuild_from_raw_statements(config)
```

Re-parses every archived statement and regenerates `ledger.csv` from
scratch. Fix the code, then rebuild — never hope a backup exists.

---

## Validation chain

Every row passes through two pydantic boundaries:

```
IBKR XML attributes  →  IbkrTrade / IbkrCashTransaction  →  LedgerEvent
```

Native field names stop at `models.py`. The rest of the codebase sees only
canonical ledger column names.

---

## Adding fields or sources

To pull a new IBKR section (e.g. corporate actions for SPLIT events):

1. Add a pydantic model in `brokers/ibkr/models.py` with XML attribute aliases.
2. Parse the new section in `api.py` → `parse_statement()`.
3. Add a mapping branch in `preprocessing.py`, validating through `LedgerEvent`.
4. Enable the section in your Flex Query's IBKR web UI.

The ledger schema (`LedgerEvent`) already declares SPLIT — no schema change
needed, just a new preprocessing branch.
