# Architecture

## Module map

```
src/trades/
  config.py        every tunable parameter, as fields on frozen config objects
  models.py        pydantic schemas — the only place external data gets validated,
                    and the single declaration of every canonical column name
  preprocessing.py map each broker's native shape onto a models.py schema
  transactions.py  enrich -> aggregate a canonical-shape trade DataFrame; schedule/pie helpers
  prices.py        Yahoo Finance chart API client + on-disk price cache
  returns.py       total/annualized return, HYSA benchmark, alpha, trend fit
  visualization.py every plotly chart; takes data, returns a Figure, nothing else
  brokers/
    ibkr.py        IBKR Flex Web Service client + local ledger cache
```

`transactions.py`/`returns.py` are pure logic (pandas in, pandas out, no I/O).
`prices.py`/`brokers/ibkr.py` are the only modules allowed to touch the
network or disk — one per external source, so a second broker means adding
`brokers/schwab.py`, not editing what's already here. `visualization.py`
only renders what other modules already computed. This split is what lets
`api.py` reuse the same logic behind HTTP instead of Plotly.

`transactions.py` only ever sees the trade shape `preprocessing.py`
produces — it has no idea IBKR, or any other broker, exists. See AGENTS.md
for the standing rule this enforces for future data sources.

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

Cash is a pseudo-position under the symbol `"CASH"` (`preprocessing`'s
`_CASH_SYMBOL`) — `DEPOSIT`/`SELL`/`DIVIDEND` add to it,
`WITHDRAWAL`/`BUY`/`FEE` subtract — so `portfolio_value(t) = sum(shares *
price) + cash`, no special-casing. This repo doesn't replay the ledger
into that value yet; today the ledger is the canonical *stored* shape,
populated by IBKR's `BUY`/`SELL`/`FEE` (from `<Trade>` rows) and
`DEPOSIT`/`WITHDRAWAL`/`DIVIDEND`/`WITHHOLDING` (from `<CashTransaction>`
rows, only if the Flex Query's "Cash Transactions" section is enabled).
`SPLIT` has no source yet — no corporate-actions section is pulled.

All eight are declared regardless: adding a case to a `Literal` in an
empty design is free; retrofitting one into a live system whose metrics
already assume a narrower shape is not. When a new source shows up,
`preprocessing.py` gains a mapping branch — the schema doesn't change.

`preprocessing.standardize_ibkr_ledger` (from `<Trade>`) and
`standardize_ibkr_cash_transactions` (from `<CashTransaction>`) do the
mapping; both validate their output through `models.LedgerEvent` before
returning it. `standardize_ibkr_ledger` also flags a dividend-reinvestment
`BUY` (IBKR trade-note code `"R"`, verified empirically — see its
docstring) with `meta["drip_reinvestment"] = "true"`.

`transactions.py` predates the ledger and still runs on an older, narrower
4-column shape (`models.RawTrade`: `trade_date`, `symbol`, `shares`,
`usd_spent`) — one row per real purchase, no fees, day-grained.
`preprocessing.standardize_ibkr_trades` derives it *from the ledger*
(`BUY` events, excluding the `CASH` pseudo-position) rather than from
broker-native data directly, so `transactions.py`/`returns.py`/
`visualization.py` never special-case a broker and don't care that the
ledger grew event types they don't consume.

## Configuration

Every tunable value lives on a frozen config object in `config.py`
(`AggregationConfig`, `PriceApiConfig`, `ReturnsConfig`, ...), never a bare
module-level constant a function falls back to — functions that need a
value take the config object as a required argument. A domain *fact*
(IBKR's retry codes, the DRIP note code, the CASH pseudo-symbol) is a
private module constant instead; only things a caller might legitimately
want to change belong in `config.py`.

A missing input is an error, not a silent fallback: `build_returns_table`
raises if a price is unavailable rather than dropping the trade;
`annualized_return_pct` raises on a negative `days_held`. An incomplete or
wrong-looking table should never pass silently.

## Validation boundary

Pydantic models exist only at the edges, where untrusted data enters:

- `RawTrade` — the trade-schema row (broker CSV export, or a ledger `BUY`).
- `LedgerEvent` — one ledger row; every `standardize_*` in `preprocessing.py`
  validates its output through it.
- `PriceObservation` — one (symbol, date, close) from the Yahoo API.
- `IbkrTrade` / `IbkrCashTransaction` — one `<Trade>` / `<CashTransaction>`
  row; field aliases match IBKR's XML attribute names exactly, so an
  element's `.attrib` dict validates with no manual mapping.

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

One flat CSV per symbol at `data/prices/{SYMBOL}.csv` (`price_date,
close`) — a cache, not a source of truth (see `docs/prices_api.md` for
the Yahoo endpoint itself). `update_price_cache` never mutates rows in
place: it computes existing + fetched rows in memory, deduplicates, and
atomically replaces the file. Every row is validated through
`PriceObservation` before it's storable. `_missing_ranges` only fetches
the gap(s) at the front/back of what's cached, so a daily run typically
fetches one day per symbol. One row per trading day, not calendar day;
`price_as_of` returns the most recent close on or before a target date.
Prices are Yahoo's unadjusted daily close (price return, not total
return) — a deliberate simplification.

## Data layout

```
data/
  prices/{SYMBOL}.csv                    market data, not broker-specific
  brokers/ibkr/
    manual_20260701_trades.csv           one-off manual export, kept for history
    raw_statements/{timestamp}.xml       every fetch, verbatim, never overwritten
    ledger.csv                           rebuildable cache, derived from raw_statements/
```

`ledger.csv` is the one file `brokers/ibkr.py` derives from the raw
archive (see `docs/ibkr_flex_api.md`, "Storage"). Broker data lives under
`data/brokers/{broker}/` so a second broker is a new sibling directory;
price history isn't broker-specific, so it stays outside `brokers/`.

`.env` and everything under `data/` are gitignored — this is personal
financial data, not fixtures.

## Guidance for AI assistants working on this repo

`AGENTS.md` (`CLAUDE.md` is a symlink to it) carries standing conventions,
in particular around adding new data sources and caching pulled data.
Read it before adding a new broker, external API, or cached/fetched data.
