# Next steps

A handout for picking this back up later. Nothing below is started — no
code changed for any of this yet. Rough suggested order: **trade
processing (sells, dividends) before metric cleanup before React** — the
frontend will want to display whatever the finalized metrics look like,
and the metric cleanup (weighting, annualization threshold) probably wants
dividends separated out from buys first.

## 1. Trade processing: handle sells, not just buys

Today `preprocessing.standardize_ibkr_trades` keeps only `BUY` fills and
silently drops `SELL`/`BUY (Ca.)`/`SELL (Ca.)` rows — fine for a
buy-and-hold "how much have I invested" view, not enough for real
portfolio accounting.

- The current canonical schema (`config.TradeSchema`: `trade_date`,
  `symbol`, `shares`, `usd_spent`) implicitly means "a purchase." A real
  transaction model needs at least a `transaction_type` (BUY/SELL/
  DIVIDEND, maybe more) — this probably doesn't fit as an extension of
  today's schema so much as a **new, broader one** (e.g. a `Transaction`
  schema) that the existing "invested schedule" view becomes a filtered
  slice of.
- Selling needs realized P/L and cost-basis lot tracking (FIFO is the
  common default). IBKR's Flex Query already has a **Closed Lots** option
  under the Trades section (not currently enabled on the "Trade History
  API" query) that gives you IBKR's own FIFO-matched realized P/L —
  probably worth pulling that instead of reimplementing lot-matching by
  hand. Check it before building anything custom.
- Once sells exist, "shares held per symbol over time" becomes a real
  running balance, not just a cumulative sum of buys — something to
  reconcile against IBKR's own `OpenPositions` snapshots as a sanity check.

## 2. Dividend detection and dividend logic

Two different things get called "dividends" here — worth deciding which
one (or both) you actually want before building:

- **DRIP reinvestment purchases** show up as small `Trade` rows (a
  fractional-share `BUY`). Don't build a $-amount heuristic as the first
  attempt — check a real DRIP trade's raw archived XML
  (`data/brokers/ibkr/raw_statements/`) for its `Code`/`notes` attribute
  first. IBKR Flex trades carry a codes field that may directly flag
  "this was a reinvestment," which would be far more reliable than
  guessing from trade size.
- **Cash dividends** (paid out, not reinvested) don't appear in `Trades`
  at all — they're a `CashTransaction` row, which lives in a Flex Query
  section ("Cash Transactions") the current "Trade History API" query
  doesn't include. If "how much per symbol, how often, % and USD" should
  cover *all* dividends (not just the reinvested portion), the Flex Query
  needs that section added, and `ibkr.py`/`models.py` need a
  `CashTransaction`-parsing path alongside `Trade`/`OpenPosition`/
  `CashReportCurrency`.
- Once dividend events are identified (either or both sources): per
  symbol, total $ received, frequency (e.g. payments/year), yield-on-cost
  and/or yield-on-current-value, and a portfolio-level roll-up. Probably a
  new `dividends.py` module (logic) rather than folding this into
  `transactions.py`, which is about invested capital, not income.

## 3. Metric cleanup

- **Annualized return floor**: add a minimum holding period (a new
  `ReturnsConfig` field, e.g. `min_days_for_annualization`) below which
  `annualized_return_pct` reports `NaN`/not-shown instead of the
  huge, noisy numbers short holds currently produce on purpose. This is a
  deliberate reversal of the behavior documented today in
  `docs/returns.md` ("short holds intentionally show huge numbers, that's
  not a bug") — update that doc alongside the code change so it doesn't
  keep asserting the old rationale.
- **Dollar-weight the summary metrics**: `portfolio_alpha_pct` is already
  a $-weighted average across trades; extend the same weighting to a
  portfolio-level annualized-return figure, excluding trades below the
  new minimum-holding-period floor from that weighted average (same
  reasoning already applied to alpha: don't let a handful of noisy
  short-hold trades dominate a blended number).

## 4. React interface, powered locally

- Matches the split already called out in `docs/architecture.md`:
  `transactions.py`/`returns.py`/`prices.py`/`brokers/ibkr.py` stay
  exactly as they are (pandas/logic, no UI concerns); only
  `visualization.py`'s job — turning computed data into something
  renderable — gets a second implementation (JSON over HTTP) alongside
  the existing Plotly one, not a replacement of it.
- Needs a thin local API layer (FastAPI is a reasonable default) exposing
  endpoints for: the trade/returns tables, the schedule/pie aggregations,
  price snapshots, and triggering an IBKR sync — each endpoint just calling
  the existing module functions and serializing the result.
- React app in its own directory (e.g. `web/`), fetching from that local
  API. Since this is single-user and local-only, auth is a non-issue;
  the main open decision is whether the API should be a real long-running
  local server or something lighter (e.g. the React app reading
  periodically-exported JSON files) — worth deciding based on how "live"
  you want the dashboard to feel.

## Standing reminders (from AGENTS.md / CLAUDE.md)

Apply both conventions already established to whatever comes out of this
list:

- Any new concept that needs its own canonical shape (e.g. a broader
  `Transaction` schema, a `CashTransaction` model) gets declared once in
  `config.py`/`models.py`, with a `standardize_*`/parsing function in
  `preprocessing.py`, the same way `TradeSchema`/`RawTrade`/
  `standardize_ibkr_trades` work today.
- Any new externally-fetched, locally-cached data (e.g. Cash Transactions
  from a re-configured Flex Query) gets an immutable raw archive with the
  derived/merged file treated as a rebuildable cache — the same pattern as
  `raw_statements/` + `rebuild_from_raw_statements` for trades.
