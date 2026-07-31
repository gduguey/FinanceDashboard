# `db` — shared Postgres layer

`db` is the one module both `accounting` and `trades` depend on (never the
reverse — see `docs/architecture.md` at the repo root). It owns everything
that isn't specific to either module: the database connection itself, the
`users`/`user_secrets` tables, Row-Level Security, secret encryption, and
backups. This file is the reference for all of that — what each piece is
for, and the exact commands for the scenarios you'll actually run into.

## Files in this module

| File | What it's for |
|---|---|
| `settings.py` | Where the two Postgres connection strings come from (`DATABASE_URL`, `DATABASE_URL_APP`) — see "Two roles" below. |
| `session.py` | Builds the one shared connection pool (`get_engine`) and hands each web request its own database session (`get_db`), tagged with which user is making the request. |
| `base.py` | The shared SQLAlchemy `Base` every table (in every module) is declared on, plus the `uuid7()` primary-key default; the `MONEY`/`SHARES`/`RATE` column types; the `Timestamped` mixin; `ids_by_natural_key`/`natural_keys_by_id` (the one seam translating a human-chosen string to a row's internal id) and its `UnknownNaturalKeyError`; the write helpers `merge_by_natural_key` (whose flush is savepoint-scoped so a lost race on a concurrent first insert retries as an update, bounded by `NATURAL_KEY_MERGE_ATTEMPTS` and raising `ConcurrentNaturalKeyInsertError` when exhausted), `upsert_and_prune` and `ensure_reference_rows`; and `check_and_bump_row_version`/`VersionConflictError`, the whole of optimistic concurrency. |
| `models.py` | The four tables that live outside any one module's own schema: `users`, `user_secrets`, `external_identities`, and the `currencies` reference table both schemas foreign-key against. |
| `tenant.py` | Which tables are tenant-owned, derived from the schema rather than listed by hand — `tenant_tables`, `enable_rls_statements`, `POLICY_NAME`, `is_reference_table`, and `RLS_EXEMPT`. The baseline migration emits whatever this computes; see "Row-Level Security" below. |
| `money.py` | `MONEY_SCALE`/`MONEY_QUANTUM`/`ROUNDING` (`ROUND_HALF_UP`) and the `Money`/`Rate` wire types — the one place money's scale and rounding mode are decided. |
| `currency.py` | The `CurrencyCode` literal and `CURRENCY_REFERENCE`, the seed data for `public.currencies`. Lives here so `trades` and `accounting` share one list instead of one of them going unvalidated. |
| `indexes.py` | Generates the foreign-key and `(user_id, hot_col)` indexes from the schema, so a new FK cannot ship unindexed (DB-audit D1/D2). Skips reference tables, whose columns are too low-cardinality to be worth an index. |
| `timestamped_backups.py` | Shared newest-N pruning that `db.backup.prune_old_backups` and both cache-backup modules call through to. |
| `current_user.py` | Which user is making the current request — a placeholder name FastAPI resolves to the real Clerk-session-derived identity at runtime; see "Which user is making this request" below for exactly how. Deliberately has no `DEFAULT_USER_ID` or any other fallback identity — that's a test-only concept, defined in `tests/conftest.py` instead. |
| `external_identities.py` | `lookup_user_id`/`link_identity` — the only place a `(provider, external id)` pair is ever read or written; see "The two tables here" below. |
| `encryption.py` | Encrypts/decrypts anything stored in `user_secrets.ciphertext`. |
| `secrets.py` | `get_secret`/`set_secret`/`delete_secret` — the only way any code in this repo reads or writes a credential. |
| `backup.py` | Dumps the whole database and uploads it somewhere durable. |

## Ids: `uuid7()`, and why a row's id is minted rather than computed

Every table's `id` is a UUID the *database* generates, from one SQL function
this repo defines: `public.uuid7()` (see `db.base`). It carries no meaning.
You cannot work out a row's id from what the row is about — the only way to
learn it is to ask, which is what `ids_by_natural_key` is for.

**Why version 7 specifically.** A UUID is 128 bits, and v7 spends the first
48 of them on the current time in milliseconds, big-endian, with the
remaining bits random. That ordering is the entire point. A primary key is
backed by a B-tree, and a B-tree stores keys in sorted order — so with a
*random* key, consecutive inserts land at random points in the index,
dirtying pages all over it and splitting them as they fill. With a
time-ordered key, every insert sorts after every insert before it, so they
all land at the right-hand edge: one hot page instead of hundreds of cold
ones. This is DB-audit finding **D3**.

**Why we define the function ourselves.** Postgres grew a built-in
`uuidv7()` in version 18. This project runs 16 (`deploy/docker-compose.yml`
pins `postgres:16-alpine`), so `db.base._CREATE_UUID7_SQL` implements RFC
9562 §5.7 directly — timestamp prefix, version nibble, variant bits, random
remainder. When this project moves to Postgres 18+, that function body can
be replaced by a call to the built-in; nothing else has to change, because
every table only ever names `public.uuid7()`.

**Where it gets installed.** Alembic's autogenerate cannot see functions, so
the definition is executed from two places against one source of truth —
exactly the arrangement `accounting.db.triggers` uses for the zero-sum
constraint trigger:

- the baseline migration (`upgrade` runs `UUID7_STATEMENTS`), for real
  databases;
- a `before_create` hook on `Base.metadata`, for the test suite's
  `Base.metadata.create_all`.

`before_create`, not `after_create`: every table declares
`DEFAULT public.uuid7()` on its primary key, and Postgres resolves that
function when the *table* is created, not when a row is inserted — so it has
to exist first. (For the same reason the downgrade drops it last, after the
tables that depend on it are gone.) Without the hook, the function would
exist in production and in no test.

### What replaced the content-hashed id

There used to be a helper here that computed a row's id by hashing
`(user_id, table, natural_key)` with `uuid5`, so the same natural key always
produced the same id without a lookup. It is gone, because a content hash
scatters keys uniformly — precisely the worst case for the B-tree above.

What it *guaranteed* did not go away; it moved to where it belonged all
along. "The same natural key always names the same row" was never really a
property of the id — it is `UNIQUE (user_id, natural_key)`, which every
natural-keyed table still carries. So:

- every **write** conflicts on `(user_id, natural_key)` (or, for a table
  with no natural key, on whatever its genuine unique constraint is —
  `opening_balances` on `(user_id, account_id)`, `posting_splits` on
  `(user_id, posting_id)`), never on `id`;
- every **read** that needs an id resolves it, via `ids_by_natural_key`;
- every **insert** that needs the id it just created reads it off the
  flushed row (SQLAlchemy fetches server-generated primary keys with
  `INSERT ... RETURNING id`).

`ids_by_natural_key` and `natural_keys_by_id` are the **only** two places in
this repo where an id and a natural key meet. Above that line — pydantic
models, API paths, every domain function — a row is addressed by its natural
key string, and nothing else. Subscript the lookup (`ids[key]`) when you
hold a reference that must resolve: a key with no row raises
`UnknownNaturalKeyError`, which is the loud failure the foreign key used to
provide when a made-up id reached Postgres. Use `.get(key)` only where the
absence itself is the answer.

## Primary key patterns across every table

Every table in this database falls into exactly one of four key shapes.
Which one a new table should use isn't a free choice — it follows directly
from three questions: *is this tenant data at all?*, *does this table get
bulk-rewritten or re-imported?* and *does anything else foreign-key against
its `id`?*

**1. A `uuid7()` surrogate `id`, plus a `natural_key` column** — for tables
that get fully rewritten on every save, or re-imported from an external
source, so "insert, or recognize this already exists" has to work. The
recognizing is done by `UNIQUE (user_id, natural_key)`, which is what every
write conflicts on. This is the majority of user-owned tables:
`accounting.accounts`, `categories`, `tags`, `transactions`, `postings`,
`other_assets`, `posting_merges`, `suggestions`, `transfer_links`,
`goals`, `goal_contributions`, `goal_automations`,
`categorization_rules`, `budgets`, `simulator_scenarios`;
`trades.broker_connections`, `ledger_events`.

**2. A `uuid7()` surrogate `id` and no `natural_key`** — for tables that are
never re-imported and have no human-chosen name of their own, because their
identity is entirely "which row do I hang off". Uniqueness comes from a
`UniqueConstraint` over those owning columns, and that constraint is what a
write conflicts on and what a caller addresses the row by:
`public.users` (whose identity is the external one in
`external_identities`); `accounting.opening_balances`
(`(user_id, account_id)`), `posting_overrides` (`(user_id, posting_id)`),
`posting_splits` (`(user_id, posting_id)`), `posting_split_legs`
(`(user_id, posting_split_id, ordinal)`).

**3. No surrogate `id` at all — the real key(s) are the primary key,
directly.** This is the right choice specifically when nothing else ever
foreign-keys against this table's id (so there's no need for a stable,
opaque handle a rewrite could invalidate) and the table's natural
uniqueness is already exactly what a caller looks it up by:

