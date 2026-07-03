# Portfolio Dashboard — Implementation Spec

Sections are ordered by dependency: each section only relies on things built in earlier sections. Work top to bottom. Math is specified precisely; UI sections explain the "why."

---

## PHASE 0 — Data Model (everything else derives from this)

### 0.1 Append-only transaction ledger (source of truth)

Replace any "current position" state with an immutable ledger. Every row:

```
{ id, date, symbol, type, shares, price, amount, currency, meta }
```

Transaction types (all required, add them now even if unused yet):

- `DEPOSIT` / `WITHDRAWAL` — **external** cashflows (money crossing the portfolio boundary)
- `BUY` / `SELL` — **internal** cashflows (money moving between cash and holdings)
- `DIVIDEND` — cash distribution received (recorded on **pay date**, using the actual broker amount, never estimated from yield)
- `WITHHOLDING` — tax withheld at source on a dividend (see 0.6)
- `FEE` — commissions, account fees
- `SPLIT` — corporate action: `{symbol, ratio}`; on replay, multiplies share counts of all open lots of that symbol by `ratio` and divides their cost/share by `ratio`. Without this, a 2:1 split renders as a fake −50% crash.

**Rule:** positions, cost basis, gains, XIRR, NAV, every chart — all computed by replaying the ledger. Nothing derived is ever stored as editable state.

**Definition (critical, used everywhere):**
```
total_invested := net external contributions = Σ DEPOSIT − Σ WITHDRAWAL
```
Never define it as Σ(buys). Sum-of-buys double-counts recycled sale proceeds after any reallocation.

### 0.2 Cash as a pseudo-position (this also covers "include my cash holdings")

Model a `CASH` position inside the portfolio:

- `DEPOSIT` → +cash; `WITHDRAWAL` → −cash
- `BUY` → −cash, +shares; `SELL` → +cash, −shares
- `DIVIDEND` → +cash (net of withholding; see 0.6)
- `FEE` → −cash

```
portfolio_value(t) = Σ_symbols shares(t) × price(t) + cash(t)
```

Consequences:
- Selling on Monday and buying Wednesday produces **no cliff** in the value chart — value stays continuous because proceeds sit in cash.
- Cash appears as its own slice in the allocation view and its own row in per-symbol rollups.
- Optional: if the cash sits in an interest-bearing sweep/HYSA, record interest as `DIVIDEND` rows on the `CASH` symbol so cash total-return is tracked like any other position.

### 0.3 Lot tracking and lot consumption on sells

- Each `BUY` opens a lot: `{lot_id, symbol, open_date, shares, cost_per_share}`.
- Each `SELL` must map explicitly to the lots it consumes. Default rule: **FIFO**, but store the mapping (`sell_id → [(lot_id, shares_consumed)]`) so specific-lot identification is possible.
- Partially consumed lots split into a closed portion and an open remainder.
- Closed (portion of a) lot gets: `{exit_date, exit_price}` and
```
realized_gain = shares_consumed × (exit_price − cost_per_share) − allocated_fees
```
- Tag every closed lot: `term = LONG if (exit_date − open_date) ≥ 365 days else SHORT`.
- Maintain running totals: `realized_gain_ST_ytd`, `realized_gain_LT_ytd` (per calendar year).

### 0.4 Internal vs. external cashflows (the distinction that drives every metric)

- **External:** `DEPOSIT`, `WITHDRAWAL`. These are the ONLY flows that enter portfolio-level XIRR, the counterfactual engines, and the unit mint/burn logic (Phase 3).
- **Internal:** `BUY`, `SELL`, `DIVIDEND`, `FEE`, `SPLIT`. These never touch portfolio-level XIRR, never move the contributions step-line, never mint units. A reallocation (sell X → buy Y) contributes **zero** external cashflow; its effect shows up only through subsequent portfolio value.

### 0.5 Price data rules

- **Own positions:** raw (unadjusted) prices + explicit `DIVIDEND` ledger rows. Never compute `return = adjusted_close_today / raw_price_paid` — mixing the two series silently corrupts every return.
- **Benchmark counterfactuals:** adjusted / total-return (dividend-reinvested) series. The all-VOO counterfactual **must** use the dividend-reinvested series, otherwise the comparison flatters you by ~1.3%/yr.
- **Weekends/holidays:** carry forward the last known price. Never interpolate.

