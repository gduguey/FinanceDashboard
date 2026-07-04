// Short, plain-language explanations for the dashboard's technical terms:
// what it is, what it guards against, and — where it matters — the
// formula and exactly what's included/excluded on this specific dashboard.
// Keyed by GlossaryTerm and rendered via <InfoTooltip term="..." />.

export const GLOSSARY = {
  xirr: {
    title: 'XIRR',
    body: "The annual return that matches every deposit, withdrawal, and today's portfolio value. It measures the return on the actual money that went into the account. Guards against judging performance only from today's balance. Only deposits and withdrawals count as cash flows; trades between holdings do not. Formula: Σ cashflow ÷ (1+r)^(days÷365) = 0.",
  },
  xirrProvisional: {
    title: 'XIRR (provisional)',
    body: 'Shown when there is less than 12 months of history. Annualizing a short period can turn small gains or losses into very large-looking yearly returns. Treat this number as an early estimate until a full year has passed.',
  },
  twr: {
    title: 'TWR — Time-Weighted Return',
    body: "The portfolio's investment return with deposits and withdrawals removed. It answers 'how did the investments perform?' regardless of when money was added. Guards against contribution timing making performance look better or worse. Built by chaining NAV_end ÷ NAV_start − 1 across the periods between cash flows.",
  },
  timingGap: {
    title: 'Timing gap (XIRR − TWR)',
    body: "The difference between the money-weighted return (XIRR) and the investment return (TWR). A positive value means deposit and withdrawal timing helped. A negative value means timing hurt, such as adding money before a market drop.",
  },
  growthOf100: {
    title: 'Growth of $100',
    body: "Shows what $100 invested on day one would be worth today with no later deposits or withdrawals. Every line starts at 100 so investment performance can be compared directly. Unlike the dollar chart, contribution timing is ignored.",
  },
  nav: {
    title: 'NAV — Net Asset Value (per unit)',
    body: "The value of one portfolio unit. Deposits buy more units and withdrawals sell units, so the unit price changes only because the investments gained or lost value. This separates investment performance from money moving in or out. Formula: NAV(t) = portfolio value(t) ÷ units outstanding(t).",
  },
  cpi: {
    title: 'CPI',
    body: 'The Consumer Price Index, a common measure of inflation. It is indexed to 100 like the other lines so investment growth can be compared with inflation over the same period.',
  },
  counterfactual: {
    title: 'Counterfactual',
    body: 'A replay of the same deposit and withdrawal history using a different investment. The dates and amounts stay the same; only where the money was invested changes. This compares decisions using the exact same contribution schedule.',
  },
  hysaCounterfactual: {
    title: 'HYSA counterfactual',
    body: "Shows what the same deposits and withdrawals would be worth today if they had gone into a high-yield savings account instead. Interest compounds daily using the configured rate. This compares investing with simply keeping the money in cash.",
  },
  benchmarkCounterfactual: {
    title: 'Benchmark counterfactual',
    body: "Shows what the same deposits and withdrawals would be worth if every deposit bought the benchmark fund (VOO by default) and every withdrawal sold shares on that day. Dividends are reinvested. This compares the portfolio with buying the benchmark using the exact same cash-flow schedule. Formula: shares(t) = Σ(deposit ÷ benchmark price) − shares sold; value(t) = shares(t) × benchmark price(t).",
  },
  benchmarkIndex: {
    title: 'Benchmark',
    body: "The benchmark fund (VOO by default), shown as a growth-of-$100 index. It is not matched to the account's deposits or withdrawals. It simply shows how the benchmark itself performed over time.",
  },
  dollarAlphaHysa: {
    title: 'Dollar alpha vs. HYSA',
    body: "Today's portfolio value minus today's HYSA counterfactual value. A positive value means investing produced more dollars than leaving the same deposits in a high-yield savings account.",
  },
  portfolioValue: {
    title: 'Portfolio value',
    body: "The total value of the account today. It includes every holding at its current market price plus any cash in the account, including uninvested deposits and cash dividends.",
  },
  contributions: {
    title: 'Contributions',
    body: "Cumulative deposits minus withdrawals. Only money entering or leaving the account counts. Dividends and trades between holdings are excluded because no money entered from outside the account.",
  },
  marketGain: {
    title: 'Market gain',
    body: "The change in value that came from investment performance rather than deposits or withdrawals. Formula: ending value − starting value − net deposits. Includes both price changes and dividends.",
  },
  lotReturn: {
    title: 'Return',
    body: "The total return for this lot. Formula: (current value + dividends received) ÷ purchase amount − 1. It is not adjusted for how long the lot has been held.",
  },
  annualizedReturn: {
    title: 'Annualized return',
    body: "The lot's return converted into an equivalent yearly rate. Formula: (1 + raw return)^(365 ÷ days held) − 1. Only shown after a lot has been held for at least 365 days because shorter periods produce unstable annualized values.",
  },
  realizedGain: {
    title: 'Realized gain',
    body: "Gain or loss that became final when shares were sold. It does not change after the sale, regardless of future market prices.",
  },
  unrealizedGain: {
    title: 'Unrealized gain',
    body: "Gain or loss on shares that are still held. It changes as the market price changes until the shares are sold.",
  },
  lotTerm: {
    title: 'Term',
    body: 'LONG = held for at least 365 days before being sold. SHORT = held for less than 365 days. This is only a holding-period label, not tax advice.',
  },
  alphaVsHysa: {
    title: 'Alpha vs. HYSA',
    body: "This lot's return minus the return a high-yield savings account would have earned over the same holding period. Because the lot is closed, this value is final and will not change.",
  },
  drift: {
    title: 'Drift',
    body: "The difference between a holding's current portfolio weight and its target weight. Positive means the holding is above its target allocation. Negative means it is below.",
  },
  maxDrawdown: {
    title: 'Largest peak-to-trough so far',
    body: "The largest drop in NAV from its highest value up to that point. It measures the worst decline experienced before reaching a new high. Formula: min over time of NAV(t) ÷ (highest NAV seen so far) − 1.",
  },
  dripReinvestment: {
    title: 'DRIP (dividend reinvestment)',
    body: "Shares bought automatically using a cash dividend. They are not counted as new contributions because no new money entered the account. The additional shares and their future gains are still included in portfolio performance.",
  },
  ledger: {
    title: 'Ledger',
    body: "The complete history of every deposit, withdrawal, buy, sell, and dividend, stored in one common format. Every calculation on this dashboard is rebuilt from this history, so nothing is stored as pre-computed results.",
  },
  lot: {
    title: 'Lot',
    body: "One purchase of shares. Each purchase is tracked separately because purchase date, purchase price, holding period, and gains can all be different. When shares are sold, the oldest lots are used first (FIFO).",
  },
  openLot: {
    title: 'Open lot',
    body: "A purchase that still has shares remaining. Its gain or loss is unrealized and changes with the current market price.",
  },
  closedLot: {
    title: 'Closed lot',
    body: "The part of a purchase that has been sold. It has its own sale price, holding period, and realized gain. A single sale may close multiple lots or only part of one lot.",
  },
  taxRegime: {
    title: 'Tax regime',
    body: "Which U.S. tax treatment applies: NRA (nonresident alien, e.g. F-1 student status) generally owes no U.S. tax on bank interest or on security sales at all; RESIDENT (e.g. H-1B, once the substantial-presence test is met) is taxed the same way a U.S. citizen is, on both.",
  },
  washSaleFlag: {
    title: 'Wash-sale',
    body: "A loss sale is flagged when the same security — or one on a declared similar-fund list — was bought back within 30 days before or after the sale. On this page it is just a mechanical proximity check, to surfaces the risk.",
  },
  taxToggle: {
    title: 'Taxes',
    body: "Reveals the tax section further down the page: the annual realized-gain and dividend report, an estimated tax-owed summary, flagged wash sales, and open-lot sale previews. It also changes two things outside that section, easy to miss: the HYSA line and rate wherever they appear (the dollar chart, the growth-of-$100 chart) switch from the published rate to an after-tax rate, and the overview's 'Dollar alpha vs. HYSA' card is recomputed the same way — both labeled '(after tax)' once this is on. When tax mode is enabled, dividend amounts in per-lot returns and metrics are shown net of withholding (reflecting the actual cash received); XIRR already reflects withholding since it's a cash outflow recorded in the ledger. Unrealized gains, portfolio value, and benchmark comparisons do not anticipate future tax liability on unsold positions, since no tax is owed until a gain is realized.",
  },
  taxOwed: {
    title: 'Estimated tax owed',
    body: "Capital-gains tax on the year's net realized gain, plus dividend/interest tax, at the rates set above — a resident alien pays the marginal rate on short-term gains and ordinary income, the lower rate on long-term gains and qualified dividends; a nonresident alien owes nothing on gains or interest and a flat rate on dividends. A net loss in a bucket is floored at zero, not a rebate. Balance due nets this estimate against tax already withheld by the broker.",
  },
  liquidationValue: {
    title: 'After-tax liquidation value',
    body: "Today's portfolio value minus the capital-gains tax a full sale of every open lot, right now, would trigger. A snapshot of what selling everything today would actually leave you with, not a projection of any other date.",
  },
} as const

export type GlossaryTerm = keyof typeof GLOSSARY