- `public.user_secrets` → composite `(user_id, key)`.
- `trades.ledger_event_trade_details` → single-column `ledger_event_id`
  (itself a foreign key — a 1:1 extension of `ledger_events`, one row
  exists only if the parent event is a `BUY`/`SELL`).
- `trades.dashboard_settings` → single-column `user_id`. A genuine
  singleton: at most one row per user, looked up only ever by that user's
  own id, nothing else references it.
- `accounting.llm_usage` → composite `(user_id, provider)`. Looked up only
  ever by that exact pair, nothing else references it.

The last two are new tables (added to move `trades`'s dashboard
preferences and `accounting`'s LLM call counters out of flat JSON files —
see each package's own docstrings on `trades.db.models.DashboardSettings`
and `accounting.db.llm.LLMUsage`), and deliberately follow this third
pattern rather than the majority one: neither is ever bulk-rewritten or
re-imported, and nothing else in the schema foreign-keys against either
one's identity — so a surrogate id would just be one more column with no
job to do. Giving these a surrogate id would be following the majority
pattern out of habit rather than for a reason that actually applies. The
pure association tables (`accounting.posting_tags`,
`posting_override_tags`, `posting_merge_duplicates`,
`categorization_rule_exclusions`, `transfer_linked_transactions`) belong
here too: the association *is* the key, and their surrogate ids were dropped
for exactly that reason (DB-audit D9).

**4. No `user_id` at all, and the vocabulary value itself is the primary
key** — the reference/dimension tables. These are shared across every
tenant, so there is nothing to isolate and no surrogate to mint:

- `public.currencies` → PK `code` (the FK target every `currency` column in
  both schemas names).
- `accounting.institutions` → PK `code`.
- `trades.securities` → PK `symbol`.

