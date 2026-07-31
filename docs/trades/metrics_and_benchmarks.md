# Metrics and benchmarks

All performance math lives in `src/trades/ledger/` — `metrics.py`, `nav.py`,
and `counterfactuals.py`. `dashboard/` composes these into the chart series
and card values the API serves.

For plain-language definitions of every term, see [glossary.md](glossary.md).

---

## The replay mindset

Nothing is stored as a pre-computed result. Every metric is rebuilt from
the ledger on demand:

1. **Replay** the ledger chronologically → open lots, closed lots, cash
   balance (`ledger/replay.py`).
2. **Price** holdings with a `price_lookup(symbol, date)` callable wired
   to the on-disk cache (`dashboard/valuation.py`).
3. **Compute** the metric on that state.

If a number looks wrong, there is exactly one place to look: the ledger
events and the replay logic.

---

## Portfolio value

```
portfolio_value = Σ (shares × price) + cash_balance
```

Cash is a plain running total, not a lot — every dollar is identical. See
[ledger.md](ledger.md) for how each event type moves cash.

---

## XIRR (money-weighted return)

**Module:** `ledger/metrics.py` → `xirr()`

The annual rate `r` that satisfies:

```
Σ  cashflow_i / (1 + r)^(days_i / 365) = 0
```

Cash flows are **external only**: deposits and withdrawals. Trades
between holdings are internal — no money crossed the portfolio boundary.

The terminal value is today's portfolio value (positive inflow from the
solver's perspective).

**Provisional flag:** if history spans less than 365 days
(`config.returns.annualization_days`), the dashboard labels XIRR as
provisional — short periods annualize into misleadingly large numbers.

---

## TWR (time-weighted return)

**Module:** `ledger/nav.py` → `time_weighted_return()`

Answers "how did the investments perform?" independent of when money was
added or removed.

Built in two steps:

1. **`nav_series()`** converts a daily portfolio value series into NAV per
   unit and units outstanding. Each deposit buys units at the current NAV;
   each withdrawal redeems units. NAV only moves with investment
   performance, never with cash flows.

   ```
   NAV(t) = value_pre_flow(t) / units_outstanding(t)
   ```

2. **`time_weighted_return()`** chains sub-period returns between external
   cash flows:

   ```
   TWR = Π (NAV_end / NAV_start) − 1   across each inter-flow period
   ```

Both raw and annualized TWR are reported.

### Timing gap

```
timing_gap = XIRR − TWR (annualized)
```

Positive → contribution timing helped. Negative → timing hurt.

---

## NAV and growth of $100

**Module:** `ledger/nav.py`

| Function | What it produces |
|----------|------------------|
| `nav_series()` | Daily NAV per unit + units outstanding |
| `growth_of_100()` | Any price/index series reindexed to 100 at its start |
| `period_pnl()` | Market gain for a date window (value change minus net deposits) |

The growth-of-$100 chart uses NAV for the portfolio line, and pure indices
(not contribution-matched counterfactuals) for benchmark, HYSA, and CPI.
See `dashboard/charts.py` → `growth_of_100_chart()` for why.

---

## Max drawdown

**Module:** `ledger/metrics.py` → `max_drawdown()`

Largest peak-to-trough decline in the NAV series:

```
drawdown(t) = NAV(t) / max(NAV up to t) − 1
max_drawdown = min(drawdown(t))
```

Reported as a percentage (0 or negative).

---

## Lot-level metrics

**Module:** `ledger/metrics.py`

| Function | Output |
|----------|--------|
| `lot_returns()` | Per open lot: current price, days held, raw return %, annualized return % |
| `symbol_metrics()` | Per symbol rollup: realized + unrealized gain, XIRR, return |
| `realized_gain_total()` | Sum of closed-lot realized gains |
| `unrealized_gain()` | Sum of open-lot mark-to-market gains |

### Lot return

```
raw_return = (shares × current_price + dividends_received) / (shares × cost_per_share) − 1
```

### Annualized lot return

```
annualized = (1 + raw_return)^(365 / days_held) − 1
```

Only shown when `days_held ≥ 365` — shorter holds produce unstable
annualized values that are mostly noise.

### Excess return vs HYSA

For closed lots, the dashboard also computes:

```
excess_return_vs_hysa_pct = total_return_pct − hysa_period_return_pct
```

where `hysa_period_return_pct` compounds daily at the rate
`dashboard.settings.hysa_rate_lookup` returns for each day of the lot's
actual holding window — the user's selected bank, their fixed-rate
override, after tax when tax is enabled. This is a final, non-provisional
number, and it is a difference between two percentages rather than alpha:
no risk adjustment is applied.

---

## Counterfactuals

**Module:** `ledger/counterfactuals.py`

A counterfactual replays the **same deposit and withdrawal history** using
a different investment. Dates and amounts stay fixed; only where the money
went changes.

| Function | Scenario |
|----------|----------|
| `hysa_counterfactual_series()` | Every external flow went into a HYSA instead |
| `hysa_counterfactual_value()` | HYSA balance as of one date |
| `benchmark_counterfactual_series()` | Every deposit bought the benchmark; every withdrawal sold shares |

All three live in `trades/ledger/counterfactuals.py`.

### HYSA counterfactual

Each deposit grows a virtual savings balance; each withdrawal shrinks it.
The balance compounds daily at `rate_lookup(date)` (from
`market_data/hysa_rates.py` or a fixed override).

### Benchmark counterfactual

Uses **adjusted** prices (see [market_data.md](market_data.md)). Dividends
are implicitly reinvested through the adjusted price series.

```
shares(t) = Σ(deposit / benchmark_price) − shares sold on withdrawals
value(t)  = shares(t) × benchmark_price(t)
```

### Excess value vs HYSA

```
excess_value_vs_hysa_usd = portfolio_value − hysa_counterfactual_value
```

A difference in dollars, not a return, and not alpha — nothing here
adjusts for risk. The closed-lot table's `excess_return_vs_hysa_pct` is
the percentage-point sibling of this, over one lot's own holding window,
and both read the same rate from `dashboard.settings.hysa_rate_lookup`.

When the tax toggle is on, the HYSA rate is after-tax (see
`ledger/taxes.after_tax_rate_lookup`).

---

## Monthly P&L decomposition

**Module:** `dashboard/charts.py`

Each calendar month is split into:

- **Contributions** — net external deposits minus withdrawals
- **Market gain** — everything else (price changes + dividends)

```
market_gain = value_end − value_start − net_contributions
```

A per-symbol variant attributes each symbol's net BUY−SELL as its
"contribution," with the remainder going to a CASH row. The two
attributions reconcile exactly with the portfolio-level monthly P&L.

---

## What is *not* a contribution

| Event | Counts as external cash flow? |
|-------|-------------------------------|
| DEPOSIT | Yes |
| WITHDRAWAL | Yes |
| BUY / SELL | No — internal reallocation |
| DIVIDEND | No — stays inside the account |
| DRIP reinvestment BUY | No — flagged via `meta["drip_reinvestment"]` |
| FEE | No |

This is why XIRR and the contribution line on the dollar chart only react
to deposits and withdrawals, not to trades or dividends.
