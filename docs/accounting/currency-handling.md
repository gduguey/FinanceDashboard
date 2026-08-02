# Currency handling

## The core rule: convert only when aggregating, never at write time

Every `Account`, every `Posting`, and every manually-added asset keeps its
own native currency forever — nothing is ever silently converted the
moment it's imported or entered. A posting on a EUR account stays in EUR
in the ledger, permanently. Conversion only happens where amounts from
several accounts need to be combined into one number: net worth, the
income statement, budgets, and goals. Each of those reads a
**display currency** (a toggle in the page header, defaulting to USD) and
converts every amount into it on the fly, at read time — the stored data
itself never changes.

This means the same ledger can answer "what's my net worth in USD" and
"what's my net worth in EUR" from the exact same rows, just by asking for
a different display currency; neither answer is more "native" than the
other, and neither is cached.

## Which date's rate: a stock converts at the as-of date, a flow at its own

The rate a conversion uses depends on what is being converted, and the two
cases are genuinely different questions.

A **stock** — an account balance, a manually-added asset, net worth — is a
statement about one date. "What is this EUR savings account worth in USD
as of 30 June" converts at 30 June's rate, and the net-worth history chart
prices each of its points at that point's own date.

A **flow** — a posting, a goal contribution — happened on its own date, and
converts at *that* date's rate no matter how long ago it was. A €500
expense from March 2024 is worth what €500 was worth in March 2024,
permanently. This is the part that used to be wrong: the income statement,
budgets and the spend curve passed no as-of date at all, so every row in
them, including one from two years ago, was converted at today's rate —
and last March's total moved every time the currency market did. They now
convert per posting date, and so do goal contributions and the unallocated
residual derived from them.

`accounting.ledger.currency` holds both halves: `convert` against
`DisplayCurrency.rates_to_base` for a stock, `with_converted_amount`
against `DisplayCurrency.rates_by_date` for a flow.

**Two consequences worth knowing.** The divisor is per-date too, so an
amount displayed in its own currency is exactly itself on every date
rather than drifting with the rate of the day it is looked at. And the
rate cache holds `DEFAULT_HISTORY_YEARS` (two) years, so a posting older
than the cache has no trailing mean of its own: it is converted at the
oldest rate on file, and one newer than the cache at the newest. That
clamp is deliberate. The alternatives were a null rate — which a left join
turns into a null amount that every `sum` then silently skips, producing a
wrong total that looks like a right one — and refusing the request, which
would make a single old posting a 400 for the whole income statement.

**The currency axis has the same exposure, and there the answer is the
opposite one.** A row whose currency the rate table has no entry for — or
whose currency column is null — joins to nothing and lands in exactly that
skipped-by-`sum` state. Unlike an old date, that is never a legitimate
input: it means the table was not built from the rows being converted.
`with_converted_amount` raises `UnconvertibleCurrencyError` instead, on
both the dated and the scalar path, and nothing catches it — the same
convention `db.base.UnknownNaturalKeyError` follows. Every API caller
builds its table from `api.dependencies._currencies_in_use`, derived from
the same accounts, assets and contributions it is about to convert, so no
caller in this repo can trip it; the check exists because nothing enforced
that, and a caller assembling its own frame would not inherit it. It costs
a `collect()` over one column, measured at nothing on the gated read path
— see `_reject_currencies_with_no_rate` for the figures.

## Where a rate comes from

`accounting/market_data/exchange_rates.py` fetches daily historical rates
from Frankfurter (an ECB-sourced, free, no-API-key exchange-rate service —
the same "no credentials needed" shape this repo already uses for
investment market data). Every raw response is archived to disk, verbatim
and timestamped, before anything is parsed from it, matching the same
archive-raw-first convention every other fetched-and-cached data source in
this repo follows. The parsed history is cached as a small CSV
(`date`, `currency`, `rate_to_base`). There is no manual "Sync exchange
rates" button anymore — `accounting.market_data.fx_sync.run_fx_sync`
refreshes this cache automatically via cron, a few times a day in a short
window around the ECB's ~16:00 CET daily publication rather than
continuously (14:00, 14:45, and 15:30 UTC — spanning the fixing's 16:00
CET publish time across both CEST/summer and CET/winter clocks).
`update_rate_history_cache` is incremental: it only requests the
range from the day after the cache's current latest date through today,
so a run that finds the cache already current makes no network call at
all — cheap enough to check more than once a day without cost. The cache
file is also backed up to R2 (or local disk) after every successful
write, and transparently restored from that backup if it's ever found
corrupted on disk (see `accounting.utils.cache_backup`).

A single day's exchange rate is noisy — pricing net worth off yesterday's
tick would make it swing on pure currency-market noise that has nothing to
do with what's actually happening in your accounts. So the rate this app
actually converts with is a trailing 30-day average of that daily history,
not the latest spot rate. `smoothed_rate_as_of` computes that average for
one date; `smoothed_rate_series` computes it for every date at once, which
is what converting each flow at its own date needs.

## The two pieces that make conversion generic

- `accounting.ledger.currency.convert(amount, from_currency, to_currency, rates_to_base)`
  — takes any two currency codes and a rate table (each currency's rate
  relative to one shared base currency, USD) and converts between them.
  It has no knowledge of which currencies exist; it only ever needs a
  `rates_to_base` entry for whichever two codes it's asked to convert
  between.