`db.tenant.is_reference_table` is the formal predicate — no `user_id`, and
not `users`. That one predicate does three jobs: it keeps these tables out of
the RLS derivation without needing an exemption, it keeps `db.indexes` from
minting a useless index over a handful of distinct values, and it tells a
reader that the missing `user_id` is the point rather than an oversight.
`db.base.ensure_reference_rows` is the one batched, concurrency-safe seam
that populates them. `accounting.db.institutions`' docstring carries the
"why a table rather than a repeated `CHECK (... IN (...))`" argument
(Karwin's "31 Flavors") — these three tables replaced eight copied currency
CHECKs, and gave `trades.ledger_events.currency` its first constraint.

## How a save actually writes to Postgres: wipe-and-reinsert vs. upsert-and-prune

The `natural_key` column above (and its `UNIQUE` constraint) is what *lets*
a table be safely rewritten — but rewriting is still a choice each write
path makes, table by table. Two techniques are in use across the
accounting write paths, and picking the wrong one for a new table is a
real mistake, not just a style preference — see "Which technique to use"
below.

There used to be exactly one write path — a single whole-store save every
mutating endpoint funnelled through, always passing the complete desired
end-state of *every* table. It is gone. Every accounting table is now
owned by one per-aggregate repository under `accounting.repositories`,
each writing only the rows a request actually names:

- `accounting.repositories.accounts` owns `accounts` and
  `opening_balances` (and projects manual transfers on and off the
  ledger's own `transactions`/`postings` — see below);
- `accounting.repositories.taxonomy` owns `categories`, `tags`, and — for
  want of a better home so far — `other_assets`, `simulator_scenarios`;
- `accounting.repositories.planning` owns `budgets`, `goals`,
  `goal_contributions`, `goal_automations`;
- `accounting.repositories.interpretation` owns `categorization_rules`,
  `categorization_rule_exclusions`, `posting_splits`,
  `posting_split_legs`, `posting_merges`, `posting_merge_duplicates`,
  `transfer_links`, `transfer_linked_transactions`, `posting_overrides`,
  `posting_override_tags`, `suggestions`.

The read side went the same way. There is no whole-store read either:
each repository exposes its own `load_*`, and a caller asks for the
collections it actually uses — the resolution pipeline takes the five
overlay tables plus accounts, a net-worth request takes accounts,
opening balances and other assets, and `GET /store` is a router-level
recomposition of every `load_*` rather than a type anything passes
around (see `accounting.api.routers.bootstrap.get_store`). What survives of
the old whole-store read is `accounting.taxonomy.seeded_categories`/
`seeded_accounts`: the same `load_*`, with a brand-new user's defaults
seeded first.

One consequence worth knowing: the whole-store save counter went with
the whole-store save, since its granularity is exactly what made two
unrelated edits conflict, and its `accounting.store_versions` table is
gone. Writes are now guarded by row scope, or by a per-row `version`
column where a real lost-update risk exists.

That per-row column is the **only** optimistic-concurrency mechanism left
in this repo: `db.base.check_and_bump_row_version`, against the `version`
column on `accounting.goals` and `accounting.categorization_rules`
(both effects alike). A caller sends the version it last read as
`expected_version` in the request body; a mismatch raises
`db.base.VersionConflictError`, which one handler in
`accounting.api.api` (registered by `install_error_handlers`, since
FastAPI hangs exception handlers off the application rather than off an
`APIRouter`) turns into an HTTP 409. `expected_version=None` opts a
write out of the check entirely (last-write-wins), which is the right
choice for an idempotent toggle. There is no version header, no per-user
counter table, and nothing store-wide — a second, identical mechanism for
`trades.dashboard_settings` was deleted alongside the accounting one; that
row is deliberately last-write-wins (see
`trades.dashboard.settings.save_settings`). The reasoning behind which
fields deserve a check at all is in
`docs/optimistic-concurrency-versioning.md`.

**Wipe-and-reinsert**: delete every row this user owns in a table, then
insert fresh rows for everything currently held in memory. Not "diff and
patch what changed" — the *entire* table is thrown away and rebuilt on
every single save, even a save that only touched one unrelated field.
This is only safe for a table **nothing outside the rewrite references**. A
reinserted row comes back with a *new* `id` — ids are minted, not
recomputed — so any foreign key held elsewhere would be left pointing at a
row that no longer exists. (That is why `accounts`/`categories`/`tags`, which
the ledger does reference, use upsert-and-prune instead.)
This is the technique for **eight** tables, each behind a `replace_*`
function that runs only when a request genuinely submits that whole list:

- `repositories.accounts.replace_opening_balances`: `opening_balances`.
- `repositories.taxonomy.replace_other_assets` /
  `replace_simulator_scenarios`: `other_assets`, `simulator_scenarios`.
- `repositories.planning.replace_budgets`: `budgets`.
- `repositories.interpretation.replace_posting_splits` /
  `replace_posting_merges`: `posting_splits`, `posting_split_legs`,
  `posting_merges`, `posting_merge_duplicates`.

Two of those pairs are parent-and-child (`posting_split_legs` →
`posting_splits`, `posting_merge_duplicates` → `posting_merges`), which
works because parent and child are rewritten in the same transaction, the
parents flushed first so each child reads its parent's brand-new id
straight off the flushed row.

A ninth, `repositories.planning.replace_goal_automations`, is the same
shape but **scoped**: it deletes only the rows for one `direction`, so
rewriting the withdrawal order cannot touch the recurring additions
sharing the table.

Everything a reader might expect to find here and does not:

- **`goals` and `goal_contributions` are not wiped.** There is no
  `replace_goals` and no `replace_goal_contributions`. A goal is written
  by `insert_goal`/`update_goal` (the latter version-checked) and removed
  by `delete_goal`; a contribution by `insert_goal_contributions`/
  `upsert_goal_contribution`/`remove_goal_contribution`. All are per-row.
- **`categorization_rules` is upserted, then pruned within one `effect`**
  — see below.
- **`categorization_rule_exclusions`** is diffed per rule
  (`_sync_rule_exclusions` — add the newly excluded, delete the no-longer
  excluded), never deleted for the whole user.
- **`transfer_links` and `transfer_linked_transactions` are not wiped.**
  They are written by `insert_transfer_links` and removed one link (or
  one rule's links) at a time.
- **`posting_overrides` is not wiped either.** The scoped
  `save_overrides_for_postings` rewrites only the postings it is handed —
  see `docs/accounting/category-tag-merging.md` for why that scoping is
  load-bearing.

Note the difference the split makes: these used to be wiped on *every*
save, however unrelated, because one function wrote every table at once.
Single-entity endpoints (`POST /budgets`, `POST /transfer-rules`,
`PUT /postings/{id}/split`, ...) go through the scoped
`upsert_*`/`insert_*`/`remove_*` functions instead, which touch one row's
worth of state and nothing else.

One table in the interpretation set is neither: `categorization_rules`
carries a `version` column that per-row optimistic concurrency depends on,
so its rows are upserted by raw
`INSERT ... ON CONFLICT (user_id, natural_key) DO UPDATE` whose `SET` clause
omits `version`, then pruned — scoped to one `effect`, so rewriting the
transfer rules cannot delete a category pattern sharing the table (see
`repositories.interpretation.replace_transfer_rules`). A dismissed
`suggestions` row is only ever upserted one at a time.

**Upsert-and-prune** (`db.base.upsert_and_prune`): for each row
currently held in memory, `session.merge()` it — update it in place if a row
with that `(user_id, natural_key)` already exists, insert it if not (see
`db.base.merge_by_natural_key`, which resolves the real key to the row's `id`
so `merge()` can do one or the other) — then, separately,
delete only whichever rows *used to* exist for this user but aren't in
the new set anymore. Nothing not mentioned in the new state gets touched;
nothing mentioned gets torn down and rebuilt. Exactly three tables use
this — `accounts` and `categories` (`repositories.accounts`/`taxonomy`'s
`replace_accounts`/`replace_categories`) and `tags` (`replace_tags`) —
because `postings.account_id`/`category_id`/`subcategory_id` and
`posting_tags.tag_id` are real foreign keys into them. Wiping these the
same way as the 15 above would mean, for one instant mid-transaction, a
category your real transaction history still points at doesn't exist —
Postgres would reject that outright (see "What happens if you delete
something still in use" below), turning every single write into a hard
failure the moment any account/category/tag existed at all.

`accounts` and `categories` additionally reference *themselves*
(`parent_account_id`, `parent_category_id`), so both `replace_*` functions
run the upsert in two passes — parents first, children second — while
pruning against the complete desired set on both passes, so a child
written in the second pass is never swept up by the first pass's prune.

**Which technique to use, for a new table**: wipe-and-reinsert *unless*
something else foreign-keys against this table's `id` — in which case it
has to be upsert-and-prune, or every save touching it would fail the
moment a real reference existed. This is exactly the same "does anything
foreign-key against this?" question the primary-key section above asks,
applied one layer up: at the *write path* instead of the *id* itself.

**Why the 15 wipe-and-reinsert tables stay small**: every one of them
holds *settings you configured by hand* — a budget you typed a number
into, a savings goal you created, a transfer rule you wrote — never
anything an import can add on its own. A heavy user might have dozens of
budgets and goals after years of use; that's still hundreds of rows at
most, not the tens of thousands a transaction history could reach. Small
row counts are exactly what makes "delete everything, reinsert
everything" cheap enough to do on every save without it mattering.

## `transactions`/`postings`: written once at import time, never wiped

Your actual transaction history isn't part of either pattern above. Bulk
writes to it are owned by a separate path,
`accounting.importers.ingest._write_ledger`, called only when you import
a statement (`ingest_csv`, the canonical CSV/Excel importer) or rebuild
the ledger from your raw archive (`rebuild_from_raw_statements`). Clicking
"+" on a goal, editing a budget, renaming a category — none of that ever
touches these two tables, no matter how large your real history has
grown.

Two repositories do reach them, and neither wipes anything:

- `repositories.ledger` is the **read** side — the one SQL statement every
  accounting read path starts from, joining `postings` to `transactions`,
  accounts, categories and budgets and projecting the result into
  `ledger.frame.LEDGER_FRAME_SCHEMA`. It writes nothing.
- `repositories.accounts.insert_manual_transfers` is the one **write**
  outside the import path: a manual transfer has no table of its own, so
  it is recorded as one `origin='manual'` transaction plus two balancing
  postings, additively and keyed by natural key, so re-recording the same
  transfer is a harmless upsert.

`_write_ledger` itself *is* upsert-and-prune, same technique as
`accounts`/`categories`/`tags` and for the same reason —
`posting_overrides`, `posting_splits`, `posting_merges`, and
`goal_contributions.source_posting_id` all foreign-key into `postings` —
but it's reached by an entirely different trigger (an import finishing,
not a settings save), and in practice it's almost always **additive**:
each import call only ever adds the new rows a fresh statement actually
contains, on top of whatever was already there — it doesn't re-derive
your whole history from scratch the way `rebuild_from_raw_statements`
does. A statement's rows are matched to existing ones by their own
content-addressed `natural_key`, not by position in the file or by upload
order; a row already there keeps the `id` it was first given.

**What happens if you import the same statement twice**: every
transaction's id is a hash of the facts that describe it — account, date,
amount, description (`accounting.importers.ingest._fingerprint`) — so
re-importing an identical row produces the identical id both times.
`_merge_ledger`'s `.unique(subset="posting_id", keep="last")` then
collapses the duplicate automatically; nothing is inserted twice, and no
special "have I seen this file before" tracking is needed.

**What happens if two *real, distinct* transactions genuinely look
identical** — two coffees bought at the same shop for the same amount the
same morning — is the harder case this same hash would otherwise get
wrong: both would hash to the same id, and the second would silently
overwrite the first. `_reassign_colliding_transaction_ids` handles this by
counting instead of just hashing: the first row matching an existing
fingerprint reuses that same id (nothing changes); a second, third, ...
row with no more existing matches to reuse gets a counting suffix instead
(`...#2`, `...#3`) and is added as a genuinely new transaction. This also
makes a re-import order-independent — re-uploading the same statement
later, even if the bank happens to print its rows in a different order
that time, still produces the same result, since matching is by how many
rows share a fingerprint, not by position in the file.

## What happens if you delete something still in use

For the three upsert-and-prune tables, deleting a row that's still
referenced elsewhere doesn't quietly corrupt anything — it's rejected
outright by Postgres itself. `postings.category_id`/`subcategory_id` and
`postings.account_id` are declared with no `ondelete` clause, so
Postgres's default behavior (`NO ACTION`) applies: attempting to delete a
category, subcategory, or account any posting still points to fails the
whole transaction with a foreign-key-violation error, before anything is
written. Nothing is silently orphaned, and no posting is ever
auto-deleted as a side effect of deleting something it references.

For `categories`, that rejection is not something the delete/merge paths
have to dance around any more, because **a category in use is never
deleted at all — it is retired**. `accounting.db.core.Category` carries
`retired_at` plus a `superseded_by_category_id` self-reference, and
`repositories.taxonomy.retire_categories` sets them instead of issuing a
`DELETE`: the row leaves the live tree (`load_categories` returns only
live rows, so nothing in the app sees it) while every posting's foreign
key into it stays valid forever. That is what lets a posting's imported
`category_id` be raw provenance that is never rewritten (DB-audit D14):
what a merged-away category *resolves to* is read back at query time from
its successor (`load_category_redirects`), never written down onto the
rows that point at it. `replace_categories`' prune deliberately skips
retired rows, since no caller-supplied tree could ever contain one; and
writing a category again clears its retirement, which is how re-creating
one by name resurrects the same row rather than colliding with it.

`posting_tags` (linking a posting to a tag) is the one deliberate
exception — it's declared with `ondelete="CASCADE"` on both
`posting_tags.tag_id` and `posting_tags.posting_id`. Deleting a tag
cascades to remove the join rows that referenced it (the posting itself
is completely untouched — only the tag *label* disappears from it, the
posting doesn't get deleted, its category doesn't change). Symmetrically,
deleting a posting cascades to remove its own tag associations, which
makes sense here in a way it wouldn't for `category_id`: a join table's
only reason to exist is to describe a relationship between two other
rows, so once either side of that relationship is gone, the row
describing the relationship has nothing left to mean — unlike a
category/account reference, which is a fact *about* the posting itself
and should never silently disappear out from under it.

## The four tables here: `users`, `user_secrets`, `external_identities`, `currencies`

- **`users`** — one row per person using the app, created automatically
  (via `trades.api.webhooks`) the moment someone accepts a Clerk invite —
  every other table's `user_id` column foreign-keys to this table. Just
  `id` (a random UUID, unrelated to anything Clerk-specific) and `email`.
  `users` deliberately knows nothing about Clerk, or any other identity
  provider — that mapping lives in `external_identities` instead (below),
  so this core table (and everything foreign-keyed to it) stays usable
  even if this app ever swaps identity providers.
  `hashed_password`, `is_superuser` and `is_verified` — shaped to match
  what a different auth library expected, before Clerk became this app's
  real identity provider — were dropped by the schema rewrite. The one
  boolean left, `is_active`, is **not** vestigial: it is a soft-delete
  marker. `trades/api/webhooks.py` sets it on `user.created` and clears it
  on `user.deleted`, so a Clerk account that goes away leaves its row (and
  therefore every row foreign-keyed to it) standing as a record instead of
  cascading the whole tenant away.
- **`external_identities`** — one row per `(provider, external_id)` pair,
  e.g. `("clerk", "user_2abc...")`, pointing at the `users.id` it belongs
  to. This is the *only* place any code in this app is allowed to know a
  specific identity provider's own id format exists — `trades/api/auth.py`
  reads it on every request (see "Which user is making this request"
  below), `trades/api/webhooks.py` writes it once per newly provisioned
  user. Deliberately **excluded from Row-Level Security** (unlike every
  other table below) — a session-scoped policy here would block the very
  lookup this table exists to do, since the user id being searched for
  isn't known yet at the point this table needs to be queried (see "Why
  `external_identities` can't have RLS" below for the concrete
  walkthrough). It holds no financial data, only an identity mapping, so
  skipping row-level isolation here is a narrow, deliberate trade-off, not
  an oversight.
  Reassigning someone's account after they're deleted and re-invited in
  Clerk (their Clerk id changes, their internal `users.id` shouldn't) is a
  single-row update here, never a migration touching every other table.
- **`user_secrets`** — one row per `(user_id, key)` pair: a broker token,
  an LLM API key, whatever comes next. `key` is a caller-chosen name like
  `"broker:ibkr"` or `"llm:gemini"`; `kind` groups secrets by shape
  (`"broker_credentials"`, `"llm_api_key"`) so "every broker credential
  across every user" is a real filter, not string-matching on `key`.
  `ciphertext` is never plaintext — see Encryption below. This is the only
  place a credential is ever stored; nothing in this app keeps a secret in
  a `.env` file, a JSON file on disk, or anywhere else.
- **`currencies`** — one row per ISO currency code, keyed by the code
  itself. The odd one out here: it is not tenant data at all, but shared
  reference data, and it lives in `public` for the same reason `users` does
  — both `accounting` and `trades` foreign-key against it, so it cannot sit
  inside either one's schema. All nine `currency` columns across both
  schemas point at it, which is what let the eight copied
  `currency IN (...)` CHECKs be deleted. It needs no `RLS_EXEMPT` entry
  because it has no `user_id`: `db.tenant.is_reference_table` recognises it
  as a shared vocabulary rather than a tenant table with a hole in it.
  Seeded from `db.currency.CURRENCY_REFERENCE`, the same data the
  `CurrencyCode` literal projects, in both the baseline migration and
  `create_all`.

## Two Postgres roles, and why there are two

Every table lives in one Postgres database, but the app connects to it as
one of **two different roles**, depending on what's running:

- **`finance`** (env var `DATABASE_URL`) — the role that owns every table
  and runs every migration. Alembic always connects as this one, since
  schema changes need its privileges.
- **`app_runtime`** (env var `DATABASE_URL_APP`) — an ordinary, restricted
  role with only `SELECT`/`INSERT`/`UPDATE`/`DELETE` granted. The running
  API process (`db.session.get_engine`) always connects as this one, never
  as `finance`.

**Why two roles instead of one:** Postgres superusers, and a table's own
*owner*, always bypass Row-Level Security — no policy can override that,
by design. `finance` is both (it owns every table, and in this project's
Docker setup it's also a cluster superuser), so if the running app
connected as `finance`, every RLS policy below would silently do nothing:
the queries would still only ever return the right rows, but only because
application code happened to filter correctly, not because Postgres was
actually enforcing it. `app_runtime` is a normal, non-owner role — RLS
applies to it unconditionally.

`settings.py` requires each of `DATABASE_URL`/`DATABASE_URL_APP`
independently, with **no fallback from one to the other** — if
`DATABASE_URL_APP` isn't set, the app refuses to start rather than quietly
falling back to running as the superuser. A half-configured state (RLS
policies exist, but the running app isn't actually subject to them) is
worse than refusing to start, so it's not allowed to happen silently.

## Row-Level Security (RLS): the actual backstop

RLS is **derived from the schema**, not opted into per table. `db.tenant`
owns the derivation, and `src/migration/versions/000000000001_baseline_schema.py`
just emits what it computes:

- `tenant_tables(Base.metadata)` walks the metadata and returns every table
  that has a `user_id` column (`OWNER_COLUMN`), with `public.users`
  special-cased on its own `id`.
- `enable_rls_statements(...)` turns each one into the three statements
  below. The policy name is one constant, `POLICY_NAME = "user_isolation"`.

```sql
ALTER TABLE some_table ENABLE ROW LEVEL SECURITY;
ALTER TABLE some_table FORCE ROW LEVEL SECURITY;
CREATE POLICY user_isolation ON some_table
  USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
  WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);
```

Two details in that SQL are load-bearing. `FORCE` matters as much as
`ENABLE`: migrations run as the table owner, and a plain `ENABLE` does not
apply to the owner, so without `FORCE` the policy would be silently inert for
exactly the role that created it. And the `NULLIF(..., '')` is there because
`app.current_user_id` is an undeclared placeholder GUC — Postgres resets it to
the empty string rather than `NULL`, so a bare cast would raise instead of
matching nothing. Failing closed (zero rows) beats a 500, and beats returning
everything.

`public.users` is the one table whose owner column is `id` rather than
`user_id` — it *is* the tenant — and `tenant_tables` special-cases it.

**A table therefore cannot ship with a `user_id` and no policy.** It used to
be able to: RLS was applied by hand-copying a table list into each migration,
and three tables — `transfer_links`, `transfer_linked_transactions`,
`categorization_rule_exclusions` — shipped with a `user_id` column and no
policy, which nothing noticed (VISION-AUDIT T3). Deriving the list from the
metadata removes the step a human could skip.

There are exactly two ways a table can legitimately have no policy, and both
are explicit rather than accidental:

- **It is reference data.** `db.tenant.is_reference_table` says so: no
  `user_id` and not `users`, so it is a shared vocabulary
  (`public.currencies`, `accounting.institutions`, `trades.securities`) with
  no tenant rows to isolate.
- **It is in `db.tenant.RLS_EXEMPT`**, a dict keyed by `(schema, table)` whose
  value is a mandatory prose reason. `public.external_identities` is the only
  entry: it must be readable *before* the acting user is known, so a policy
  keyed on `user_id` could never match. That is an accepted hole rather than a
  solved problem — see "Why `external_identities` can't have RLS" below for the
  compensating controls.

`tests/db/test_rls_coverage.py` migrates its own scratch database and asserts
the live `pg_policies` matches — every tenant table forced, and every
unprotected table either reference data or a declared exemption.

It runs in **both** backend CI jobs, which is the point — the `test` job sets
`DATABASE_URL_APP` as well as `DATABASE_URL_TEST` precisely so these tests
execute there rather than erroring out. The module does still skip itself when
`DATABASE_URL_TEST` is unset, which is the "no Postgres on this machine at all"
case; what it deliberately does *not* do is skip when Postgres is present but
half-configured, because a green CI run that silently proved nothing is how the
three unprotected tables shipped in the first place.

This means: even if a query somewhere in the code forgot its own
`WHERE user_id = ...` filter, Postgres itself still refuses to return
another user's rows — the database enforces isolation as a second,
independent layer, not just the application's own filtering.

For this to mean anything, Postgres has to know *which* user is making the
current request. `db.session.get_db` (the FastAPI dependency every route
uses to get a database session) sets that, once, right at the start of
every request — see the next section for exactly where that id comes from:

```python
session.execute(
    text("SELECT set_config('app.current_user_id', :user_id, true)"),
    {"user_id": str(user_id)},
)
```

The `true` third argument (`is_local`) scopes this to the current
transaction only — it's automatically forgotten the moment the request's
transaction ends, so it can never leak into a pooled connection's next,
unrelated request.

## Which user is making this request — the actual trace, step by step

`db.current_user.get_current_user_id` is a **name**, not really a
function meant to run — its own body just raises an error
unconditionally. Nothing in the real, running app ever actually executes
that body. Concretely, here's what happens for one real request — say,
Bob (a real invited user) loads the dashboard, which calls
`GET /api/v1/trades/overview`:

1. The endpoint declares it needs two things, both *by name*, not by
   calling anything directly:
   ```python
   def get_overview(
       session: Annotated[Session, Depends(get_db)],
       user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
       ...
   ```
2. To build `session`, FastAPI has to run `get_db` first — but `get_db`
   *itself* asks for a `user_id`, via that same name:
   ```python
   def get_db(user_id: Annotated[uuid.UUID, Depends(get_current_user_id)]) -> Iterator[Session]:
   ```
   So before `get_db` can even start, FastAPI has to resolve
   `get_current_user_id` first.
3. Here's the swap that decides what actually runs — set once, when the
   app starts (`trades/api/api.py`):
   ```python
   app.dependency_overrides[get_current_user_id] = resolve_current_user_id
   ```
   This says: "wherever anyone asks for `get_current_user_id`, hand them
   `resolve_current_user_id` instead." That's the function that actually
   runs — it lives in `trades/api/auth.py` (the one file in this repo
   that's allowed to know Clerk exists at all). It checks Bob's login token
   is real (its own signature, expiry, everything Clerk's SDK verifies),
   pulls Bob's Clerk id (`user_...`) out of the now-trusted token, and
   opens its own database session to look that id up in
   `external_identities` (`db.external_identities.lookup_user_id`) — a
   **real lookup, not a computed formula**. `external_identities` is the
   one table in this schema deliberately excluded from the RLS policy
   described above, precisely so this lookup can run *before* Bob's
   internal id is known — the chicken-and-egg problem a `users`-table
   lookup would otherwise hit (RLS on `users` would block the very
   `WHERE ... = Bob's id` query trying to discover what Bob's id is). If
   no row matches (a Clerk session for someone who was never provisioned —
   see "How a new user gets provisioned" below for the webhook that does
   that), the request is rejected with 401 rather than falling back to
   anyone else's id.
4. That id flows back into `get_db`, which uses it to set
   `app.current_user_id` (the RLS section above) before handing back a
   working session.
5. Only now does the actual endpoint body run, with `session` already
   scoped to Bob and `user_id` set to Bob's own id.

The placeholder body (the one that raises) exists so a *missing* swap
fails loudly and immediately — every request erroring out — rather than
silently resolving to nothing or to the wrong person. If step 3's
override line were ever accidentally deleted, this design means the app
breaks obviously, on the first request, instead of quietly misbehaving.

## How a new user gets provisioned — the actual trace, step by step

The sign-in trace above assumes Bob's `users`/`external_identities` rows
already exist. Here's how they get created in the first place, the one
time it happens — say, Alice invites a new person, Carol:

1. Alice sends Carol an invite from the Clerk Dashboard (or
   `clerk api /invitations`).
2. Carol clicks the email link and finishes signing up — entirely on
   Clerk's own servers; this app is never involved yet.
3. The moment Carol's account is created, Clerk calls this app directly:
   `POST /api/webhooks/clerk`, carrying Carol's new Clerk id
   (`user_...`) and her email. This is the one route in the app *not*
   gated by a Clerk session (`trades.api.api` mounts it separately) —
   Clerk's servers are calling it directly, so there's no session to
   check. Authenticity instead comes from a Svix-signed payload,
   verified against `CLERK_WEBHOOK_SIGNING_SECRET`.
4. `trades.api.webhooks._provision_user` checks `external_identities`
   first: "is there already a row for this Clerk id?" — guards against
   Clerk redelivering the same event later and provisioning Carol twice.
5. If no row exists yet: generate a brand-new random UUID, insert a
   `users` row under that id with Carol's email, and insert one row into
   `external_identities` linking `("clerk", Carol's Clerk id)` to that
   new `users.id`.

Nothing else happens at signup — no other table is touched. From this
point on, every one of Carol's requests follows the sign-in trace above,
step 3 of which is the read side of the exact row step 5 here writes.

One consequence worth knowing: if Carol is later deleted in Clerk and
re-invited under the same email, she gets a **new** Clerk id, but step 4
only checks by Clerk id — so a naive re-provisioning would create a
second, disconnected `users` row, orphaning anything tied to the first
one. `user.deleted` *is* handled — `webhooks._deactivate_user` clears
`is_active` on the matching `users` row — but that is a soft delete, not
a re-link: it deliberately leaves `external_identities` pointing at the
old Clerk id, because the row exists to preserve what happened rather
than to be recycled. So relinking is still not automatic, and the manual
step it requires is a single-row
update, reassigning her existing `external_identities` row to the new
Clerk id (never a migration touching every other table, which is the
whole reason this table exists as a separate mapping instead of a column
on `users`) — see `db.external_identities`'s own docstring and
`tests/db/test_external_identities.py`'s test of exactly that.

## Why `external_identities` can't have RLS

Look at step 3 of the sign-in trace above: `resolve_current_user_id`
queries `external_identities` for Bob's Clerk id *before* it knows Bob's
internal `user_id` — finding that id is the whole point of the query.

If `external_identities` had the same RLS policy every other table gets,
Postgres would silently rewrite that query to also require
`AND user_id = current_setting('app.current_user_id', true)::uuid` — RLS
bolts that filter onto every query against the table, regardless of what
was actually asked for. But `app.current_user_id` hasn't been set yet at
this point in the request — setting it is literally the *next* step,
using the answer this query is trying to produce. The added filter would
compare against nothing (or a stale leftover value from a previous
request on a pooled connection, if `get_db` somehow didn't reset it), the
real row would get filtered out, and the lookup would come back empty —
not because Bob has no account, but because the query asked "give me my
own row" before anyone knew who "my" was. Every request would then hit
step 3's 401 ("no account found for this session yet"), for every real,
already-provisioned user, forever.

So `external_identities` is the one entry in `db.tenant.RLS_EXEMPT` (see
"Row-Level Security" above) — a dict that requires a prose reason next to
every exemption, so the absence of a policy is a recorded decision rather
than something a reader has to infer from a table's absence from a list.
There's no "turn RLS off" command involved: the baseline simply never emits
a policy for an exempt table.

This is a genuine hole in the isolation guarantee, not a solved problem, and
worth being precise about: the `(provider, external_id)` primary key enforces
*uniqueness*, not access control. It stops one external account mapping to two
internal users; it does not stop a query reading a row that isn't yours. What
makes the hole acceptable is the three compensating controls, none of which is
the primary key:

- **The table holds no financial data** — only `provider`, `external_id`,
  `user_id`. Reading every row of it reveals who has an account, not what
  anyone owns.
- **`db.external_identities` is the only code that touches it**, and it offers
  no listing or enumeration path — just exact-match lookup on a
  `(provider, external_id)` pair, returning an opaque internal id.
- **That pair has to come from somewhere trusted**: a Clerk session JWT this
  app has already verified (`trades.api.auth`) or a signature-checked webhook
  (`trades.api.webhooks`). A caller cannot supply another user's pair without
  first forging one of those.

Every table that does hold real user data has a forced policy, derived from its
`user_id` column and asserted by `tests/db/test_rls_coverage.py`.

## Encryption: what's protected, and how

`db.encryption.SecretsEncryptor` encrypts every value before
`db.secrets.set_secret` writes it, and decrypts it after
`db.secrets.get_secret` reads it back — Postgres itself never sees a
broker token or API key in plaintext, only ciphertext.

It uses **Fernet** (from the `cryptography` library): symmetric
authenticated encryption — the same key both encrypts and decrypts, and
every encrypted value carries a checksum, so a tampered/corrupted
ciphertext fails to decrypt loudly (`InvalidToken`) instead of silently
returning garbage.

Three settings control it, read from the environment:

| Env var | Meaning |
|---|---|
| `APP_SECRETS_ENCRYPTION_KEY` | The current key. Every *new* secret is encrypted under this one. |
| `APP_SECRETS_ENCRYPTION_KEY_VERSION` | An integer tag for the current key (starts at `1`). Stored on every row (`user_secrets.encryption_key_version`), so it's always known which key encrypted a given row. |
| `APP_SECRETS_ENCRYPTION_KEYS_PREVIOUS` | A JSON object mapping an old version number to the key that used to be current, e.g. `{"1": "<old key>"}`. Populated once you rotate to a new key — kept around only so rows still on the old version keep decrypting. |

**Generating a key** (needed once, at first setup):

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Rotating from version 1 to version 2**, once you want to retire the
current key:

1. Generate a new key with the command above.
2. Move the *current* key into the previous-keys map, keyed by its own
   version number:
   `APP_SECRETS_ENCRYPTION_KEYS_PREVIOUS={"1":"<the key that used to be APP_SECRETS_ENCRYPTION_KEY>"}`
3. Set `APP_SECRETS_ENCRYPTION_KEY` to the new key, and bump
   `APP_SECRETS_ENCRYPTION_KEY_VERSION` to `2`.
4. Restart/redeploy the app. No migration needed — these three values are
   the entire state.

After that: every row written before the rotation still decrypts fine
(its `encryption_key_version` is `1`, found in the previous-keys map).
Every row written from now on is encrypted under version `2`. Existing
rows are **not** automatically re-encrypted — nothing walks the table and
upgrades old rows to the new key on its own. If you ever want to fully
retire an old key (delete it from `APP_SECRETS_ENCRYPTION_KEYS_PREVIOUS`
for good), something has to first read every row still on that version,
decrypt it under the old key, and re-encrypt it under the current one —
that bulk-rewrite doesn't exist yet, so don't delete a version from the
previous-keys map until you've confirmed nothing still needs it.

## Backups: how they work, and how to restore one

`db.backup.run_backup()` (run as `python -m db.backup`) does four things,
in order:

1. **Dumps** the whole database with `pg_dump --format=custom`, connecting
   as `finance` (`DatabaseSettings`, never `AppRuntimeDatabaseSettings`) —
   a backup has to see every row in every table regardless of RLS, which
   is exactly the privilege the restricted `app_runtime` role must never
   implicitly have.
2. **Verifies the dump is actually restorable** (`verify_backup_restorable`)
   before doing anything else with it: it creates a throwaway scratch
   database on the same Postgres server (`CREATE DATABASE
   backup_verify_<uuid>`, using `finance`'s superuser/`CREATEDB`
   privileges), restores the dump into it with `pg_restore --clean
   --if-exists`, then drops the scratch database again (`DROP DATABASE ...
   WITH (FORCE)`) regardless of whether the restore succeeded. If
   `pg_restore` fails, the error propagates immediately and `run_backup`
   stops right there — it does **not** upload the bad dump or touch any
   existing backups. This is deliberate: a broken backup should never
   silently replace, or even sit alongside, a good one; a human needs to
   see this failure, not have it hidden behind an apparently-successful
   run.
3. **Names** the dump by timestamp (`{UTC timestamp}.dump`) and **uploads**
   it to Cloudflare R2 under `backups/postgres/`, if R2 is configured
   (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
   `R2_BUCKET_NAME`, `R2_ENDPOINT_URL` all set) — otherwise it falls back
   to writing the same file to local disk, under `data/backups/postgres/`.
   Only reached once step 2 has passed.
4. **Prunes** old backups (`prune_old_backups`) down to the newest
   `BACKUP_RETENTION_COUNT` (`BackupSettings`, defaults to 14 if unset) —
   whichever backend is active (R2 or local disk), backups are sorted
   newest-first by their timestamp-prefixed filename (already
   lexicographically = chronologically sortable) and anything beyond that
   count is deleted. The sort-and-prune itself lives in
   `db.timestamped_backups`, shared with the two cache-backup modules that
   need the identical rule. Only ever runs after a successful verify +
   upload, so a failed backup never causes a good one to be pruned away.

**This does not run by itself.** `python -m db.backup` is just a command —
nothing in this repo schedules it automatically. Making it run on a
recurring basis is an infrastructure-level setup step, done once, outside
of this code; it isn't part of what `docker compose up` brings up on its
own — a `crontab` entry on the deploy VM runs it once daily, at 3:00 UTC.

**Restoring** a dump — this is destructive (it drops and recreates
objects before loading), so only run it against a database you actually
intend to overwrite:

```bash
pg_restore --clean --if-exists -d <DATABASE_URL> <path-to-dump-file>
```

- `<DATABASE_URL>` — the `finance` (superuser) connection string, not
  `DATABASE_URL_APP` — restoring needs full privileges, the same as
  backing up.
- `<path-to-dump-file>` — wherever the dump actually is: a path under
  `data/backups/postgres/` if it came from the local-disk fallback, or a
  file you've downloaded from R2 first if it was uploaded there (`pg_dump`
  output isn't something Postgres can pull directly from an S3-compatible
  bucket — it has to be a local file `pg_restore` can open).

`pg_dump`/`pg_restore` both need to run somewhere with network access to
Postgres and the `postgresql-client` tools installed — how exactly to
reach that (which container, which host) depends on whichever environment
you're restoring into.
