# Cash sitting

**Module:** `dashboard/cash_sitting.py`

Detects uninvested cash sitting idle in the account, and estimates what it's
missed out on by not being invested. Two things are built on top of the same
`CashLot` model: a single as-of-today snapshot (`cash_sitting_summary`, backs
`CashSittingCard`) and a full daily time series (`cash_received_counterfactual`,
backs the dashed overlay lines on `CashOverTimeChart`).

For plain-language definitions, see [glossary.md](glossary.md).

---

## The daily cash series

**Function:** `daily_cash_balances()`

Mirrors `valuation.daily_portfolio_values`'s event-date-then-forward-fill
walk (see [metrics_and_benchmarks.md](metrics_and_benchmarks.md)), but tracks
`ReplayResult.cash_balance` instead of the priced total — no price lookup
needed, since cash needs no pricing. Every other function on this page
starts from this one series: one `(date, cash)` row per calendar day.

---

## Cash lots: `open_cash_lots`

Cash is fungible — there's no real identity attached to which dollar is "the"
$500 that's been sitting since March versus the $200 that arrived
yesterday — so tracking "how long has this cash been sitting" needs *some*
simplifying model. The one used here: every dollar of cash is a `CashLot`
(`arrival_date`, `amount`) from the day it arrives until a later outflow
consumes it, oldest lot first (FIFO), exactly the way `ledger/lots.py`
already tracks share lots for cost basis.

```
if cash increases by Δ:
    push CashLot(today, Δ)
elif cash decreases by Δ:
    consume Δ from the front of the lot queue, oldest first
```

Any decrease consumes lots exactly, for its exact dollar amount — there's no
threshold deciding whether a drop was "big enough" to count as a real
deployment, unlike the reset-heuristic this replaced. `open_cash_lots()`
returns whichever lots are still open at the series' last day.

This model directly answers the two questions a single "sitting since"
scalar couldn't get right at the same time:

- **A fresh deposit landing on top of already-sitting cash gets its own
  lot**, dated from when it actually arrived — it never inherits older
  cash's arrival date, so it can't be reported as having missed growth it
  was never around for.
- **A partial withdrawal doesn't make the *leftover* cash look fresher.**
  Withdrawing money only removes dollars from the front of the queue; it
  can't reset the age of whatever's left, since that's still the same lot
  it always was.

---

## The snapshot: `cash_sitting_summary`

**Function:** `cash_sitting_summary()` · **Card:** `CashSittingCard.tsx`

Reports, as of today: how much cash is sitting, how long the *oldest* open
lot has been sitting, and what all of it would be worth now had each lot
grown at the portfolio's or benchmark's own rate since its own arrival date.

```
sitting_since = oldest open lot's arrival_date
days_sitting  = as_of − sitting_since
```

Reporting the oldest lot (the worst case) rather than a blend means a big
fresh deposit can never mask a small stale pocket of cash sitting right next
to it.

### Estimating missed earnings

Each lot's own time-weighted growth (see `charts.growth_of_100_chart`) is
applied from *its own* arrival date to `as_of`, then summed — not one
blended ratio applied to the whole balance:

```
hypothetical_value = Σ lot.amount × (index[as_of] / index[lot.arrival_date])
missed_earnings    = hypothetical_value − cash_usd
```

A dollar that arrived five minutes ago contributes ~$0 of missed earnings
this way, correctly — under the old blob heuristic it would have picked up a
share of whatever growth the *rest* of the balance had missed, however long
that had been sitting. `days_sitting` past `light_warning_days` (7) or
`heavy_warning_days` (14) sets the card's warning level.

---

## The chart series: `cash_received_counterfactual`

**Function:** `cash_received_counterfactual()` · **Chart:**
`CashOverTimeChart.tsx`

Built on `cash_lot_counterfactual()`, called once per index (the benchmark's
own adjusted price; a HYSA compounding index built by feeding a synthetic $1
deposit into `counterfactuals.hysa_counterfactual_series`). For every day, it
tracks two numbers per index — `live_usd` and `realized_usd` — rather than
one running total.

**Live** is the current worth of whichever lots are still open: bounded, and
tracking the real cash balance's own shape, since it's valuing the same
lots that make up `cash` at every point. It converges back to the real cash
balance whenever that balance hits $0.

**Realized** is a separate running total that only moves in discrete jumps —
one per lot consumption, sized to that slice's own growth from its arrival
date to the day it was consumed — then stays flat until the next
consumption. It only ever grows, and never resets, because it's the
permanent record of episodes that are already over.

```
on a decrease that consumes `amount` from a lot dated `lot_date`:
    realized += amount × (index[today] / index[lot_date] − 1)
```

### Why realized is frozen, not left compounding

An earlier version of this design let the leftover position keep compounding
indefinitely after the real money was deployed, treating that as a "lifetime
cost of ever having sat idle." That double-counts: once cash is actually
invested, the dollar chart's own counterfactuals (see
[metrics_and_benchmarks.md](metrics_and_benchmarks.md)) already track what
that money did against the benchmark/HYSA from the day it entered the
account — including whatever window it spent sitting first. Continuing to
grow a second shadow copy of the same money after deployment would count
that same span of performance twice, under two different names. Freezing the
gain at the moment of deployment keeps `realized_usd` cleanly scoped to just
the idle window's own drag, with nothing left to overlap.

`realized_usd` is deliberately not drawn as a third line on the same axes as
`cash`/`live_usd` — it's a fundamentally different-scaled quantity (a small
bounded cash balance vs. a total that can grow for as long as the account has
existed) that would flatten the cash line to a sliver once it grows large
enough. The card component instead shows it as a companion stat next to the
chart.