- `accounting.ledger.currency.DisplayCurrency` — bundles a target currency
  code with the rate tables needed to reach it, since they are always
  needed together by anything that aggregates across accounts
  (`dashboard.net_worth`, `dashboard.income_statement`, and so on):
  `rates_to_base` for a stock, and `rates_by_date` — one rate per
  (currency, day), already divided into the target — for a flow.
- `accounting.ledger.currency.with_converted_amount` — applies the second
  of those to a whole frame in one join, and is the only place that
  decides which date's rate a row gets.

Neither of these hardcodes USD/EUR, or any fixed number of currencies —
both operate purely on whatever's in the rate table they're handed.

## What actually enumerates the supported currencies

One module, `src/db/currency.py`, holding two things:

- `db.currency.CurrencyCode` — a `Literal["USD", "EUR"]`. This is the
  compile-time list of codes the codebase is allowed to write into a
  `currency` field, and it stays a hand-written `Literal` rather than
  being derived from the `public.currencies` table on purpose: a `Literal`
  is what makes `currency: CurrencyCode` a narrow union in mypy, in
  pydantic's validation, in the generated OpenAPI schema, and therefore in
  the SPA's `schema.ts`. None of those can be computed from database rows
  at type-check time.
- `db.currency.CURRENCY_REFERENCE` — each code's display symbol and
  decimal places. It is the seed for `public.currencies`, the reference
  table every `currency` column foreign-keys into
  (`db.models.CURRENCY_SEED_STATEMENTS`). So the Literal is the **API
  vocabulary** and the table is the **referential guarantee**, from one
  declaration.

`accounting.models.SUPPORTED_CURRENCIES` still exists and is what
`GET /api/v1/accounting/currencies` returns — every currency dropdown in
the frontend is built from that response rather than hardcoding "USD" or
"EUR" — but it is now a *projection* of `CURRENCY_REFERENCE`, not a second
list to keep in step.

Everything else — the exchange-rate fetcher, the conversion functions,
every aggregation in `dashboard/` — reads from these in a loop, never a
fixed list of currency names typed out by hand.

## Concretely: what adding Mexican pesos would take

Say you opened a peso-denominated account and wanted `MXN` supported
end to end. Here's exactly what would and wouldn't need to change:

**Required: one edit, in `src/db/currency.py`.** Add `"MXN"` as a new arm
of the `CurrencyCode` Literal and its entry beside it:

```python
CurrencyCode = Literal["USD", "EUR", "MXN"]

CURRENCY_REFERENCE: dict[CurrencyCode, CurrencyReference] = {
    ...
    "MXN": CurrencyReference(symbol="$", decimal_places=2),
}
```

Both live in `db`, below both ledgers, because `trades.ledger_events`'
`currency` column is the identical column and `trades` may not import
`accounting`. `CURRENCY_REFERENCE` is keyed by `CurrencyCode`, so mypy
rejects a row for a code the Literal does not have, and
`tests/db/test_schema_invariants.py` rejects the reverse — a Literal arm
with no row.

**Then get the row into `public.currencies` on every existing database.**
`CURRENCY_REFERENCE` is the *seed*, and it is only ever applied by the
baseline migration's `CURRENCY_SEED_STATEMENTS` and by the `create_all`
hook the test suite uses. Neither runs again on a database that already
exists — and unlike `institutions` and `securities`, which
`db.base.ensure_reference_rows` fills in on first sight of a name,
nothing inserts a currency at runtime. Until the row exists, every one of
the nine `currency` columns' foreign keys rejects `"MXN"`. So a currency
added after launch needs a one-line data migration inserting it; before
launch, recreating the database from the baseline is enough.

**Nothing to edit in the frontend, but do regenerate.**
`web/src/types/accounting.ts` derives `CurrencyCode` as `Currency['code']`
off the generated `schema.ts`, so there is no hand-written union with an
arm to add — but `schema.ts` still has to be regenerated and committed,
because the OpenAPI schema enumerates the Literal's arms and today's copy
lists only `USD` and `EUR`:

```bash
uv run python -m trades.api.export_openapi
cd web && npm run generate:schema && npx biome format --write src/types/schema.ts
```

CI's `openapi-types` workflow fails the build if you forget.

**Requires nothing else — genuinely automatic:**

- `market_data.exchange_rates.fetch_rate_history` builds its currency list
  as "every code in `SUPPORTED_CURRENCIES` except the base," so the very
  next cron-scheduled sync fetches MXN's history alongside EUR's with no
  code change (USD is the base currency — its rate is always 1 and is never
  fetched).
- `ledger.currency.convert`/`DisplayCurrency`, `dashboard.net_worth`,
  `dashboard.income_statement`, budgets, and goals are all already
  written generically over whatever currencies show up in the rate table
  — none of them special-case USD or EUR by name.
- Every account/goal/import currency dropdown in the frontend picks up
  the new option automatically, since none of them hardcode a currency
  list.

**Would it automatically convert USD and EUR accounts into pesos?** Yes —
as long as Frankfurter (the ECB-sourced rate source this app fetches
from) actually publishes a rate for the new code, which it does for MXN
and for the large majority of actively-traded currencies. After the two
required edits above, the next cron-scheduled sync fetches and caches
MXN's history alongside everything else, and every net-worth/income-statement/
budget/goal total involving a peso account converts correctly from that
point on.

The one genuine limit: if a hypothetical new currency code isn't covered
by Frankfurter/ECB rates at all, the two required model edits above would
still make the app accept and store amounts in it, but `convert()` would
raise a lookup error for it until either that currency starts being
covered by the rate source, or a different rate source is added
specifically for it — a real (if rare) additional-code scenario, not
something the two-edit path above covers.