### 0.6 Tax-aware ledger fields (cheap now, painful to retrofit)

- Dividends arrive net of withholding for an NRA (15% with W-8BEN treaty claim on file, 30% otherwise). Record **two ledger lines**: gross `DIVIDEND` + negative `WITHHOLDING`. Otherwise reconciliation (6.9) never ties to broker statements, and tax drag is invisible.
- Per-symbol static tag `tax_character`: `QUALIFIED_DIVIDEND` (VOO, VXUS, QQQM) vs `ORDINARY_INTEREST` (BND, CASH interest).
- Config value `residency_status_change_date` (F-1/NRA → resident alien). Every realized-gain, dividend, and interest report must be splittable on this date.

---

## PHASE 1 — Correctness Fixes (do before adding any new metric)

### 1.1 Total return including distributions (the biggest single number-mover)

Per open lot:
```
raw_return = (shares × current_price + dividends_received_for_lot) / (shares × cost_per_share) − 1
```
where `dividends_received_for_lot` = the lot's pro-rata share of each `DIVIDEND` on that symbol paid while the lot was open (pro-rata by shares held on the dividend's record/pay date). Use **gross** dividends for the pre-tax view (withholding shown separately).

Pitfalls:
- Without this, BND (whose return IS its distributions) shows permanently red, and all equity positions are understated.
- Do not approximate dividends from yield; use actual ledger amounts (0.1).

### 1.2 Annualization gating

- Rule (GIPS convention): **never display an annualized return for a holding period < 365 days.**
- For lots ≥ 365 days:
```
annualized = (1 + raw_return)^(365 / days_held) − 1
```
- For lots < 365 days: show `raw_return` + `days_held` only; annualized column blank/`—`.
- The annualized-return scatter: delete the regression line (it is fit to annualization noise, whose variance explodes as days_held → 0). If keeping the chart, re-plot with **raw return** on the y-axis; the 4% HYSA reference then becomes the curve
```
hysa_ref(days) = 0.04 × days / 365      (simple)  or  (1.04)^(days/365) − 1  (compounded)
```

### 1.3 Chart data bugs

- Investment timeline x-axis starts Apr 10 but first trade is Jan 27 — fix the range to start at the first ledger date.
- Monthly invested bars: render every calendar month on the axis, including zero months (Feb, Mar), as explicit zero bars. Skipping them hides missed contributions — the exact thing the chart exists to show.

---

## PHASE 2 — Core Metrics (require Phase 0 ledger + Phase 1 dividends)

### 2.1 Portfolio XIRR (money-weighted return) — headline metric

Cashflow set = **external flows only** (0.4):
- each `DEPOSIT` amount as a negative flow on its date
- each `WITHDRAWAL` as a positive flow on its date
- terminal positive flow: `portfolio_value(today)` (holdings + cash) on today's date

Solve for `r` in:
```
Σ_i  CF_i / (1 + r)^((d_i − d_0)/365) = 0
```
Numerically: Newton–Raphson with bisection fallback; guard against no-sign-change and multiple-root cases (rare with all-negative-then-terminal-positive flow patterns, but handle non-convergence gracefully).

Properties to preserve:
- Sells/reallocations do NOT change the input set — the metric is unchanged by internal trades by construction.
- Display rule: with < 12 months of history, show the **cumulative (raw) figure** or clearly label the annualized XIRR "provisional."

### 2.2 Counterfactual engine (write once, reuse for every benchmark)

Every counterfactual is **just another ledger** replayed through the same valuation code. Input: the real ledger's external flows. Each benchmark defines what those flows buy:

**(a) HYSA counterfactual**
- Each `DEPOSIT` adds to a virtual balance; balance compounds daily:
```
balance(t+1) = balance(t) × (1 + rate(t)/365) + deposit(t) − withdrawal(t)
```
- `rate(t)` must be a **time series**, not a hardcoded 4% (rates float with the Fed). Alternative that removes the assumption entirely: buy a T-bill ETF (SGOV/BIL) **total-return** series with the same flows — then the counterfactual is mechanical price data.
- Optional after-tax toggle (Phase 5.1).

