# Return math

Implemented in `returns.py`, one function per step.

**1. Total return per trade** — `total_return_pct`

```
total_return_pct = (current_price - price_paid) / price_paid * 100
```

**2. Annualize it** — `annualized_return_pct`

So a 7-day gain and a 155-day gain are comparable on a "per year" basis.
Standard CAGR-style annualization: compound the observed return up to a full
year's rate.

```
annualized_return_pct = ((1 + total_return_pct / 100) ** (365 / days_held) - 1) * 100
```

`days_held == 0` returns NaN (nothing to annualize). `days_held < 0` raises —
that means `as_of` is before the trade date, which is a caller mistake, not
a value to quietly compute through. Very short holds still produce
large-looking numbers by design — a 1% gain in one day annualizes to well
over 1000%. That's correct, not a bug; see step 5 for why it isn't allowed
to dominate the portfolio-level number.

**3. HYSA benchmark over the same window** — `hysa_period_return_pct`

Not `4% * days/365` — a HYSA compounds too:

```
hysa_period_return_pct = ((1 + config.hysa_annual_rate) ** (days_held / config.annualization_days) - 1) * 100
```

`annual_rate` and the 365-day convention both come from the `ReturnsConfig`
passed in — see `docs/architecture.md` for why there's no hardcoded default
buried in the function itself.

**4. Alpha** — `total_return_pct - hysa_period_return_pct`, computed inline
in `build_returns_table` as `alpha_period_pct`: your return minus what cash
would've earned over that identical window.

**5. Combine into one portfolio number** — `portfolio_alpha_pct`

Dollar-weighted average of `alpha_period_pct` across trades, weighted by
`usd_spent`. Deliberately uses *period* alpha, not annualized alpha:
annualizing a 1-day trade produces absurd numbers (e.g. a same-day trade
showing hundreds of percent), and blending those into a weighted average
would let noise dominate. Period alpha stays honest regardless of hold
length.

The per-trade *annualized* return is still worth plotting (see
`visualization.plot_return_curve`) — just not averaging.
