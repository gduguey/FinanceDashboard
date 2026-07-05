# Accounting architecture

`accounting` tracks day-to-day cash accounts — checking, savings, credit
cards, SoFi Vaults — the same way `trades` tracks a brokerage account:
**store what happened, replay everything else.** An append-only ledger of
`Posting` rows is the only source of truth; account balances, net worth,
and the income statement are all derived by walking that history forward.

It shares the same FastAPI process and React frontend as `trades` (see
`accounting/api.py`'s router, mounted by `trades/api.py`), but the two
packages are otherwise independent — `accounting` may read `trades` (net
worth needs the tracked portfolio's value), never the reverse.

For the full design rationale, the Firefly III/Maybe research behind it,
and the phased build order, see `ACCOUNTING_PLAN.md` in the repo root —
this doc describes the module map and conventions as actually built, not
the plan.

## How the pieces fit together

```
┌─────────────────────────────────────────────────────────────────┐
│  Front end: web/ (React) — same dev server as trades            │
└────────────────────────────┬────────────────────────────────────┘
                             │ reads cached data, calls same modules
┌────────────────────────────▼────────────────────────────────────┐
│  accounting/api.py       JSON endpoints, router mounted onto     │
│                          trades.api's app                        │
│  accounting/dashboard/   net worth, income statement             │
└────────────────────────────┬────────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
┌───────▼───────┐   ┌────────▼────────┐   ┌───────▼────────┐
│  ledger/      │   │  importers/     │   │  trades/        │
│  replay,      │   │  Chase/SoFi     │   │  (read-only,    │
│  categorize,  │   │  CSV + PDF →    │   │  external_      │
│  currency,    │   │  postings       │   │  investment     │
│  transfers    │   │                 │   │  balance only)  │
└───────────────┘   └─────────────────┘   └────────────────┘
        │                    │
        └────────────────────┼───────────────┐
                             │                │
                    data/accounting/  (gitignored)
```

## Module map

```
src/accounting/
  config.py             AccountingConfig — store/ledger/overrides paths
  models.py             pydantic schemas — Account, Posting, Category, Tag,
                         Rule, OtherAsset, Currency — canonical, once
  store.py              persisted accounts/categories/tags/rules/other-assets
                         (store.json) — seeded defaults, not fetched data
  ledger/               pure domain logic, no I/O
    replay.py             postings → account balances as of any date
    categorization.py     rule matching + vault/internal-transfer detection,
                           repoints placeholder counterparties, sets categories
    currency.py            convert() between the two supported currencies
    transfers.py           unmatched-internal-transfer suggestions
  dashboard/            API-facing aggregation
    net_worth.py           assets/liabilities/net worth, in a display currency
    income_statement.py    category/subcategory totals, monthly income vs.
                            expense, a spend-curve-vs-average series
  importers/            I/O — the only layer that knows a bank's native format
    common.py              shared RawLeg/posting_pair/row_hash helpers
    detect.py              header/filename fingerprint → bank + account guess
    ingest.py              archive raw, standardize, merge into the ledger;
                            rebuild_from_raw_statements recomputes it all
    chase/                 checking.py, credit_card.py
    sofi/                  checking.py, savings.py (CSV); statement_pdf.py
                            (monthly statement PDF — checking + savings +
                            every Vault in one file, the only source for
                            Vault transactions and interest)
  api.py                 FastAPI JSON layer, mounted onto trades.api's app
data/accounting/
  raw_statements/{institution}/{account_id}/{timestamp}.csv   verbatim, never overwritten
  raw_statements/SoFi/statement_pdf/{timestamp}.pdf            verbatim, never overwritten
  ledger.csv             disposable cache, rebuildable from raw_statements/
  store.json             accounts, categories, tags, rules, other assets, eur_usd_rate
  manual_overrides.json  per-posting user edits, always applied after rules
```

## The canonical schema

A `Transaction` is a set of `Posting` rows whose amounts sum to zero (per
currency) — two postings is the common case (an expense or a transfer), but
the schema doesn't assume exactly two. Every posting is signed from its own
account's point of view: positive means money arrived, negative means it
left. See `accounting.models.Posting`'s docstring for the full field list.

Every withdrawal or deposit needs a counterparty; when the real one isn't
known yet, an importer points it at one of two placeholder accounts
(`uncategorized:expense`/`uncategorized:income` — see `store.py`).
`ledger.categorization.apply_rules` repoints that placeholder at the real
counterparty — a vault, a `Rule` match, a SoFi internal checking↔savings
transfer — every time postings are read, never baked into the ledger cache,
so a manual correction is never at risk of being clobbered by re-running a
rule.

### Invariant

For every `transaction_id`, `Σ postings.amount` converted to one currency
must equal zero. This is enforced by construction in
`importers.common.posting_pair` (every importer emits balanced pairs), not
checked after the fact.

## Multi-currency

Two currencies are supported, `USD` and `EUR` (`accounting.models.CurrencyCode`).
Every account, posting, and manually-added asset keeps its own native
currency — nothing is ever silently converted at write time. Conversion
only happens where amounts are aggregated across accounts (`dashboard.net_worth`,
`dashboard.income_statement`), using one stored `eur_usd_rate` on the store
(`ledger.currency.convert`) and a per-request `display_currency` — never a
live-fetched rate.

## Double-booking: importers de-duplicate, categorization repoints

Some sources record the same real-world transfer on both sides — SoFi's
statement PDF shows a vault's own "Deposit From savings balance" mirroring
the savings account's "Withdrawal To X Vault", and Chase's credit card CSV
shows a "Payment Thank You" row mirroring the checking account's payment.
The fix in both cases is the same: the importer drops the mirrored,
non-authoritative side at parse time (see `importers.sofi.statement_pdf`'s
module docstring), keeping exactly one row per real event for
`ledger.categorization` to repoint.

## Core conventions

This module follows the same two standing rules as `trades` — see
`AGENTS.md`/`CLAUDE.md` and `docs/trades/architecture.md`:

- **New data source → canonical schema, always.** A bank's native
  vocabulary (SoFi's `TYPE` column, Chase's `Type`/`Category` columns)
  never leaks past the importer that reads it — see `standardize_sofi_statement_pdf`
  for the concrete example.
- **Cache raw, derive everything else.** Every uploaded CSV or PDF is
  archived verbatim, timestamped, never overwritten, before anything is
  parsed. `ledger.csv` and `store.json`'s auto-vivified accounts are
  disposable caches, rebuildable from `raw_statements/` via
  `importers.ingest.rebuild_from_raw_statements`.