**(b) All-VOO counterfactual**
- Same external flows buy VOO on each deposit date, using the **dividend-reinvested (adjusted)** series (0.5).
- (VT variant identical.)

Outputs from any counterfactual:
```
dollar_alpha = portfolio_value(today) − counterfactual_balance(today)   ← headline card
rate_alpha   = portfolio_XIRR − counterfactual_XIRR                     ← secondary
```
Note: counterfactual XIRR ≠ its nominal rate (e.g., HYSA XIRR ≠ exactly 4%) because of cashflow timing — compute it, don't assume it.

This replaces the current "Alpha vs 4% HYSA: −0.22%" card, which is an equal-weighted average of per-trade alphas (an $85 lot counts the same as a $3,000 lot; a 1-day lot the same as a 155-day lot). The XIRR/counterfactual construction is size- and time-weighted automatically.

### 2.3 CPI reference

Fetch a CPI index series; render as a reference line on the growth-of-$100 chart (3.3) so real vs. nominal growth is visible. No further math.

### 2.4 Realized + unrealized gain split

```
total_gain      = realized_gain + unrealized_gain
realized_gain   = Σ closed-lot realized_gain (immutable, from ledger, 0.3)
unrealized_gain = Σ open lots: shares × (current_price − cost_per_share)  [+ dividends if quoting total return]
```
Pitfall: after any sell, naive `current_value − total_invested` breaks unless `total_invested` = net external contributions (0.1).

### 2.5 Per-symbol metrics (sells ARE cashflows at this level)

Per-symbol XIRR/return cashflows:
- each `BUY` of the symbol → negative flow
- each `SELL` proceeds → positive flow
- each `DIVIDEND` → positive flow
- terminal: current value of remaining shares

A fully closed symbol gets a final, frozen realized return (no terminal-value term). Per-symbol lifecycle stats: invested (external cost), proceeds received, dividends received, current value, realized + unrealized gain, status open/closed.

---

## PHASE 3 — Unitization / NAV / TWR (requires 0.2 cash position + 1.1 dividends)

### 3.1 The NAV (unit) method

- At inception: `NAV = 100`, `units = initial_value / 100`.
- Daily: `NAV(t) = portfolio_value(t) / units_outstanding(t)`.
- On each **external** flow (only these — 0.4):
```
units_minted = deposit / NAV_pre_flow        (withdrawal burns units the same way)
```
- **Ordering bug to avoid:** compute the day's NAV **before** applying that day's contribution. Applying the flow first pollutes the unit price and drags TWR back toward the contribution-inflated number.
- **Dividends are internal:** they raise NAV (cash goes up), they never mint units. This is why the CASH pseudo-position is a prerequisite.
- Weekends/holidays: carry forward last price (0.5).

### 3.2 Time-weighted return (TWR)

Falls out of the NAV series:
```
TWR(period) = NAV_end / NAV_start − 1
```
(equivalently the compounded product of sub-period returns between external flows). Same annualization gate as 1.2: don't annualize under 12 months. Expect it to look unimpressive next to "total gain" at ~5 months of history — that is the contribution illusion being removed, not an error.

### 3.3 Growth-of-$100 chart

Plot, from the same start date, indexed to 100:
- your NAV series
- VOO total-return growth-of-100
- HYSA growth-of-100 (rate series compounded)
- CPI

Because everything is per-unit, no cashflow matching is needed — the only chart where benchmarks overlay directly.

### 3.4 XIRR vs TWR gap (free once both exist)

```
timing_effect = XIRR − TWR
```
- `TWR > XIRR` → contribution timing hurt; `XIRR > TWR` → timing helped. For fixed monthly DCA the gap should be small; display as a small secondary stat.

### 3.5 Period P&L decomposition (accounting identity)

For any month/year:
```
market_gain(period) = value_end − value_start − net_external_flows(period)
```
Powers the "grew $1,240 in June, of which $1,150 was deposits and $90 actual gain" display, and the stacked monthly bar chart (6.4).

---

## PHASE 4 — Sell / Reallocation Support (requires 0.3 lots + 2.2 engine)

