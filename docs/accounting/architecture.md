# Accounting architecture

`accounting` tracks day-to-day cash accounts — checking, savings, credit
cards, named sub-balances like SoFi Vaults — the same way `trades` tracks a
brokerage account: **store what happened, replay everything else.** Every
account balance, every net worth figure, every income/expense total, every
budget's actual spend, and every goal's balance is *recomputed* from one
append-only record every time it's asked for — nothing is a separately
maintained running total that could silently drift out of sync with the
history it's supposed to summarize.

It shares the same FastAPI process and React frontend as `trades` (see
`accounting/api.py`'s router, mounted by `trades/api.py`), but the two
packages are otherwise independent — `accounting` may read `trades` (net
worth needs the tracked brokerage portfolio's value), never the reverse.

For the plain-language, feature-by-feature walkthrough meant for someone
using the app rather than reading its code, see the in-app Guide page
(`web/src/pages/GuidePage.tsx`, linked from the sidebar). This document is
the technical counterpart: the vocabulary, the schema, and the module
layout, for someone reading or extending the code itself.

## Vocabulary

These words recur throughout the codebase and the rest of this document —
worth pinning down precisely once, here, rather than re-explaining in every
file that uses them.

- **Ledger** — the complete, append-only record of every posting ever
  produced from every CSV or statement ever imported. It is the *only*
  place data is durably written to as a fact; every other number the app
  shows is derived from it on demand, never cached as its own source of
  truth.
- **Transaction** — one real-world economic event: a paycheck landing, a
  card swipe, a transfer between two of your own accounts. A transaction
  is never stored as a single row — it exists only as the group of
  postings that share one `transaction_id`.
- **Posting** — one row of one transaction: one account, one signed
  amount, one date, optionally a category/subcategory/tags. `amount` is
  always signed from *that posting's own account's* point of view —
  positive means money arrived in that account, negative means it left.
  This is the atomic, immutable unit everything else in this module is
  built from (`accounting.models.Posting`).
- **Leg** — an informal synonym for one posting, used when talking about
  it as one side of its transaction ("the checking leg," "the credit-card
  leg" of the same transfer). It isn't a distinct type in the code — it's
  the same `Posting`, just described relative to the transaction it
  belongs to.
- **Replay** — recomputing a result by walking through the ledger's
  history in order, instead of reading a value that was stored ahead of
  time. An account's current balance, for instance, is never a stored
  field — it's "sum every posting on this account, in date order, up to
  now," recomputed fresh on every request (see `ledger/replay.py`).
- **No I/O (pure domain logic)** — code that only transforms data already
  in memory (a `polars.DataFrame`, a list of pydantic models) and never
  itself reads or writes a file, calls a network request, or touches the
  filesystem/database. Everything under `ledger/` and `dashboard/` is
  written this way deliberately: it can be tested by handing it plain
  in-memory data and checking what comes back, with no setup or teardown
  of any real file or service. Anything that *does* need to touch disk or
  the network — reading an uploaded CSV, fetching an exchange rate, calling
  an LLM — is confined to `importers/`, `market_data/`, `llm/`, and the
  handful of `load_*`/`save_*` functions in `store.py`, which pure logic
  never calls directly (it's handed already-loaded data instead).
- **TransferRule** vs. **category pattern** — two different,
  easily-confused mechanisms, covered in full in `categorization.md`. In
  short: a `TransferRule` resolves a posting's *counterparty account* (and
  optionally its category) automatically, with no confirmation step, every
  time the ledger is read. A category pattern only ever *suggests* a
  category — nothing changes until a human applies and validates the
  suggestion. Either can be switched off without deleting it (`active`).

## The canonical schema

A transaction is a set of `Posting` rows whose amounts sum to exactly zero,
per currency. Two postings — one negative, one positive — is the ordinary
case (a plain expense or a plain transfer), but nothing in the schema
requires exactly two; any number ≥ 2 is valid, which is what makes
splitting one deposit into several categorized pieces possible (see
`categorization.md`).

Every withdrawal or deposit needs a counterparty. When the real
counterparty isn't known yet, an importer points a posting at one of two
virtual placeholder accounts:

| account_id | kind | name |
|---|---|---|
| `uncategorized:expense` | `expense_payee` | Uncategorized Expense |
| `uncategorized:income` | `income_source` | Uncategorized Income |

`ledger.categorization.apply_rules` repoints that placeholder at the real
counterparty — a named vault, a `TransferRule` match, a detected internal transfer
— every time postings are read, never baked into the ledger cache. A
manual correction (`ManualOverride`) is layered on top of that, also
applied fresh on every read, so it can never be silently clobbered by
re-running a rule or re-importing a statement.

**Why this matters for every income/expense chart:** a posting only ever
counts as real income or a real expense — as opposed to an internal
transfer between two accounts you hold — when its transaction's sibling
leg is *still* on one of the two virtual placeholder accounts above (see
`dashboard.income_statement._real_income_expense_legs`). The instant a
rule repoints that placeholder at a real account, the transaction becomes,
structurally, a transfer — and every income/expense/budget/goal
computation correctly stops counting it, with no separate "is this a
transfer" flag to maintain by hand.

### Invariant

For every `transaction_id`, `Σ postings.amount` converted to one currency
must equal zero. This is enforced by construction — every importer emits
balanced pairs via `importers.common.posting_pair`, and every split
(`ledger.categorization.apply_posting_splits`) is validated against the
original posting's amount before being written — never checked after the
fact as a data-quality pass.

### The one deliberate exception: manual transfers

Every posting above traces back to a real row in an imported statement —
with one exception. Closing an account (`Account.closed`) whose balance
isn't zero needs somewhere to record where that remaining money went, and
no future bank statement will ever describe that movement, since the
account is closed. A `ManualTransfer` (`accounting.models`) is a
user-entered transfer between two of their own accounts;
`ledger.manual_transfers.postings_for_manual_transfers` turns each one into
its two postings — one leaving the closed account, one arriving at
wherever the user says it went — folded into the resolved ledger the same
way rules and overrides are, never baked into the ledger cache. Closing
and (optionally) recording where the balance went happen atomically via
`POST /accounts/{id}/close`.

## Module map

```
src/accounting/
  config.py             AccountingConfig — every on-disk path (raw statement/exchange-rate
                         archives only — everything else lives in Postgres), derived from
                         one data_dir
  models.py             pydantic schemas — Account, Posting, Category, Tag, TransferRule,
                         CategoryPattern, Goal/GoalContribution, Budget, OtherAsset,
                         ManualTransfer, PostingMerge, DismissedSuggestion, Currency —
                         canonical, declared once
  store.py              load_store — one whole-store read composed from repositories/, plus
                         the pure category/tag tree logic (normalize, rename, delete plans)
                         and the seeded defaults every new user starts with
  repositories/         one module per aggregate root, each owning its own tables' reads
                         and writes: accounts, taxonomy, planning, interpretation
  db/                   SQLAlchemy models/queries for the `accounting` Postgres schema —
                         core.py (accounts/categories/tags/postings/transactions),
                         budgets.py, goals.py, automation.py (recurring additions,
                         withdrawal priorities), corrections.py (manual overrides,
                         splits, merges, dismissed suggestions), simulator.py, llm.py
                         (per-provider usage tracking)

  ledger/               pure domain logic, no I/O
    replay.py             postings -> account balances as of any date
    categorization.py     rule matching + vault/internal-transfer detection;
                           repoints placeholder counterparties, sets categories
    patterns.py            CategoryPattern description-match suggestion logic
    pending.py              accept/reject lifecycle for a not-yet-confirmed
                            AI/pattern category suggestion
    currency.py             convert() between any two supported currencies
    transfers.py            unmatched-internal-transfer suggestions
    duplicates.py           likely-duplicate-transaction suggestions + certainty scoring
    manual_transfers.py     turns a ManualTransfer into its two postings (see below)
    goal_automations.py     contribution- and withdrawal-automation math —
                            decides amounts only, never writes anything itself

  dashboard/            API-facing aggregation, one file per concern
    net_worth.py           assets/liabilities/net worth, in a display currency
    income_statement.py    category/subcategory totals, monthly income vs.
                            expense, a spend-curve-vs-average series
    budgets.py              budget vs. actual comparison
    interest.py             realized-interest tracking + projection
    simulator.py            compound-interest what-if projection
    paystub.py              paystub-to-bank-deposit reconciliation + proposed splits
    goals.py                goal balance / unallocated-money derivation

  importers/            I/O — the only layer that knows a bank's native format
    common.py              shared RawLeg/posting_pair/row_hash helpers
    detect.py               header/filename fingerprint -> bank + account guess
    ingest.py               archive raw, standardize, merge into the ledger;
                            rebuild_from_raw_statements recomputes it all
    paystub.py              PDF text extraction -> structured EarningsStatement
    chase/                  checking.py, credit_card.py
    sofi/                   checking.py, savings.py (CSV, both formats —
                            the newer wide format also covers Vaults)
    canonical/              no-code fallback importer for any bank with no
                            dedicated standardizer — fuzzy column/date/amount
                            parsing, auto-creates categories (see
                            canonical-csv-import.md)

  market_data/
    exchange_rates.py      fetches + caches daily FX history, computes a
                           smoothed rate — see currency-handling.md

  llm/                   pluggable AI-categorization provider layer
    provider.py             provider interface + fallback-across-providers logic
    gemini.py, mistral.py    concrete providers
    categorize.py            prompt building + response validation
    settings.py              provider credentials
    usage.py                 per-provider call-count/rate-limit tracking

  api/                   FastAPI JSON layer, mounted onto trades.api's app
    api.py                  router registration
    dependencies.py          shared per-request helpers (config, resolved postings)
    api_models.py            request/response pydantic models
    routers/                 dashboard.py, store.py, postings.py, imports.py,
                              goals.py, llm.py, exchange_rates.py — one file per
                              concern, each Depends(get_current_user_id)-scoped

data/accounting/         (gitignored) — raw archives only; every derived/persisted
                          fact (ledger, store, overrides, goals, budgets, LLM usage,
                          ...) lives in Postgres instead, per-user, RLS-scoped
  raw_statements/{institution}/{account_id}/{timestamp}.csv   verbatim, never overwritten
  exchange_rates/raw/{timestamp}.json                          verbatim, never overwritten
  exchange_rates/rates.csv                                     disposable cache, rebuildable;
                                                                shared across every user, not
                                                                per-user (see market_data/
                                                                below) — the one thing under
                                                                data/accounting/ that isn't
```

## Categorization, planning, and everything past the ledger

Everything above this line is the shared foundation every page in the app
reads from. What's built on top of it is covered in its own documents,
since each is a substantial topic on its own:

- **`categorization.md`** — categories, tags, rules vs. category patterns
  vs. AI suggestions, the accept/reject ("pending") lifecycle those two
  suggestion sources share, auto-detected transfer suggestions,
  duplicate-transaction detection and merging, and transaction splitting.
- **`planning.md`** — budgets (including per-subcategory budgets) and
  goals: how each is laid over the same categorized postings without
  maintaining any separate copy of them.
- **`currency-handling.md`** — how multi-currency conversion works, and
  exactly what's required to add a new supported currency.
- **`adding-accounts.md`** — what's involved in teaching the app to read a
  new bank's export format.
- **`canonical-csv-import.md`** — the no-code fallback importer for a bank
  with no dedicated standardizer.

## The accounting/trades coupling

The SoFi savings export shows money leaving to a brokerage
("INTERACTIVE BROK ... DIRECT_PAY"). Rather than tracking brokerage detail
twice, that posting's counterparty can be a placeholder account
(`kind="external_investment"`) whose balance is pulled live from `trades`
instead of being computed by replaying postings — but only when the
account's own `external_ref` field is set to `"trades"`. This is a choice
made once, when the account is created (or edited): "pull from
Investments" sets `external_ref="trades"`; "set manually" leaves it `None`,
and `dashboard.net_worth.base_balance` then values that account exactly
like any other — from its own postings and opening balance. A manually-
tracked `external_investment` account (a friend-managed fund, a brokerage
this app doesn't sync with) never reaches into `trades` at all.

When it does pull, `api.routers.dashboard._external_investment_values_usd`
reads the value live from the *running* `trades.api` app's own
`app.state.config`, via
`trades.dashboard.valuation.daily_portfolio_values` (one batched
computation across every requested date, rather than replaying the whole
ledger once per date — net worth history can ask for a year of daily
points). This is the one explicit, one-directional coupling between the
two modules: accounting reads trades, trades never reads accounting. See
`/docs/architecture.md` (repo root) for how the two modules share one
process end to end.

## Core conventions

Two standing rules shape almost every change to this module:

- **A new data source always maps onto the canonical schema — never the
  reverse.** A bank's native column names and codes (SoFi's `TYPE` column,
  Chase's `Type`/`Category` columns) are never allowed to leak past the
  importer that reads them. Each source gets its own
  `standardize_{source}_...` function that maps its shape onto
  `accounting.models`' fields, validated through the matching pydantic
  model before anything downstream sees it. See `adding-accounts.md` for
  the concrete steps.
- **Cache raw, derive everything else.** Every uploaded CSV or PDF, and
  every fetched exchange-rate response, is archived verbatim and
  timestamped — never overwritten — before anything is parsed from it.
  The imported postings in the Postgres ledger, and `exchange_rates/rates.csv`,
  are disposable caches — rebuildable from those raw archives
  (`importers.ingest.rebuild_from_raw_statements`), never the only copy of
  anything that happened. Account metadata and user corrections (overrides,
  splits, merges, transfer links) are durable records the rebuild *reads* and
  re-applies onto the rebuilt postings, not themselves reconstructed from
  statements.
