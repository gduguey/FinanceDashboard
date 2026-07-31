# Glossary

Short, plain-language explanations for the dashboard's technical terms:
what each concept is, what it guards against, and — where it matters —
the formula and exactly what's included or excluded on this dashboard.

These definitions mirror the tooltips in the web UI (`web/src/lib/glossary.ts`).

---

## Portfolio-level metrics

### XIRR

The annual return that matches every deposit, withdrawal, and today's
portfolio value. It measures the return on the actual money that went into
the account. Guards against judging performance only from today's balance.

Only deposits and withdrawals count as cash flows; trades between holdings
do not.

**Formula:** Σ cashflow ÷ (1+r)^(days÷365) = 0

### XIRR (provisional)

Shown when there is less than 12 months of history. Annualizing a short
period can turn small gains or losses into very large-looking yearly
returns. Treat this number as an early estimate until a full year has passed.

### TWR — Time-Weighted Return

The portfolio's investment return with deposits and withdrawals removed. It
answers "how did the investments perform?" regardless of when money was
added. Guards against contribution timing making performance look better or
worse.

Built by chaining NAV_end ÷ NAV_start − 1 across the periods between cash
flows.

### Timing gap (XIRR − TWR)

The difference between the money-weighted return (XIRR) and the investment
return (TWR). A positive value means deposit and withdrawal timing helped.
A negative value means timing hurt, such as adding money before a market
drop.

### NAV — Net Asset Value (per unit)

The value of one portfolio unit. Deposits buy more units and withdrawals
sell units, so the unit price changes only because the investments gained
or lost value. This separates investment performance from money moving in
or out.

**Formula:** NAV(t) = portfolio value(t) ÷ units outstanding(t)

### Growth of $100

Shows what $100 invested on day one would be worth today with no later
deposits or withdrawals. Every line starts at 100 so investment
performance can be compared directly. Unlike the dollar chart, contribution
timing is ignored.

### Portfolio value

The total value of the account today. It includes every holding at its
current market price plus any cash in the account, including uninvested
deposits and cash dividends.

### Contributions

Cumulative deposits minus withdrawals. Only money entering or leaving the
account counts. Dividends and trades between holdings are excluded because
no money entered from outside the account.

### Market gain

The change in value that came from investment performance rather than
deposits or withdrawals.

**Formula:** ending value − starting value − net deposits

Includes both price changes and dividends.

### Largest peak-to-trough so far

The largest drop in NAV from its highest value up to that point. It
measures the worst decline experienced before reaching a new high.

**Formula:** min over time of NAV(t) ÷ (highest NAV seen so far) − 1

### Drift

The difference between a holding's current portfolio weight and its target
weight. Positive means the holding is above its target allocation.
Negative means it is below.

---

## Benchmarks and counterfactuals

### Counterfactual

A replay of the same deposit and withdrawal history using a different
investment. The dates and amounts stay the same; only where the money was
invested changes. This compares decisions using the exact same contribution
schedule.

### HYSA counterfactual

Shows what the same deposits and withdrawals would be worth today if they
had gone into a high-yield savings account instead. Interest compounds daily
using the configured rate. This compares investing with simply keeping the
money in cash.

### Benchmark counterfactual

Shows what the same deposits and withdrawals would be worth if every
deposit bought the benchmark fund (VOO by default) and every withdrawal
sold shares on that day. Dividends are reinvested. This compares the
portfolio with buying the benchmark using the exact same cash-flow schedule.

**Formula:** shares(t) = Σ(deposit ÷ benchmark price) − shares sold;
value(t) = shares(t) × benchmark price(t)

### Benchmark

The benchmark fund (VOO by default), shown as a growth-of-$100 index. It is
not matched to the account's deposits or withdrawals. It simply shows how
the benchmark itself performed over time.

### Excess value vs. HYSA

Today's portfolio value minus today's HYSA counterfactual value. A positive
value means investing produced more dollars than leaving the same deposits
in a high-yield savings account. It is a difference in dollars, not a
return, and not alpha: nothing here adjusts for how much more risk the
portfolio took to get there.

### CPI

The Consumer Price Index, a common measure of inflation. It is indexed to
100 like the other lines so investment growth can be compared with
inflation over the same period.

---

## Lots and trades

### Ledger

The complete history of every deposit, withdrawal, buy, sell, and dividend,
stored in one common format. Every calculation on this dashboard is
rebuilt from this history, so nothing is stored as pre-computed results.

### Lot

One purchase of shares. Each purchase is tracked separately because
purchase date, purchase price, holding period, and gains can all be
different. When shares are sold, the oldest lots are used first (FIFO).

