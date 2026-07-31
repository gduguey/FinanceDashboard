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

Two things, and only two:

- `accounting.models.CurrencyCode` — a `Literal["USD", "EUR"]` type. This
  is the compile-time list of currency codes the codebase is allowed to
  write into a `currency` field.
- `accounting.models.SUPPORTED_CURRENCIES` — a `dict[CurrencyCode, Currency]`
  registry holding each currency's display symbol and decimal places.
  `GET /api/v1/accounting/currencies` returns this dict directly, and every
  currency dropdown in the frontend (an account's currency, a goal's
  target currency, the page-header display-currency toggle) is built from
  that endpoint's response — none of them hardcode "USD" or "EUR" as a
  literal option.

Everything else in the module — the exchange-rate fetcher, the conversion
functions, every aggregation in `dashboard/` — reads from these two in a
loop, never a fixed list of currency names typed out by hand.

## Concretely: what adding Mexican pesos would take

Say you opened a peso-denominated account and wanted `MXN` supported
end to end. Here's exactly what would and wouldn't need to change:

**Required, two edits, both in `accounting/models.py`:**

1. Add `"MXN"` as a new arm of the `CurrencyCode` Literal.
2. Add an entry to `SUPPORTED_CURRENCIES`:
   ```python
   "MXN": Currency(code="MXN", symbol="$", decimal_places=2),
   ```

**Recommended, one edit, for full frontend type-safety:** add `'MXN'` to
the `CurrencyCode` union type in `web/src/types/accounting.ts`. The
frontend's currency dropdowns are already data-driven from
`GET /currencies`, so they'd show "MXN" as a selectable option even
without this — but TypeScript wouldn't otherwise know `'MXN'` is a valid
value for a typed `currency` field.

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
