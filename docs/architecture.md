# Architecture

## Module map

```
src/trades/
  config.py        every tunable parameter, as fields on frozen config objects
  models.py        pydantic schemas — the only place external data gets validated,
                    and the single declaration of every canonical column name
  prices.py        Yahoo Finance chart API client + on-disk raw/adjusted price cache
  cpi.py           FRED CPI index client + on-disk cache (real-vs-nominal reference)
  transactions.py  enrich -> aggregate a canonical-shape trade DataFrame; schedule/pie helpers
  returns.py       total/annualized return, HYSA benchmark, alpha, trend fit
  visualization.py every plotly chart; takes data, returns a Figure, nothing else
  ledger/          pure ledger-replay domain logic (see "The ledger" below)
    replay.py      walk the ledger -> open/closed lots + cash balance
    lots.py        FIFO lot consumption, splits, realized gain (no I/O, no ledger-walking)
    metrics.py      )
    counterfactuals.py  )  not yet implemented — see each module's docstring
    nav.py              )  for which NEW_TASKS.md sections it will cover
    taxes.py       )
  brokers/
    ibkr/          IBKR Flex Web Service client + local ledger cache
      api.py       network + XML parsing (SendRequest/GetStatement, `<FlexStatement>`)
      models.py    pydantic schemas for IBKR's raw XML shapes (`IbkrTrade`, ...)
      preprocessing.py  map IBKR's raw shapes onto `models.LedgerEvent`
      main.py      orchestration: sync, rebuild, load the local ledger cache
```

`transactions.py`/`returns.py`/`ledger/*` are pure logic (pandas in, pandas
out, no I/O). `prices.py`/`cpi.py`/`brokers/ibkr/api.py` are the only
modules allowed to touch the network or disk — one per external source, so
a second broker means adding `brokers/schwab/`, not editing what's already
here. `visualization.py` only renders what other modules already computed.
This split is what lets `api.py` reuse the same logic behind HTTP instead
of Plotly.

`transactions.py` only ever sees the trade shape `brokers/ibkr/preprocessing.py`
produces — it has no idea IBKR, or any other broker, exists. See AGENTS.md
for the standing rule this enforces for future data sources.

`ledger/` holds everything that only needs the ledger itself to do its
job — no broker awareness, no I/O. It's a separate folder from
`transactions.py`/`returns.py` (which predate the ledger and still run on
an older, narrower shape — see below) because NEW_TASKS.md's Phase 0-4
work is naturally one growing family of modules that all consume
`replay.py`'s output, not a pair of files.

## The ledger

The stored truth of this system is one chronological, append-only list of
events — never a "current position" or "current cash balance" stored as
its own editable field. Everything else (positions, cost basis, gains,
every chart) gets computed by **replaying** this list from the start, so
there's exactly one place to look when a number is wrong, and a new metric
later is a new replay function, not a migration. One row (`models.LedgerEvent`):

```
{ event_id, event_datetime, symbol, event_type, shares, price, amount, currency, meta }
```

`event_datetime` is a full timestamp, not just a date — IBKR reports fills
to the second, and truncating that now would be unrecoverable later.
`amount` is always a non-negative magnitude; direction (cash up or down)
comes from `event_type` alone, never a sign. `event_type` is one of eight
kinds:

- `DEPOSIT` / `WITHDRAWAL` — external cashflows, money crossing the
  portfolio boundary.
- `BUY` / `SELL` — internal cashflows, cash moving against a holding.
  `amount` is the pure principal; commission is its own `FEE` row.
- `DIVIDEND` — a cash distribution, recorded on pay date at the broker's
  actual amount, never estimated from yield.
- `WITHHOLDING` — tax withheld on a dividend, its own row (not netted into
  `DIVIDEND`) so gross income and tax drag both stay visible.
- `FEE` — commissions and account fees.
- `SPLIT` — `{symbol, ratio}` in `meta`; on replay, multiplies open lots'
  shares by `ratio` and divides cost/share by it.