### Open lot

A purchase that still has shares remaining. Its gain or loss is unrealized
and changes with the current market price.

### Closed lot

The part of a purchase that has been sold. It has its own sale price,
holding period, and realized gain. A single sale may close multiple lots or
only part of one lot.

### XIRR (per symbol)

This symbol's own money-weighted return: buys count negative, sells and
dividends count positive, and any withholding on those dividends counts
negative too. If the position is still open, its current value is added as
a final flow, as if sold today.

Unlike the portfolio-level XIRR above, trades count here — a symbol has no
deposits or withdrawals of its own to measure against.

### Return

The total return for this lot.

**Formula:** (current value + dividends received) ÷ purchase amount − 1

It is not adjusted for how long the lot has been held.

### Annualized return

The lot's return converted into an equivalent yearly rate.

**Formula:** (1 + raw return)^(365 ÷ days held) − 1

Only shown after a lot has been held for at least 365 days because shorter
periods produce unstable annualized values.

### Realized gain

Gain or loss that became final when shares were sold. It does not change
after the sale, regardless of future market prices.

### Unrealized gain

Gain or loss on shares that are still held. It changes as the market price
changes until the shares are sold.

### Term

LONG = held for at least 365 days before being sold. SHORT = held for less
than 365 days. This is only a holding-period label, not tax advice.

### Excess return vs. HYSA

This lot's return minus the return a high-yield savings account would have
earned over the same holding period, compounded daily at your configured
HYSA rate — the same rate the overview card uses. Because the lot is
closed, this value is final and will not change. It is a difference between
two percentages, not alpha: no risk adjustment is applied.

### DRIP (dividend reinvestment)

Shares bought automatically using a cash dividend. They are not counted as
new contributions because no new money entered the account. The additional
shares and their future gains are still included in portfolio performance.

---

## Tax

Taxes has its own top-level page now (`/investments/taxes`, two tabs:
Report and How it's taxed), separate from Performance and Allocation. The
regime/rate selector lives only there; Performance and Allocation only
carry the plain on/off "Apply taxes" switch (see Taxes (toggle) below).

### Tax regime

Which U.S. tax treatment applies: NRA (nonresident alien, e.g. F-1 student
status) generally owes no U.S. tax on bank interest or on security sales
at all, as long as you're present in the U.S. fewer than 183 days in the
tax year; RESIDENT (e.g. H-1B, once the substantial-presence test is met)
is taxed the same way a U.S. citizen is, on both. This app doesn't count
actual days present — picking a regime is a statement of which one
applies, not something it verifies.

### LTCG — Long-Term Capital Gains

The profit from selling a lot held longer than 365 days (see Term) —
taxed, under the RESIDENT regime, at this lower rate instead of the
regular marginal rate. A qualified dividend gets the same lower rate.
Never applies under NRA: those capital gains aren't U.S.-taxed at all,
regardless of holding period.

### Wash-sale

A loss sale is flagged when the same security — or one on a declared
similar-fund list — was bought back within 30 days before or after the
sale. This is just a mechanical proximity check, to surface the risk — it
doesn't disallow the loss in any number shown here, since that adjustment
lives on an actual tax return, not this dashboard.

### Taxes (toggle)

Shown on Performance and Allocation, not on the Taxes page itself (which
always shows its report regardless of this switch). Changes two things:
the HYSA line and rate switch from the published rate to an after-tax rate
(labeled "(after tax)"), and the overview's "Excess value vs. HYSA" card is
recomputed the same way; on Allocation, lot-level dividend figures switch
from gross to net of withholding, reflecting the actual cash received. XIRR
already reflects withholding either way, since it's a cash outflow recorded
in the ledger, not something this toggle changes. Portfolio value, TWR, and
the benchmark comparison are never taxed here — a gain sitting unsold owes
nothing.

### Estimated tax owed

Capital-gains tax on the year's net realized gain, plus dividend/interest
tax, at the rates set on the Taxes page — a resident pays the marginal
rate on short-term gains and ordinary income, the lower LTCG rate on
long-term gains and qualified dividends; a nonresident owes nothing on
gains or interest, and a flat rate on dividends (a tax treaty's negotiated
rate if W-8BEN is claimed, otherwise the default 30% statutory
withholding). A net loss in a bucket is floored at zero, not a rebate.
Balance due nets this estimate against tax already withheld by the broker.

### After-tax liquidation value

Today's portfolio value minus the capital-gains tax a full sale of every
open lot, right now, would trigger. A snapshot of what selling everything
today would actually leave you with, not a projection of any other date.
Shown at the top of the Taxes page's Report tab.
