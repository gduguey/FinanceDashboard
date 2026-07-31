# The ledger

The stored truth of this system is one chronological, append-only list of
events. Positions, cost basis, gains, charts, and tax estimates are all
**replayed** from this list — never stored as editable state.

For metric definitions (XIRR, NAV, counterfactuals), see
[metrics_and_benchmarks.md](metrics_and_benchmarks.md). For plain-language
term definitions, see [glossary.md](glossary.md).

---

## One row, one event

Every event is a `LedgerEvent` (`models.py`):

```
event_id, event_datetime, symbol, event_type, shares, price, amount, currency, meta
```

| Field | Meaning |
|-------|---------|
| `event_id` | Stable unique ID (e.g. `ibkr:12345`, `ibkr:12345:fee`) |
| `event_datetime` | Full timestamp — IBKR reports fills to the second |
| `symbol` | Ticker, or `"CASH"` for cash-only events |
| `event_type` | One of eight kinds (below) |
| `shares` | Share count for BUY/SELL; `None` otherwise |
| `price` | Per-share price for BUY/SELL; `None` otherwise |
| `amount` | Non-negative cash magnitude; direction comes from `event_type` |
| `currency` | ISO currency code |
| `meta` | Source-specific tags (DRIP flag, dividend tax character, split ratio, …) |

`amount` is always non-negative. Never encode direction with a sign.

---

## Event types

### External cashflows (cross the portfolio boundary)

| Type | Effect on cash | Notes |
|------|----------------|-------|
| **DEPOSIT** | Increases | Money entering the account from outside |
| **WITHDRAWAL** | Decreases | Money leaving the account |

These are the only events that count as external cash flows for XIRR,
contributions, and counterfactuals.

### Internal cashflows (cash ↔ holdings)

| Type | Effect on cash | Effect on lots |
|------|----------------|----------------|
| **BUY** | Decreases | Opens a new lot (FIFO queue) |
| **SELL** | Increases | Closes lots oldest-first |

`amount` on BUY/SELL is the **principal only** (shares × price).
Commission is a separate **FEE** row so it never inflates a lot's cost
basis.

A DRIP reinvestment BUY is tagged `meta["drip_reinvestment"] = "true"`.
It is not a new contribution — no money entered from outside.

### Income and charges

| Type | Effect on cash | Notes |
|------|----------------|-------|
| **DIVIDEND** | Increases | Cash distribution on pay date, at broker's actual amount |
| **WITHHOLDING** | Decreases | Tax withheld on a dividend — separate row, not netted into DIVIDEND |
| **FEE** | Decreases | Commissions, account fees, interest paid |

Dividends also **accrue into open lots** of the same symbol, pro rata by
shares held at the moment of payment. A lot opened after the dividend gets
none of it.

Dividend tax character (qualified vs ordinary vs interest) is tagged in
`meta` at preprocessing time so `ledger/taxes.py` never sees broker-native
strings.

### Corporate actions

| Type | Effect | Notes |
|------|--------|-------|
| **SPLIT** | Multiplies open lot shares, divides cost/share | Ratio in `meta`; cash untouched |

**SPLIT is declared in the schema but not yet populated from IBKR** — no
corporate-actions section is pulled from the Flex Query today. When a source
appears, `brokers/ibkr/preprocessing.py` gains a mapping branch; the schema
doesn't change.

---

## Cash

Cash is tracked as a **plain running total**, not as lots. Every dollar is
identical — unlike a real symbol where different buys have different prices,
there is no cost-basis heterogeneity for FIFO to track.

```
portfolio_value(t) = Σ (shares × price) + cash_balance
```

Cash moves:

| Increases cash | Decreases cash |
|----------------|----------------|
| DEPOSIT, SELL, DIVIDEND | WITHDRAWAL, BUY, FEE, WITHHOLDING |

---

## Replay

**Module:** `ledger/replay.py` → `replay_ledger()`

Walks every event in chronological order and returns:

```
ReplayResult:
  open_lots    lot_id, symbol, opened_at, shares, cost_per_share, dividends_received
  closed_lots  + closed_at, exit_price, realized_gain, term, closed_by_event_id
  cash_balance float
```

The walk is a **sequential fold** — each event's effect depends on lots
left open by every prior event. This repo otherwise prefers vectorized
polars/numpy expressions over explicit loops, but that convention assumes
each row's computation is independent; here it deliberately isn't, so a
`for` loop is the honest shape for this one function rather than forcing
a vectorized expression to fake sequential state.

Per event type:

- **BUY** → opens a new `Lot`, reduces cash
- **SELL** → `lots.consume_fifo()` closes oldest lots first, increases cash.
  Each closed portion gets `realized_gain`, a `LONG`/`SHORT` term (≥ 365
  days held = LONG), and a link to the closing event