Cash is a pseudo-position under the symbol `"CASH"` — `DEPOSIT`/`SELL`/
`DIVIDEND` add to it, `WITHDRAWAL`/`BUY`/`FEE` subtract — so
`portfolio_value(t) = sum(shares * price) + cash`, no special-casing.
Populated so far by IBKR's `BUY`/`SELL`/`FEE` (from `<Trade>` rows) and
`DEPOSIT`/`WITHDRAWAL`/`DIVIDEND`/`WITHHOLDING` (from `<CashTransaction>`
rows, only if the Flex Query's "Cash Transactions" section is enabled).
`SPLIT` has no source yet — no corporate-actions section is pulled.

All eight are declared regardless: adding a case to a `Literal` in an
empty design is free; retrofitting one into a live system whose metrics
already assume a narrower shape is not. When a new source shows up,
`brokers/ibkr/preprocessing.py` gains a mapping branch — the schema
doesn't change.

`brokers/ibkr/preprocessing.py`'s `standardize_ibkr_ledger` (from
`<Trade>`) and `standardize_ibkr_cash_transactions` (from
`<CashTransaction>`) do the mapping; both validate their output through
`models.LedgerEvent` before returning it. `standardize_ibkr_ledger` also
flags a dividend-reinvestment `BUY` (IBKR trade-note code `"R"`, verified
empirically — see its docstring) with `meta["drip_reinvestment"] = "true"`.

`transactions.py` predates the ledger and still runs on an older, narrower
4-column shape (`models.RawTrade`: `trade_date`, `symbol`, `shares`,
`usd_spent`) — one row per real purchase, no fees, day-grained.
`preprocessing.standardize_ibkr_trades` derives it *from the ledger*
(`BUY` events, excluding the `CASH` pseudo-position) rather than from
broker-native data directly, so `transactions.py`/`returns.py`/
`visualization.py` never special-case a broker and don't care that the
ledger grew event types they don't consume. Most of `transactions.py`/
`returns.py` is expected to be replaced by `ledger/*` as NEW_TASKS.md's
phases land — see "Replaying the ledger: lots and cash" below.

## Replaying the ledger: lots and cash

`ledger.replay.replay_ledger(ledger: pd.DataFrame) -> ReplayResult` is the
one place the ledger gets walked chronologically (0.1-0.3). It returns:

```
ReplayResult:
  open_lots: DataFrame     # lot_id, symbol, opened_at, shares, cost_per_share
  closed_lots: DataFrame   # + closed_at, exit_price, realized_gain, term, closed_by_event_id
  cash_balance: float
```

The walk is a genuine sequential fold — each event's effect depends on
lots left open by every prior event — so it's a plain `for` loop over
`ledger.iterrows()`, not a vectorized expression; that's a deliberate
exception to the "avoid for loops" rule elsewhere in this codebase (see
AGENTS.md), not an oversight.

Per event type:
- `BUY` opens a new lot (`ledger.lots.Lot`) and reduces `cash_balance`.
- `SELL` consumes lots **oldest-`opened_at`-first** via
  `ledger.lots.consume_fifo` and increases `cash_balance`. Each closed
  portion is tagged `LONG`/`SHORT` (>= 365 days held) and gets
  `realized_gain = shares_consumed x (exit_price - cost_per_share) -
  allocated_fees` (0.3) — `allocated_fees` defaults to 0 today; linking a
  `SELL` to its `FEE` event's amount is a known gap, deferred rather than
  built on a fragile assumption about `event_id` naming.
- `DEPOSIT`/`WITHDRAWAL`/`DIVIDEND`/`WITHHOLDING`/`FEE` only move
  `cash_balance` — **cash is a plain running total, not a lot**: every
  dollar is identical, so unlike a real symbol (where different buys have
  different prices) there's no cost-basis heterogeneity for FIFO to track.
  A separate `cash.py` was considered and deliberately skipped for this
  reason (see the architecture discussion this folder came out of).
- `SPLIT` multiplies open lots' shares and divides their cost/share via
  `ledger.lots.apply_split` (0.1) — cash is untouched.

`ledger.lots.py` itself has no I/O and no ledger-walking: `consume_fifo`
and `apply_split` are pure functions over a `list[Lot]`, independently
tested without needing a ledger DataFrame at all.

## Configuration

Every tunable value lives on a frozen config object in `config.py`
(`AggregationConfig`, `PriceApiConfig`, `CpiConfig`, `ReturnsConfig`,
`IbkrFlexApiConfig`, ...), never a bare module-level constant a function
falls back to — functions that need a value take the config object as a
required argument (e.g. `IbkrFlexApiConfig.drip_reinvestment_note_code`,
since a broker could plausibly change its own codes). A domain *fact*
nothing external could reconfigure (IBKR's retry codes, the `"CASH"`
pseudo-symbol) is a private module constant instead; only things a caller
might legitimately want to change belong in `config.py`.

