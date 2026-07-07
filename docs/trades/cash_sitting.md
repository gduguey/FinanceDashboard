# Cash sitting

**Module:** `dashboard/cash_sitting.py`

Detects uninvested cash sitting idle in the account, and estimates what it's
missed out on by not being invested. Two independent things are built on top
of the same daily cash series: a single as-of-today snapshot
(`cash_sitting_summary`, backs `CashSittingCard`) and a full daily time
series (`cash_received_counterfactual`, backs the dashed overlay lines on
`CashOverTimeChart`). They answer related but different questions, and each
has its own known simplification — see below.

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

## The snapshot: `cash_sitting_summary`

**Function:** `cash_sitting_summary()` · **Card:** `CashSittingCard.tsx`

Reports, as of today: how much cash is sitting, how long it's been "sitting
since," and what that cash would be worth now had it grown at the
portfolio's or benchmark's own rate since then instead.

### Finding "sitting since"

**Function:** `sitting_since_date()`

Walks the daily cash series day by day. The "sitting since" date only moves
forward when cash **drops** by at least `decrease_threshold_pct` (20% by
default, see `CashSittingConfig`) relative to the *immediately preceding
day's* balance — a real deployment into an investment, big enough to look
deliberate rather than noise. An increase, of any size, never moves it.

```
if cash[t] < cash[t-1] * (1 - decrease_threshold_pct):
    sitting_since = t
```

**This tracks one scalar for the whole cash balance, not per-dollar
history.** Cash is fungible — there's no real identity attached to which
dollar is "the" $500 that's been sitting since March versus the $200 that
arrived yesterday — so some simplifying assumption is unavoidable. The one
here has a specific, known bias: because only decreases reset the clock, a
fresh deposit landing on top of an already-sitting balance (without a
qualifying drop happening first) inherits the *old* sitting-since date. The
newly-arrived money is then reported as having been sitting, and missing
out on growth, since before it existed. `test_cash_sitting.py`'s
`test_sitting_since_date_does_not_reset_on_an_increase_after_a_decrease`
exercises this directly.

In practice this means the reported "days sitting" and "missed earnings"
skew toward *overstating* staleness whenever new cash arrives while older
cash is already idle — never toward understating it in the same way, since
a genuine 20%+ drop always does reset the clock exactly.

### Estimating missed earnings

Once `sitting_since` is known, today's cash total is scaled by how much the
portfolio's and benchmark's own growth-of-100 index (see
`charts.growth_of_100_chart`) moved between that date and today:

```
hypothetical_value = cash_usd × (index[today] / index[sitting_since])
missed_earnings    = hypothetical_value − cash_usd
```

This is **not** a replay of a specific hypothetical purchase — it's "what
this cash would be worth now had it grown at the same rate as everything
else already invested," applied as a single multiplier to the current
blob. `days_sitting` past `light_warning_days` (7) or `heavy_warning_days`
(14) sets the card's warning level.

---

## The chart series: `cash_received_counterfactual`

**Function:** `cash_received_counterfactual()` · **Chart:**
`CashOverTimeChart.tsx`

Answers a different, chart-shaped question: "if every dollar of cash had
been invested (in the benchmark, or at the HYSA rate) the moment it
arrived, instead of ever sitting, what would that be worth on any given
day?" — reusing the same `benchmark_counterfactual_series` /
`hysa_counterfactual_series` engine the Performance page's dollar chart
runs on (see [metrics_and_benchmarks.md](metrics_and_benchmarks.md)).

`cash_inflows()` turns the daily cash series into one virtual "deposit" per
day cash **increased** — a decrease produces no flow at all, on the theory
that money deployed into a real investment was already "invested" in the
counterfactual world from the day it arrived, so there's nothing for a
later decrease to undo there.

### Known limitation: this line never comes back down

Because only increases generate a flow, the counterfactual is a monotonic
running total of *every dollar ever received*, growing at the benchmark's
or HYSA's rate forever — it has no way to reflect that a given dollar
stopped sitting once it was actually invested. Once a meaningful share of
lifetime cash has been deployed into real positions, the gap between this
line and the (much smaller) real cash balance stops measuring "the cost of
cash sitting right now" and starts measuring something closer to "the cost
of never having any cash at all," which isn't the question the chart is
trying to answer.

The fix under consideration — not yet implemented — is to make the flows
symmetric: feed the counterfactual the daily cash **delta** in both
directions (a decrease becomes a virtual withdrawal, at that day's price,
of the same dollar amount), the same convention `benchmark_counterfactual_series`
already uses for real withdrawals elsewhere. That bounds the counterfactual
to track real cash's own shape, and — because a withdrawal sells shares at
whatever price they've grown to, not the original share count — it also
leaves a small residual position behind representing exactly the growth
that already accrued during that sitting window, which then keeps
compounding on its own. That residual is arguably a feature rather than a
bug: it's the permanent, realized cost of a sitting episode that's already
over, as distinct from the growth still accruing on cash that's sitting
right now — two different numbers this design would surface for free,
without needing `sitting_since_date`'s reset heuristic at all.
