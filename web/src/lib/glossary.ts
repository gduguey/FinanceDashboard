// Short, plain-language explanations for the dashboard's technical terms:
// what it is, what it guards against, and — where it matters — the
// formula and exactly what's included/excluded on this specific dashboard.
// Keyed by GlossaryTerm and rendered via <InfoTooltip term="..." />.

export const GLOSSARY = {
  xirr: {
    title: 'XIRR',
    body: "Your money-weighted annual return: the constant yearly rate that makes your deposits, withdrawals, and today's value net to zero. Guards against confusing 'my balance went up' with 'my money grew' — only counts cash you actually moved in/out, not trades between your own positions. Formula: Σ cashflow ÷ (1+r)^(days÷365) = 0.",
  },
  xirrProvisional: {
    title: 'XIRR (provisional)',
    body: 'Shown when you have under 12 months of history. Annualizing a short window blows tiny moves into huge-looking rates (a $10 gain in a week "annualizes" to triple digits) — provisional means treat this number as noisy until a year has passed.',
  },
  twr: {
    title: 'TWR — Time-Weighted Return',
    body: "Your strategy's return with contribution timing removed — as if you'd invested once and never added or withdrew. Guards against XIRR flattering you just because a deposit happened to land right before a rally. Built by chaining NAV_end ÷ NAV_start − 1 across the periods between your flows.",
  },
  timingGap: {
    title: 'Timing gap (XIRR − TWR)',
    body: "The difference between your actual (money-weighted) return and your strategy's (time-weighted) return. Positive means your contribution timing helped; negative means it hurt — e.g. depositing right before a drop.",
  },
  growthOf100: {
    title: 'Growth of $100',
    body: "If you'd put exactly $100 in on day one and never added or removed anything, what would it be worth today? Every line here (your NAV, the benchmark, HYSA, CPI) is indexed to 100 at the start so they're comparable side by side — no cashflow matching needed, unlike the dollar chart.",
  },
  nav: {
    title: 'NAV — Net Asset Value (per unit)',
    body: "Think of your portfolio like a private fund you're the only investor in. Day one, NAV = $100 and you're issued units. Each deposit buys more units at that day's NAV; each withdrawal sells units back — but the unit price itself only moves with investment performance, never with your own deposits. That's what removes the 'I put more in, so it looks like a gain' illusion. Formula: NAV(t) = portfolio value(t) ÷ units outstanding(t).",
  },
  cpi: {
    title: 'CPI',
    body: 'The Consumer Price Index — a measure of inflation, indexed to 100 like everything else here. Overlaying it shows whether your growth is beating inflation, not just beating $0.',
  },
  counterfactual: {
    title: 'Counterfactual',
    body: 'A "what if" replay of your exact deposit/withdrawal history, but parked somewhere else instead of your real trades — same dates, same amounts, different destination (a savings account, or a benchmark fund). Not a generic index comparison; it mirrors your specific contribution schedule.',
  },
  hysaCounterfactual: {
    title: 'HYSA counterfactual',
    body: "What your deposits and withdrawals would be worth today if they'd gone into a high-yield savings account instead, compounding daily at the configured rate, on the exact days you actually moved money. Used to answer 'did investing actually beat just leaving it in cash, in dollars, given how I actually contributed.'",
  },
  benchmarkCounterfactual: {
    title: 'Benchmark counterfactual',
    body: "What if every deposit had instead bought the benchmark fund (VOO by default) on that exact day, dividends reinvested, and every withdrawal had sold shares that day? Isolates 'did my picks/timing beat just buying the index' from 'did investing beat cash.' Formula: shares(t) = Σ(deposit ÷ benchmark price on that day) − shares sold on withdrawals; value(t) = shares(t) × benchmark price(t).",
  },
  benchmarkIndex: {
    title: 'Benchmark',
    body: "Your account's comparison fund (VOO by default), shown here as its own total-return growth-of-100 index — not tied to your contribution schedule at all, unlike the dollar chart's benchmark counterfactual. This is simply 'how did the index do,' indexed to 100 like everything else on this chart.",
  },
  dollarAlphaHysa: {
    title: 'Dollar alpha vs. HYSA',
    body: "Your portfolio's value today minus the HYSA counterfactual's value today: what your exact deposits/withdrawals would be worth if they'd sat in a compounding savings account instead. Positive means investing them instead of saving them in cash paid off, in real dollars, given your actual contribution timing.",
  },
  portfolioValue: {
    title: 'Portfolio value',
    body: "Every share you hold, priced at today's market price, plus whatever cash is sitting in the account — including deposits not yet invested and dividends not yet reinvested.",
  },
  contributions: {
    title: 'Contributions',
    body: "Your cumulative net deposits minus withdrawals — money that crossed into or out of the account from outside. Doesn't include dividends (the portfolio paying you isn't new money from your pocket) and doesn't move when you sell one holding to buy another (that's internal, not a new contribution).",
  },
  marketGain: {
    title: 'Market gain',
    body: "The part of a period's value change that isn't your own money moving in or out: value at the end minus value at the start, minus net deposits/withdrawals in between. This DOES include dividends and price appreciation — a dividend raises your account's value without being a deposit, so it shows up here as gain.",
  },
  lotReturn: {
    title: 'Return',
    body: "This lot's raw, non-annualized return: (current value + dividends received on it) ÷ (amount you paid) − 1. Not adjusted for how long you've held it, so a 5% return over 10 days looks the same size as 5% over a year here — see Annualized for a holding-period-adjusted number.",
  },
  annualizedReturn: {
    title: 'Annualized return',
    body: "This lot's return, projected to a one-year rate: (1 + raw return)^(365 ÷ days held) − 1. Only shown once a lot has been held 365+ days — annualizing shorter holds turns small moves into wild, meaningless-looking percentages.",
  },
  realizedGain: {
    title: 'Realized gain',
    body: "Gains already locked in by selling — this number can't change anymore, regardless of what the market does next.",
  },
  unrealizedGain: {
    title: 'Unrealized gain',
    body: "Paper gains on what you still hold, at today's price. Would change if the price moves before you actually sell.",
  },
  lotTerm: {
    title: 'Term',
    body: 'LONG = held 365 days or more as of the sale date; SHORT = held less. A holding-period label only — not tax advice.',
  },
  alphaVsHysa: {
    title: 'Alpha vs. HYSA',
    body: "This closed lot's total return compared to what a HYSA would have earned over the exact same holding window. Unlike a live position, this is a finished, non-provisional number since the position is closed.",
  },
  drift: {
    title: 'Drift',
    body: "How far a symbol's current % of your portfolio is from its target %. Positive = you're holding more than intended (overweight); negative = less (underweight).",
  },
  maxDrawdown: {
    title: 'Largest peak-to-trough so far',
    body: "The biggest drop your NAV has ever taken from a prior high point, on paper. Doesn't measure everyday wiggle — measures how bad your worst 'should I sell?' moment would have felt. Formula: min over time of NAV(t) ÷ (highest NAV seen so far) − 1.",
  },
} as const

export type GlossaryTerm = keyof typeof GLOSSARY