### 4.1 Behavior of existing metrics on a sell (verification checklist, no new math)

- Portfolio XIRR: unchanged (external flows only).
- HYSA / all-VOO counterfactuals: unchanged — which means the gap now *includes* reallocation quality. Ensure the engine subscribes only to external flows, never trades.
- Contributions step-line: does not move.
- NAV/units: unchanged (internal flow — no mint/burn).
- Per-symbol metrics: sell proceeds enter as inflows (2.5).

### 4.2 Decision counterfactual ("what if I hadn't sold")

On each reallocation, snapshot a shadow ledger that skips the sell and subsequent buy (keeps holding the old symbol) and replay it forward through the same engine as 2.2:
```
decision_alpha(t) = actual_portfolio_value(t) − no-sell_counterfactual_value(t)
```
One number in dollars per reallocation decision, updating over time.

### 4.3 Wash-sale flag (mechanical check, not advice)

Trigger a warning when:
```
a SELL realizes a loss on symbol S
AND a BUY of S or a flagged substantially-similar/correlated symbol
occurs within [sell_date − 30d, sell_date + 30d]
```
Maintain a small user-editable similarity map (e.g., pairs of S&P 500 funds). The dashboard flags; it does not rule on "substantially identical" — that determination is for a tax reference/professional.

### 4.4 Realized-gains report

Per calendar year: `Σ realized ST gains`, `Σ realized LT gains` (from 0.3 tags), additionally **split by the residency-status-change date** (0.6) since the tax regime differs on each side.

### 4.5 Pre-sale preview

At sell time, surface per candidate lot: age (days), unrealized gain/loss, ST/LT flag if sold today, and the wash-sale check result. Measurement only; no lot recommendation.

---

## PHASE 5 — Tax-Adjusted Views (requires 0.6 + 2.2)

### 5.1 After-tax HYSA toggle

```
after_tax_rate(t) = rate(t) × (1 − marginal_ordinary_rate)
```
applied inside the HYSA counterfactual compounding. Crude by design — HYSA interest is taxed annually as ordinary income (for a resident) while ETF gains compound untaxed until sale, so the pre-tax comparison systematically understates the ETFs' edge. Exception to know: while NRA (F-1), US bank deposit interest is generally **exempt** — the toggle should respect the status-change date (rate × 1.0 before it, × (1−tax) after).

### 5.2 Status-dependent display rules (config-driven, keyed on `residency_status_change_date`)