- **DEPOSIT / WITHDRAWAL / WITHHOLDING / FEE** → cash only
- **DIVIDEND** → increases cash, **and** calls `lots.accrue_dividend()` on every open lot of that symbol — see "Dividend allocation" below
- **SPLIT** → `lots.apply_split()` on open lots; cash untouched

To value the portfolio **as of a past date**, truncate the ledger to events
on or before that date, then replay. This is how daily portfolio values,
monthly P&L, and as-of snapshots all work.

---

## Lots

**Module:** `ledger/lots.py`

Each **BUY** opens one lot. Lots are consumed **FIFO** (oldest `opened_at`
first) on **SELL**.

### Open lot

A purchase that still has shares remaining. Gain is **unrealized** — it
changes with the current market price.

### Closed lot

The portion of a lot consumed by one SELL. Has its own exit price, holding
period, and **realized gain**:

```
realized_gain = shares × (exit_price − cost_per_share) − allocated_fees
```

A single SELL may close multiple lots or only part of one lot.

`allocated_fees` comes from `consume_fifo`'s `total_fees` parameter,
divided pro rata by shares consumed — and that parameter **defaults to
`0.0` and is never passed** on the replay path (`ledger.replay`), so in
practice the term is always zero today. That is deliberate rather than an
omission: a commission arrives as its own separate `FEE` event
(`ibkr:{transactionID}:fee`), precisely so it never inflates a lot's cost
basis or gets buried inside a realized gain. The parameter exists for a
future source that reports a sale's fee inline instead.

### Term

| Label | Condition |
|-------|-----------|
| LONG | Held ≥ 365 days before sale |
| SHORT | Held < 365 days |

This is a holding-period label for display and tax bucketing, not tax
advice.

### Dividend allocation

When a `DIVIDEND` event is replayed, `lots.accrue_dividend()` splits the
cash amount across every **open lot of that symbol at that moment**, in
proportion to shares held:

```
lot.dividends_received += dividend_amount × (lot.shares / total_shares_held)
```

Key rules:

- **Pro rata by shares, not cost basis.** A lot with 10 shares gets twice
  as much as a lot with 5 shares, regardless of what either cost.
- **Snapshot at pay date.** Only lots open at the moment the `DIVIDEND`
  event is processed get any allocation. A lot opened the next day gets
  nothing from that dividend, including a DRIP lot created by the
  dividend's own reinvestment `BUY`.
- **Carries through partial sells.** When `consume_fifo()` partially closes
  a lot, `dividends_received` splits proportionally:
  ```
  consumed_dividends = lot.dividends_received × consumed_shares / lot.shares
  ```
  The remaining open portion and the closed portion each keep their share.
- **Included in `lot_returns`, opt-in for `unrealized_gain`.**
  `metrics.lot_returns()` always puts `dividends_received` in the
  numerator, so a lot's total return accounts for all income received
  while it was open. `metrics.unrealized_gain()` takes
  `include_dividends`, which **defaults to `False`** — it is price
  appreciation alone unless a caller asks otherwise, and the one caller
  today (`dashboard.overview`) does not. The two figures therefore differ
  for any lot that has received a dividend, on purpose.

---

## Populating the ledger from IBKR

Today the ledger is populated by `brokers/ibkr/`:

| IBKR source | Ledger events |
|-------------|---------------|
| `<Trade>` rows | BUY, SELL, FEE |
| `<CashTransaction>` rows | DEPOSIT, WITHDRAWAL, DIVIDEND, WITHHOLDING, FEE |

See [ibkr_flex_api.md](ibkr_flex_api.md) for sync details and Flex Query
setup.

Both mappers live in `brokers/ibkr/preprocessing.py` and validate through
`LedgerEvent` before returning. Adding a second broker means adding
`brokers/{broker}/` with its own preprocessing — the ledger schema stays
the same.

---

## Storage

```
data/trades/brokers/ibkr/
  raw_statements/{timestamp}.xml   source of truth — every fetch, verbatim
```

The deduplicated ledger itself is a Postgres cache (`ledger_events`, one
row per event, per user — `brokers.ibkr.main.load_ledger`/`_write_ledger`),
derived from the raw archive above, not a flat file. If it's ever wrong,
`rebuild_from_raw_statements()` re-parses every archived statement from
scratch and rewrites it.

---

## Design rules

1. **Never store derived state as editable truth.** If you can compute it
   from the ledger, don't persist it separately.
2. **Declare all event types upfront.** Adding a case to a `Literal` in an
   empty design is free; retrofitting one into live metrics is not.
3. **Keep broker vocabulary out of ledger logic.** Map at preprocessing;
   tag semantics in `meta`.
4. **Validate at the boundary.** Every row entering the ledger passes
   through `LedgerEvent`.