A missing input is an error, not a silent fallback: `build_returns_table`
raises if a price is unavailable rather than dropping the trade;
`annualized_return_pct` raises on a negative `days_held`. An incomplete or
wrong-looking table should never pass silently.

## Validation boundary

Pydantic models exist only at the edges, where untrusted data enters:

- `RawTrade` — the trade-schema row (broker CSV export, or a ledger `BUY`).
- `LedgerEvent` — one ledger row; every `standardize_*` in `preprocessing.py`
  validates its output through it.
- `PriceObservation` — one (symbol, date, close) from the Yahoo API (raw
  or adjusted — same shape, see "Price cache" below).
- `CpiObservation` — one (month, index value) from FRED's CPI series.
- `IbkrTrade` / `IbkrCashTransaction` (in `brokers/ibkr/models.py`) — one
  `<Trade>` / `<CashTransaction>` row; field aliases match IBKR's XML
  attribute names exactly, so an element's `.attrib` dict validates with
  no manual mapping.

Once past one of these, data is a plain, typed pandas DataFrame for the
rest of the pipeline — no pydantic model per aggregated/return row, that
would just be ceremony around data that's already trusted.

## Same-day trade aggregation

`transactions.aggregate_same_day_trades` groups by `(trade_date, symbol)`,
sorts by `usd_per_share`, and chains adjacent rows into one cluster while
each next price stays within `config.same_day_price_tolerance` (default
0.01%, relative) of the previous one — a "chain" clustering, so a slow
price drift across many small fills merges as long as each *consecutive*
step is small, even if the first and last differ by more. That matches
what a single day's DRIP/limit fills should look like as one execution.

## Price cache

Two flat CSVs per symbol under `data/prices/`: `{SYMBOL}.csv` (raw close)
and `{SYMBOL}.adjusted.csv` (dividend/split-adjusted close) — caches, not
a source of truth (see `docs/prices_api.md` for the Yahoo endpoint
itself). NEW_TASKS.md 0.5's rule: raw for pricing your own positions,
adjusted for benchmark counterfactuals (e.g. all-VOO) — mixing the two
silently corrupts every return, so they're deliberately separate files,
not a column flag on one. Both share the same mechanics: `update_price_cache`
/`update_adjusted_price_cache` never mutate rows in place (existing +
fetched rows merged in memory, then an atomic replace), every row is
validated through `PriceObservation` before it's storable, and
`_missing_ranges` only fetches the gap(s) at the front/back of what's
cached — a daily run typically fetches one day per symbol. One row per
trading day, not calendar day; `price_as_of` returns the most recent
close on or before a target date (never interpolated, per 0.5).

## CPI cache

One flat CSV at `data/cpi/{series_id}.csv` (`observation_date, value`),
pulled from FRED's public CSV export (no API key). Unlike prices, the
whole series is re-fetched and the cache overwritten on every
`update_cpi_cache` call rather than fetching just the missing range —
FRED revises seasonal adjustments on already-published months, so an
incremental fetch could miss a revision to old data, and the full series
is small enough (a few hundred KB) that re-fetching it is cheap. Still an
atomic write. `cpi_as_of` mirrors `price_as_of`'s rollback lookup.

## Data layout

```
data/
  prices/
    {SYMBOL}.csv                          raw close, market data, not broker-specific
    {SYMBOL}.adjusted.csv                 dividend/split-adjusted close
  cpi/{series_id}.csv                     CPI index, re-fetched whole each update
  brokers/ibkr/
    manual_20260701_trades.csv           one-off manual export, kept for history
    raw_statements/{timestamp}.xml       every fetch, verbatim, never overwritten
    ledger.csv                           rebuildable cache, derived from raw_statements/
```

`ledger.csv` is the one file `brokers/ibkr/main.py` derives from the raw
archive (see `docs/ibkr_flex_api.md`, "Storage"). Broker data lives under
`data/brokers/{broker}/` so a second broker is a new sibling directory;
price/CPI history isn't broker-specific, so it stays outside `brokers/`.

`.env` and everything under `data/` are gitignored — this is personal
financial data, not fixtures.

## Guidance for AI assistants working on this repo

`AGENTS.md` (`CLAUDE.md` is a symlink to it) carries standing conventions,
in particular around adding new data sources and caching pulled data.
Read it before adding a new broker, external API, or cached/fetched data.