- NRA side: dividends flat 15% (treaty, W-8BEN) or 30% withheld — surfaced via the WITHHOLDING rows (0.6); capital-gains regime is the fact-specific 183-day / treaty question (flag it, don't compute it).
- Resident side: dividends split by `tax_character` tag — qualified (VOO/VXUS/QQQM) vs ordinary (BND, cash interest); gains split ST/LT (0.3).
- Non-computational reminders worth a static checklist in the app: W-8BEN → W-9 swap at status change; FBAR/8938 if foreign accounts > $10k; PFIC warning for French funds/assurance-vie; timing of the reallocation sale relative to the status change is the item to take to a cross-border professional.

---

## PHASE 6 — UI / Dashboard (each item states the why)

### 6.1 Overview cards

Merge "Total invested," "Current value," "Total gain," "+0.14%" into **one Value card** (current value; subline: gain $ and %, split realized/unrealized per 2.4) — they are one fact spread across three cards, and the freed space goes to the numbers that answer real questions. New card set:
1. **Value** (with gain subline)
2. **XIRR** (provisional label < 12 mo)
3. **Dollar alpha vs HYSA counterfactual** (the honest replacement for −0.22%)
4. **TWR** (small/secondary), optionally with the XIRR−TWR timing gap
Remove "4 symbols" as alpha-card subtext — symbol count is not context for alpha.

### 6.2 The three-line dollar chart (the single most valuable addition)

One chart, three series: cumulative **contributions step-line** (moves only on external flows), **portfolio market value** (holdings + cash), **HYSA counterfactual balance**; add the **all-VOO counterfactual** as a fourth line. Why: the vertical distances answer "what's happening / am I beating cash / is the gap growing" at any date, making the scatter and several cards redundant. Add small **reallocation markers** ("sold X → bought Y") so kinks in the value line have context — and the step-line's non-movement at those markers visually teaches that reallocations are internal.

### 6.3 Growth-of-$100 chart (from 3.3)

Why alongside 6.2: the dollar chart shows *your money's* journey (money-weighted world); this shows *your strategy's* quality (time-weighted world), directly comparable to published fund numbers.

### 6.4 Monthly bars, upgraded

Keep as schedule-adherence view; render missing months as explicit zeros (1.3). Upgrade: stacked/paired bars per month — **contributions in grey, market P&L (3.5) in green/red**. Why: directly kills the "portfolio is up $12k (of which $11k was my paycheck)" illusion every month.

### 6.5 Allocation view

Slice by **current value** (including the cash slice), not invested dollars — invested-USD slices can't show drift. Add a user-defined **target allocation** and per-symbol drift (`actual% − target%`). Why a horizontal bar instead of a donut: target-vs-actual pairs read as aligned bars, not two donuts. Feature hook: since money arrives monthly, suggest directing the next contribution toward the most underweight symbol (measurement-driven, no advice needed).

### 6.6 Trade-level table

- Split into **Open lots** (current price, raw return, days held, accrued dividends) and **Closed lots** (exit date/price, final return, realized $, ST/LT flag, and vs-HYSA alpha over the actual window — now a legitimate finished number; annualized allowed if ≥ 365 days). Why: sold positions must not vanish — that's self-inflicted survivorship bias.
- Add **shares** and **cost** columns (size context is currently absent — an $85 lot and a $3,000 lot look identical).
- Add a **per-symbol rollup row**: total invested, current value, avg cost/share, dividends, total return $ and % (there is currently no per-symbol summary anywhere).
- Annualized column: gated per 1.2.

### 6.7 Risk stat

`max_drawdown = min over t of (NAV(t) / max_{s≤t} NAV(s) − 1)` shown as "largest peak-to-trough so far: −X%". Why this and not ratios: for buy-and-hold DCA the real risk is behavioral (panicking at a dip), and this is the number that calibrates it.

### 6.8 Look-through concentration panel

Using published ETF holdings: aggregate top underlying holdings weighted by your current-value allocation → show top-10 names, % in the largest single name, and US / international / bonds / cash split. Why: VOO and QQQM overlap heavily in the same mega-caps; ticker-level "4 symbols" overstates diversification. Measurement, not advice.

### 6.9 Trust & data-quality layer

- **Monthly reconciliation:** dashboard value vs broker statement, per symbol, to the cent; visible warning on drift. Why first-class: every data bug (adjusted-price mixing, missed dividend, missed split, withholding mismatch) announces itself here first.
- **Export** the ledger (CSV/JSON, one click). Why: it's your financial history; it must not live only in a local DB with no backup.
- **Data-quality corner:** last successful price sync per symbol, missing dividend records, reconciliation status. Why: a monitoring tool you can't trust is worse than none.

### 6.10 Anti-overmonitoring defaults

- Default every view's time range to "since inception" or "1Y", never "today".
- Make the daily change deliberately de-emphasized/hard to find (no big green/red number up top).
Why: monthly DCA + daily watching = the one failure mode (flinching at drawdowns and breaking the plan). The dashboard's job is the five-minute monthly ritual: this month's contribution → drift vs target → gap vs counterfactuals → done.

---

## Dependency summary (build order)

```
0.1 ledger ─┬─ 0.2 cash ─── 3.1 NAV/units ── 3.2 TWR ── 3.3 growth-of-100 ── 3.4 gap
            ├─ 0.3 lots ─── 4.3 wash-sale, 4.4 gains report, 4.5 preview
            ├─ 0.5 prices ─┬ 1.1 total return ── 1.2 annualization gate
            │              └ 2.2 counterfactuals ── 2.3 CPI, 4.2 decision CF, 5.1 after-tax
            ├─ 0.6 tax fields ── 5.1/5.2
            └─ 0.4 int/ext ── 2.1 XIRR ── 2.2 alphas
1.3 chart bugs: independent, do immediately
2.4, 2.5: after 0.3 + 1.1
Phase 6: each item after the metric it displays
```