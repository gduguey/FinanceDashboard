# Database schema

The standing reference for the Postgres schema: every table, column, key,
`CHECK`, foreign key, index and RLS policy, and the reasoning behind the
cross-cutting decisions. This is what the database *is* — not an account of how
it got here. The rewrite narrative that used to share these pages is in
[archive/schema-rewrite.md](archive/schema-rewrite.md).

**This document is hand-maintained, with no generator.** It is verified against
the SQLAlchemy models and against a scratch database built by
`alembic upgrade head` and introspected with `\d+`, `pg_indexes`, `pg_policies`
and `pg_constraint` — see §6 for the commands. Treat a disagreement between this
page and the models as a bug in this page.

**Its table inventory is gated** (item E8):
`tests/db/test_schema_doc_drift.py` asserts §3's headings, §2's aggregate map
and §1's counts against `Base.metadata`, in both directions, so a table added,
removed or renamed without this page moving fails CI. Everything else here —
every column, key, index and every line of §4's reasoning — is still only as
current as the last manual pass, and no parser can see a sentence go stale.

Last verified against the models: **1.11.1**.

---

## 1. Orientation

One Postgres database backs a multi-tenant personal-finance product with two
independent halves: a double-entry cash ledger (`accounting`) and a brokerage
transaction ledger (`trades`). Every row belongs to one user, and isolation is
enforced by the database rather than by application `WHERE` clauses.

| Schema | Tables | What lives there |
|---|---|---|
| `public` | 4 | Identity (`users`, `external_identities`, `user_secrets`) and the currency dimension |
| `accounting` | 28 | The cash ledger, its taxonomy, its interpretation overlays, the resolved projection, planning, the institution dimension |
| `trades` | 6 | Broker connections, the brokerage event ledger, the sync-run record, dashboard settings, the security dimension |

**38 application tables** (39 counting `alembic_version`, which is Alembic's own
bookkeeping and lives in `public`). Postgres 16, both locally and in
`deploy/docker-compose.yml`.

Schema history is the baseline plus three revisions:
`src/migration/versions/000000000001_baseline_schema.py` (`down_revision = None`),
then `000000000002_resolved_posting_projection.py`, which adds §3.10's two
tables and their triggers; `000000000003_sync_runs.py`, which adds
`trades.sync_runs`; and
`000000000004_narrow_the_categories_staleness_trigger.py`, which changes one
trigger function's body and no table at all.
The previous 28-revision chain — which contained a drop-and-recreate of both
schemas partway through and hand-copied RLS into six separate revisions — was
collapsed, not carried forward. The project is pre-launch with no data to
preserve.

**What the rewrite changed, in one paragraph.** The single whole-store
load-mutate-save that fronted ~25 accounting tables at once was dissolved into
independent aggregates, each with its own repository module under
`src/accounting/repositories/`. Money became exact `NUMERIC`/`Decimal` at the
persistence and domain layers with one named boundary where it becomes float for
analytics. Primary keys became database-minted time-ordered UUIDs instead of
content-derived hashes, with `natural_key` + `UNIQUE (user_id, natural_key)`
remaining the addressing mechanism. Row-Level Security stopped being a
hand-maintained list and is now derived from the presence of a `user_id`
column. Concurrency collapsed from two whole-store counter tables plus a request
header down to per-row `version` columns. Every foreign key got an index, every
table got `created_at`/`updated_at`, ten tables were merged or deleted, three
dimension tables replaced repeated strings and fifteen restated `CHECK`s, and a
long list of invariants that Python used to police — two-level tree depth,
category/subcategory pairing, per-transaction zero-sum, "one remainder"
automation, split-leg ordering — are now refused by the engine.

---

## 2. The aggregate roots

The spine of the schema. Each aggregate loads and writes its own tables
independently; nothing outside an aggregate's module writes its tables. To find
a table, ask which aggregate owns it.

| Aggregate | Tables | Owning code |
|---|---|---|
| **Identity** | `public.users`, `public.external_identities`, `public.user_secrets` | `src/db/models.py`, `src/db/external_identities.py`, `src/db/secrets.py` |
| **Dimensions** (shared reference data, no tenant) | `public.currencies`, `accounting.institutions`, `trades.securities` | `src/db/models.py` (`Currency`), `src/db/currency.py`, `src/accounting/db/institutions.py`, `src/trades/db/models.py` (`Security`); write path `db.base.ensure_reference_rows` |
| **Accounts** | `accounting.accounts`, `accounting.opening_balances` | `src/accounting/repositories/accounts.py` |
| **Taxonomy** | `accounting.categories`, `accounting.tags`, plus `accounting.other_assets` and `accounting.simulator_scenarios` (homeless, see below) | `src/accounting/repositories/taxonomy.py`; pure tree logic in `src/accounting/taxonomy.py` |
| **Accounting ledger** | `accounting.transactions`, `accounting.postings`, `accounting.posting_tags` | `src/accounting/importers/ingest.py` (`load_ledger`, `_write_ledger`); the zero-sum trigger in `src/accounting/db/triggers.py` |
| **Interpretation** | `accounting.categorization_rules`, `accounting.categorization_rule_exclusions`, `accounting.posting_overrides`, `accounting.posting_override_tags`, `accounting.posting_splits`, `accounting.posting_split_legs`, `accounting.posting_merges`, `accounting.posting_merge_duplicates`, `accounting.transfer_links`, `accounting.transfer_linked_transactions`, `accounting.suggestions` | `src/accounting/repositories/interpretation.py`; stage ordering in `src/accounting/precedence.py` |
| **Planning** | `accounting.budgets`, `accounting.goals`, `accounting.goal_contributions`, `accounting.goal_automations` | `src/accounting/repositories/planning.py` |
| **Resolved projection** | `accounting.resolved_postings`, `accounting.resolved_postings_dirty` | `src/accounting/db/projection.py` (the tables and their staleness triggers), `src/accounting/repositories/projection.py` (the drain and every read of it) |
| **Trades ledger** | `trades.broker_connections`, `trades.ledger_events`, `trades.ledger_event_trade_details`, `trades.sync_runs` | `src/trades/db/models.py`, `src/trades/brokers/ibkr/`; sign convention in `src/trades/ledger/signs.py`; the sync runner in `src/trades/api/sync_runs.py` |
| **Settings / usage** | `trades.dashboard_settings`, `accounting.llm_usage` | `src/trades/dashboard/settings.py`, `src/accounting/llm/usage.py` |

Two placements are explicitly provisional, recorded in
`src/accounting/repositories/taxonomy.py`'s docstring: `other_assets` and
`simulator_scenarios` sit in the taxonomy module for want of a better home —
neither is referenced by anything and neither is taxonomy. They move when an
aggregate is named for them.

Manual transfers have **no table**. They are a projection
(`load_manual_transfers` / `insert_manual_transfers` in
`repositories/accounts.py`) over ordinary `transactions` with
`origin = 'manual'` and their two balancing `postings`. They live in the
accounts aggregate because closing an account is the only thing that creates
one.

---

## 3. Every table

Conventions used throughout, so they are not repeated per table:

- **`id`** — `uuid NOT NULL DEFAULT public.uuid7()`, primary key, on every
  aggregate-root table.
- **`user_id`** — `uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE`.
  Its presence is what makes a table tenant-scoped; every such table carries
  `ENABLE` + `FORCE ROW LEVEL SECURITY` and one policy named `user_isolation`
  with both `USING` and `WITH CHECK`.
- **`created_at` / `updated_at`** — `timestamptz NOT NULL DEFAULT now()` on all
  38 tables; `updated_at` additionally carries the mapper's `onupdate=now()`.
- **`natural_key`** — `varchar NOT NULL`, with
  `UNIQUE (user_id, natural_key)`. The domain's addressing mechanism.
- **Index naming** — `ix_<table>_<cols>`; every non-unique index listed below is
  `(foreign_key, user_id)` unless stated otherwise.
- Ledger-facing dates are `timestamp without time zone` (naive) on purpose;
  audit timestamps are `timestamptz`. Two columns are true `date`.
- `varchar` is unbounded (no length limit) everywhere.

Column lists below omit `created_at`/`updated_at`.

### 3.1 Identity — `public`

#### `public.users` — one person using the app. Tenant-scoped by its own `id`.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | `DEFAULT uuid7()` |
| `email` | `varchar NOT NULL` | Deliberately **not** unique — display information, not a lookup key |
| `is_active` | `boolean NOT NULL` | Soft-delete marker set by the Clerk `user.deleted` webhook |

- RLS: forced, policy compares `id` (not `user_id`) — the one special case in
  `db.tenant._USERS_TABLE`.
- Referenced by all 34 tenant tables.
- No indexes beyond the primary key.

#### `public.external_identities` — provider account → internal user.

| Column | Type | Notes |
|---|---|---|
| `provider` | `varchar NOT NULL` | PK part |
| `external_id` | `varchar NOT NULL` | PK part |
| `user_id` | `uuid NOT NULL` | FK → `users.id` `ON DELETE CASCADE` |

- PK `(provider, external_id)`; index `ix_external_identities_user_id (user_id)`.
- **The one RLS exemption in the schema.** No policy, by design — see §4.2.

#### `public.user_secrets` — one named, application-encrypted credential per user.

| Column | Type | Notes |
|---|---|---|
| `user_id` | `uuid NOT NULL` | PK part |
| `key` | `varchar NOT NULL` | PK part, e.g. `broker:ibkr`, `llm:gemini` |
| `kind` | `varchar NOT NULL` | Groups secrets by shape independently of `key` |
| `ciphertext` | `varchar NOT NULL` | Fernet-encrypted before it reaches the column (`db/encryption.py`) |
| `encryption_key_version` | `integer NOT NULL` | Which key encrypted this row, so the key can rotate |

- PK `(user_id, key)`. Tenant-scoped, forced policy. No other indexes.

### 3.2 Dimensions — shared reference data

All three have **no `user_id`**, therefore no RLS policy *and no exemption
entry* — they are not tenant tables at all (`db.tenant.is_reference_table`).
That predicate also stops `db.indexes` minting an index behind each of the
twelve columns that reference them (§4.5). Each is a single-column table
because nothing else about the entity is known or populated anywhere in the
codebase; inventing columns nothing fills would make the table a promise
rather than a fact.

#### `public.currencies` — the currency list every `currency` column references.

| Column | Type | Notes |
|---|---|---|
| `code` | `varchar` PK | ISO 4217, e.g. `USD`. The value every referencing column stores — no surrogate id, so no join to render an amount |
| `symbol` | `varchar NOT NULL` | e.g. `$` |
| `decimal_places` | `smallint NOT NULL` | Presentation precision (`db.money.round_to_currency`) |

- **Seeded** by the migration and by `create_all`, from
  `db.models.CURRENCY_SEED_STATEMENTS`: `USD`/`$`/2 and `EUR`/`€`/2. Verified
  in the scratch database — 2 rows.
- Referenced by nine columns across both schemas: `accounts.currency`,
  `budgets.currency`, `goals.target_currency`, `goal_automations.currency`,
  `goal_contributions.currency`, `other_assets.currency`,
  `postings.currency`, `simulator_scenarios.currency`,
  `trades.ledger_events.currency`.

#### `accounting.institutions` — where an account is held.

| Column | Type | Notes |
|---|---|---|
| `code` | `varchar` PK | The name as `accounts.institution` stores it, e.g. `chase`, or `internal` for a placeholder counterparty |

- **Not seeded** — open vocabulary. Rows are created by the write path that
  names one (`db.base.ensure_reference_rows`, one
  `INSERT ... ON CONFLICT DO NOTHING` over the batch). 0 rows in a fresh
  database.
- Deliberately a table rather than a `CHECK`: the value both *selects code*
  (`importers.ingest._STANDARDIZERS` is keyed by
  `(institution, account_kind)`) and is *typed by a user*, and the allowed set
  is not knowable when a migration runs.
- `trades.broker_connections.broker` is deliberately **not** folded in here:
  that vocabulary is closed by what integrations exist.
  `dashboard_settings.hysa_bank_id` is not either — its authoritative list is
  a scraped on-disk cache, so a foreign key would claim authority Postgres
  does not have.

#### `trades.securities` — instruments a ledger event or benchmark override can name.

| Column | Type | Notes |
|---|---|---|
| `symbol` | `varchar` PK | Ticker as the broker reports it, e.g. `VOO` |

- Not seeded; open vocabulary, filled by
  `brokers.ibkr.main._write_ledger` and `dashboard.settings.save_settings`.
- Referenced by `ledger_events.symbol` and
  `dashboard_settings.benchmark_symbol_override`. The second was the visible
  failure this closes: a mistyped benchmark used to be stored happily, fetch an
  unknown symbol, cache the empty result, and produce a silent zero-row
  comparison instead of an error.

### 3.3 Accounts

#### `accounting.accounts` — a place money can sit, or a virtual counterparty.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid NOT NULL` | |
| `natural_key` | `varchar NOT NULL` | |
| `name` | `varchar NOT NULL` | |
| `kind` | `varchar NOT NULL` | CHECK-constrained to 10 values |
| `institution` | `varchar NOT NULL` | FK → `accounting.institutions(code)` |
| `currency` | `varchar NOT NULL` | FK → `currencies(code)` |
| `last_four` | `varchar NULL` | |
| `parent_account_id` | `uuid NULL` | Self-reference; a vault names the savings account it is a sub-balance of |
| `depth` | `smallint NOT NULL` | `GENERATED ALWAYS AS (CASE WHEN parent_account_id IS NULL THEN 1 ELSE 2 END) STORED` |
| `parent_depth` | `smallint NULL` | `GENERATED ALWAYS AS (CASE WHEN parent_account_id IS NULL THEN NULL ELSE 1 END) STORED` |
| `broker_connection_id` | `uuid NULL` | FK → `trades.broker_connections(id) ON DELETE SET NULL` — the whole seam between the two ledgers |
| `meta` | `jsonb NOT NULL` | |
| `closed` | `boolean NOT NULL` | |

- Unique: `uq_accounts_user_natural_key (user_id, natural_key)`,
  `uq_accounts_id_depth (id, depth)` (the FK target below).
- CHECKs:
  - `ck_accounts_kind` — `kind IN ('checking','savings','credit_card','vault','cash','loan','income_source','expense_payee','external_investment','other_asset')`
  - `ck_accounts_broker_link_is_an_investment` — `broker_connection_id IS NULL OR kind = 'external_investment'`
- FKs: `user_id`, `institution`, `currency`, `broker_connection_id`, and the
  composite `FOREIGN KEY (parent_account_id, parent_depth) REFERENCES accounts (id, depth)`
  — which is what makes a vault-of-a-vault unrepresentable.
- Indexes: `ix_accounts_parent_account_id_user_id`,
  `ix_accounts_broker_connection_id_user_id`.
- Written by `upsert_and_prune` in two passes (parents before children).

#### `accounting.opening_balances` — the balance an account already had before its postings start.

| Column | Type |
|---|---|
| `id` | `uuid` PK |
| `user_id` | `uuid NOT NULL` |
| `account_id` | `uuid NOT NULL` → `accounts(id) ON DELETE CASCADE` |
| `amount` | `numeric(18,4) NOT NULL` |
| `as_of_date` | `timestamp NOT NULL` |

- Unique `uq_opening_balances_user_account (user_id, account_id)`; index
  `ix_opening_balances_account_id_user_id`. No `natural_key` — an opening
  balance is a property of one account and is addressed by it.

### 3.4 Taxonomy

#### `accounting.categories` — one node in the two-level category tree.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid NOT NULL` | |
| `natural_key` | `varchar NOT NULL` | |
| `name` | `varchar NOT NULL` | |
| `classification` | `varchar NOT NULL` | `income` \| `expense` |
| `parent_category_id` | `uuid NULL` | Adjacency list |
| `depth` | `smallint NOT NULL` | Generated stored, `1` root / `2` child |
| `parent_depth` | `smallint NULL` | Generated stored, `1` when there is a parent |
| `color` | `varchar NOT NULL` | |
| `retired_at` | `timestamptz NULL` | When the category left the live tree |
| `superseded_by_category_id` | `uuid NULL` | FK → `categories(id) ON DELETE SET NULL`. Set = merged; `NULL` with `retired_at` set = deleted |

- Unique: `(user_id, natural_key)`, `(id, depth)`,
  **`(id, parent_category_id)`** — the last exists solely to be a foreign-key
  target for the `(subcategory_id, category_id)` pair on six other tables.
- CHECKs: `ck_categories_classification`;
  `ck_categories_successor_requires_retirement` —
  `superseded_by_category_id IS NULL OR retired_at IS NOT NULL`.
- FKs: `user_id`, `superseded_by_category_id`, and the composite
  `(parent_category_id, parent_depth) REFERENCES categories (id, depth)`.
- Indexes: `ix_categories_parent_category_id_user_id`,
  `ix_categories_superseded_by_category_id_user_id`.
- **Categories are retired, never deleted**, once anything is filed under
  them. `postings.category_id` is a real foreign key holding the category the
  statement was imported with, so a `DELETE` has to be impossible rather than
  arranged. Writing the same natural key again clears the retirement and
  revives the row.
- Referenced by 14 foreign keys from 7 tables.

#### `accounting.tags` — a cross-cutting label independent of the category tree.

`id`, `user_id`, `natural_key`, `name varchar NOT NULL`. Unique
`(user_id, natural_key)`. No other indexes. Referenced by `posting_tags` and
`posting_override_tags`.

#### `accounting.other_assets` — a manually-entered net-worth line with no history.

| Column | Type |
|---|---|
| `id` `user_id` `natural_key` | as usual |
| `name` | `varchar NOT NULL` |
| `value` | `numeric(18,4) NOT NULL` |
| `currency` | `varchar NOT NULL` → `currencies(code)` |
| `note` | `varchar NOT NULL` |

- CHECK `ck_other_assets_value_is_not_negative` — `value >= 0`. This is an
  *asset* line; liabilities are `credit_card`/`loan` accounts with signed
  postings, so a negative value here would be silently subtracted from assets
  rather than counted as debt.

#### `accounting.simulator_scenarios` — saved inputs to the compound-interest projector.

| Column | Type |
|---|---|
| `id` `user_id` `natural_key` | as usual |
| `name` | `varchar NOT NULL` |
| `initial_capital`, `monthly_contribution` | `numeric(18,4) NOT NULL` |
| `horizon_years`, `annual_rate_pct` | `numeric(12,6) NOT NULL` |
| `compounding_frequency` | `varchar NOT NULL` — `annually` \| `monthly` \| `daily` |
| `currency` | `varchar NOT NULL` → `currencies(code)` |

- CHECK on `compounding_frequency`. No non-unique indexes.

### 3.5 The accounting ledger

#### `accounting.transactions` — one economic event, owning its date, description and balance.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid NOT NULL` | |
| `natural_key` | `varchar NOT NULL` | |
| `posted_at` | `timestamp NOT NULL` | The one day the event happened on |
| `description` | `varchar NOT NULL` | What the statement (or person) said it was |
| `origin` | `varchar NOT NULL` | `imported` \| `manual` — the provenance discriminator |

- Unique `(user_id, natural_key)`. Index
  **`ix_transactions_user_posted_at (user_id, posted_at)`** — the one composite
  ordered `(user_id, col)` rather than `(col, user_id)`, because `posted_at`
  is a range predicate, not equality (§4.5).
- CHECK `ck_transactions_origin`.
- Referenced by `postings`, `posting_merges.kept_transaction_id`,
  `posting_merge_duplicates`, `transfer_linked_transactions`,
  `categorization_rule_exclusions`.
- `posted_at` and `description` are facts about the *event*, not about a leg.
  They used to be columns on `postings`, written identically to every leg by
  every path that produced one — which made "two legs of one purchase dated
  two days apart" a state the schema could hold and no reader could mean.

#### `accounting.postings` — one leg of one event. Immutable and raw.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid NOT NULL` | |
| `natural_key` | `varchar NOT NULL` | |
| `transaction_id` | `uuid NOT NULL` | FK → `transactions(id) ON DELETE CASCADE` |
| `account_id` | `uuid NOT NULL` | FK → `accounts(id)` (restrict) |
| `amount` | `numeric(18,4) NOT NULL` | **Signed by design**; deliberately no positivity CHECK |
| `currency` | `varchar NOT NULL` | FK → `currencies(code)` |
| `category_id` | `uuid NULL` | The category *the file itself* named — written once at import, never rewritten |
| `subcategory_id` | `uuid NULL` | |
| `budget_id` | `uuid NULL` | FK → `budgets(id)`. Real FK, nothing in the live app sets it non-null today |
| `meta` | `jsonb NOT NULL` | |

- Unique `(user_id, natural_key)`.
- CHECK `ck_postings_subcategory_needs_category`.
- FKs: `user_id`, `transaction_id`, `account_id`, `currency`, `category_id`,
  `budget_id`, and the composite
  `(subcategory_id, category_id) → categories(id, parent_category_id)`.
- Indexes: `ix_postings_transaction_id_user_id`,
  `ix_postings_account_id_user_id`, `ix_postings_category_id_user_id`,
  `ix_postings_subcategory_id_user_id`, `ix_postings_budget_id_user_id`.
- **Trigger** `postings_balance_at_commit` — `CONSTRAINT TRIGGER AFTER INSERT
  OR UPDATE OR DELETE ... DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE
  FUNCTION accounting.assert_transaction_balances()`. See §4.7.
- Referenced by `posting_overrides`, `posting_splits`, `posting_tags`,
  `suggestions.posting_id`, `goal_contributions.source_posting_id`.

#### `accounting.posting_tags` — one (posting, tag) pairing.

PK `(user_id, posting_id, tag_id)` — a pure association table, no surrogate
`id`. Both FKs `ON DELETE CASCADE`. Indexes
`ix_posting_tags_posting_id_user_id`, `ix_posting_tags_tag_id_user_id`.

### 3.6 Interpretation

Everything here layers on top of the immutable ledger. Five of the tables carry
a `stage` column, CHECK-pinned to the single stage they are applied at, so the
resolver reads its running order out of the data (§4.9).

#### `accounting.categorization_rules` — one description matcher, two effects.

| Column | Type | Notes |
|---|---|---|
| `id` `user_id` `natural_key` | | `natural_key` prefixed `rule:…` / `pattern:…` per effect, so one unique constraint covers both |
| `effect` | `varchar NOT NULL` | `transfer` \| `categorize` |
| `stage` | `varchar NOT NULL` | `counterparty` \| `override`, derived from `effect` and pinned to it |
| `description_contains` | `varchar NOT NULL` | |
| `account_id` | `uuid NULL` | FK → `accounts(id)`; `transfer` only. Unset = "any account" |
| `counterparty_account_id` | `uuid NULL` | FK → `accounts(id)`; `transfer` only |
| `category_id` | `uuid NULL` | FK → `categories(id)`; `categorize` only, required there |
| `subcategory_id` | `uuid NULL` | |
| `priority` | `integer NOT NULL` | |
| `description` | `varchar NOT NULL` | |
| `active` | `boolean NOT NULL` | |
| `version` | `integer NOT NULL` | Per-row optimistic version (§4.3) |

- CHECKs: `effect`, `stage`, `subcategory_needs_category`, and
  `ck_categorization_rules_effect_columns` — written as **one** disjunction so
  there is no column arrangement that satisfies each individual rule while
  being a row neither effect could produce:
  `(effect='transfer' AND stage='counterparty' AND category_id IS NULL AND subcategory_id IS NULL) OR (effect='categorize' AND stage='override' AND category_id IS NOT NULL AND account_id IS NULL AND counterparty_account_id IS NULL)`.
- Indexes on all four nullable reference columns.
- Two pydantic models (`TransferRule`, `CategoryPattern`) map onto this one
  table: the storage shape is shared, the two API resources are not.

#### `accounting.categorization_rule_exclusions` — one transaction opted out of one rule.

PK `(user_id, rule_id, transaction_id)`; both FKs `ON DELETE CASCADE`. Indexes
on `(rule_id, user_id)` and `(transaction_id, user_id)`. Only ever written for
a `transfer`-effect rule — an application-level fact, not a CHECK, since the
effect lives on the other side of `rule_id`.

#### `accounting.posting_overrides` — the user's direct edit to one posting.

| Column | Type | Notes |
|---|---|---|
| `id` `user_id` | | |
| `stage` | `varchar NOT NULL` | CHECK `= 'override'` |
| `posting_id` | `uuid NOT NULL` | FK → `postings(id) ON DELETE CASCADE` |
| `account_id` | `uuid NULL` | FK → `accounts(id)` |
| `category_id` / `subcategory_id` | `uuid NULL` | Paired FK into `categories` |
| `tags_overridden` | `boolean NOT NULL` | The bit a join-table row count cannot carry: `True` with zero `posting_override_tags` rows means "overridden to no tags"; `False` means "don't consult that table at all" |

- Unique `uq_posting_overrides_user_posting (user_id, posting_id)`. No
  `natural_key` — the posting is the key.
- CHECKs: `stage`, `subcategory_needs_category`.
- Indexes on `posting_id`, `account_id`, `category_id`, `subcategory_id`.

#### `accounting.posting_override_tags` — one tag in an override's tag set.

PK `(user_id, override_id, tag_id)`; both FKs `ON DELETE CASCADE`. Indexes on
`(override_id, user_id)`, `(tag_id, user_id)`. A join table rather than a UUID
array, because Postgres cannot enforce "every element of an array references a
real row".

#### `accounting.posting_splits` — the decision to break one posting into legs.

`id`, `user_id`, `stage` (CHECK `= 'split'`), `posting_id` (FK
`ON DELETE CASCADE`). Unique `(user_id, posting_id)`. Index on
`(posting_id, user_id)`.

#### `accounting.posting_split_legs` — one piece of a split.

| Column | Type |
|---|---|
| `id`, `user_id` | |
| `posting_split_id` | `uuid NOT NULL` → `posting_splits(id) ON DELETE CASCADE` |
| `ordinal` | `integer NOT NULL` |
| `amount` | `numeric(18,4) NOT NULL` |
| `category_id` / `subcategory_id` | `uuid NULL`, paired FK |
| `description` | `varchar NOT NULL` |

- Unique **`uq_posting_split_legs_user_split_ordinal (user_id, posting_split_id, ordinal)`** —
  this table previously shipped with no unique constraint at all, so two legs
  could both claim ordinal 1 and the returned order was arbitrary.
- CHECK `subcategory_needs_category`. Indexes on `posting_split_id`,
  `category_id`, `subcategory_id`.

#### `accounting.posting_merges` — two or more transactions are the same real event.

`id`, `user_id`, `natural_key`, `stage` (CHECK `= 'merge'`),
`kept_transaction_id` (FK → `transactions(id) ON DELETE CASCADE`),
`description varchar NULL`. Unique `(user_id, natural_key)`. Index on
`(kept_transaction_id, user_id)`. `CASCADE` is deliberate: this row references
its kept transaction directly, so pruning that transaction in a rebuild
correctly deletes the whole merge decision.

#### `accounting.posting_merge_duplicates` — one transaction dropped by a merge.

PK `(user_id, merge_id, duplicate_transaction_id)`. Both FKs `ON DELETE
CASCADE`; here it drops only the one membership row, never the merge or its
siblings. Indexes on `(merge_id, user_id)` and
`(duplicate_transaction_id, user_id)`. The FK name is truncated by Postgres to
`fk_posting_merge_duplicates_duplicate_transaction_id_tr_c306`.

#### `accounting.transfer_links` — two transactions confirmed as one transfer.

| Column | Type | Notes |
|---|---|---|
| `id` `user_id` `natural_key` | | `natural_key` derived from the two transaction ids sorted, so re-confirming from either side is idempotent |
| `stage` | `varchar NOT NULL` | CHECK `= 'link'`; last of the five stages |
| `source` | `varchar NOT NULL` | CHECK `manual` \| `rule` |
| `rule_id` | `uuid NULL` | FK → `categorization_rules(id) ON DELETE SET NULL` |

- Index on `(rule_id, user_id)`. `rule_id` was a bare `String` holding what was
  supposed to be a rule's natural key, with nothing stopping it naming a rule
  that never existed. `SET NULL` keeps the useful half of the old intent:
  deleting the rule leaves the link standing (`source` still records that a
  rule proposed it) and clears only the reference that no longer resolves.

#### `accounting.transfer_linked_transactions` — one transaction's membership in a link.

PK `(user_id, link_id, transaction_id)`, **plus** unique
`uq_transfer_linked_transactions_user_transaction (user_id, transaction_id)` —
a strictly stronger, different rule the PK cannot express: a transaction
appearing in a *second* link must fail at the database. `link_id` cascades;
`transaction_id` deliberately **restricts**, because deleting one side of a
pair would leave a one-legged link. Indexes on `(link_id, user_id)` and
`(transaction_id, user_id)`.

#### `accounting.suggestions` — a proposal awaiting an answer.

| Column | Type | Notes |
|---|---|---|
| `id` `user_id` `natural_key` | | |
| `status` | `varchar NOT NULL` | `pending` \| `dismissed` |
| `kind` | `varchar NOT NULL` | `category` \| `transfer` \| `duplicate` |
| `source` | `varchar NOT NULL` | `ai` \| `pattern` \| `detector` |
| `description` | `varchar NOT NULL` | |
| `posting_id` | `uuid NULL` | FK → `postings(id) ON DELETE CASCADE`; pending only |
| `selected` | `boolean NOT NULL` | |
| `previous_category_id` / `previous_subcategory_id` | `uuid NULL` | Paired FK; pending only |
| `dismissed_at` | `timestamptz NULL` | Required when dismissed |

- Six CHECKs. Three are simple vocabulary checks (`status`, `kind`, `source`),
  one is `subcategory_needs_category`, and two are cross-column shape
  constraints that keep each lifecycle's columns out of the other's:
  - `ck_suggestions_pending_shape` — `status <> 'pending' OR (kind='category' AND source IN ('ai','pattern') AND posting_id IS NOT NULL AND dismissed_at IS NULL)`
  - `ck_suggestions_dismissed_shape` — `status <> 'dismissed' OR (kind IN ('transfer','duplicate') AND source='detector' AND posting_id IS NULL AND previous_category_id IS NULL AND previous_subcategory_id IS NULL AND dismissed_at IS NOT NULL)`
- Indexes on `posting_id`, `previous_category_id`, `previous_subcategory_id`.
- One `UNIQUE (user_id, natural_key)` covers both lifecycles. A pending row's
  key is `pending:<posting natural key>`, which subsumes the old
  `UNIQUE (user_id, posting_id)`; a dismissed row's is derived from the
  suggestion's own content, so the same real-world pair or group dismisses and
  restores as the same row however many times the detector recomputes it.
- **No `stage` column**, unlike the other overlay tables: a proposal has no
  precedence of its own. See §4.9.

### 3.7 Planning

#### `accounting.budgets` — one spending target, monthly or general.

| Column | Type | Notes |
|---|---|---|
| `id` `user_id` `natural_key` | | |
| `month` | `varchar NULL` | `"YYYY-MM"` for one month; `NULL` is the general, every-month-alike target |
| `category_id` | `uuid NOT NULL` | FK → `categories(id)` |
| `subcategory_id` | `uuid NULL` | Paired FK |
| `amount` | `numeric(18,4) NOT NULL` | |
| `currency` | `varchar NOT NULL` | FK → `currencies(code)` |

- Unique constraint `(user_id, natural_key)`, plus the partial-expression
  unique index
  `uq_budgets_user_month_category (user_id, coalesce(month,''), category_id, coalesce(subcategory_id,'000…0'::uuid))`.
  The coalesces exist because Postgres treats every `NULL` as distinct, so a
  plain `UNIQUE` over nullable columns would silently allow two "whole
  category, no subcategory" budgets for the same month.
- CHECKs: `amount >= 0`;
  `month IS NULL OR month ~ '^\d{4}-(0[1-9]|1[0-2])$'` — without the 01–12
  bound, `"2024-13"` stored happily and then sorted and grouped as if it were
  a month that exists; `subcategory_needs_category`.
- Indexes on `category_id`, `subcategory_id`. Referenced by
  `postings.budget_id`.

#### `accounting.goals` — a savings target.

| Column | Type | Notes |
|---|---|---|
| `id` `user_id` `natural_key` | | |
| `name` | `varchar NOT NULL` | |
| `target_amount` | `numeric(18,4) NOT NULL` | CHECK `> 0` — a goal of zero is already met and has nothing to progress towards |
| `target_currency` | `varchar NOT NULL` | FK → `currencies(code)` |
| `target_date` | `timestamp NOT NULL` | |
| `color` | `varchar NOT NULL` | |
| `version` | `integer NOT NULL` | Per-row optimistic version |

A goal's balance is never stored — it derives entirely from its
`goal_contributions`. No non-unique indexes.

#### `accounting.goal_contributions` — one dated, signed allocation into or out of a goal.

| Column | Type | Notes |
|---|---|---|
| `id` `user_id` `natural_key` | | |
| `goal_id` | `uuid NOT NULL` | FK → `goals(id)` (restrict) |
| `date` | `timestamp NOT NULL` | |
| `amount` | `numeric(18,4) NOT NULL` | |
| `currency` | `varchar NOT NULL` | FK → `currencies(code)` |
| `note` | `varchar NOT NULL` | |
| `account_id` | `uuid NULL` | FK → `accounts(id)`. Which account the earmarked money actually sits in — **recorded but not yet used in any arithmetic** |
| `source_posting_id` | `uuid NULL` | FK → `postings(id) ON DELETE SET NULL` |
| `origin` | `varchar NOT NULL` | CHECK `manual` \| `automation` |
| `edited` | `boolean NOT NULL` | |

- `SET NULL`, not `CASCADE`, on `source_posting_id`: `amount`/`date` are the
  real financial record, so a posting pruned by a ledger rebuild must take
  only the traceability link, never the contribution.
- Indexes on `goal_id`, `account_id`, `source_posting_id`.

#### `accounting.goal_automations` — one ordered rule for moving money into or out of a goal.

| Column | Type | Notes |
|---|---|---|
| `id` `user_id` `natural_key` | | |
| `goal_id` | `uuid NOT NULL` | FK → `goals(id)` |
| `direction` | `varchar NOT NULL` | `contribution` \| `withdrawal` |
| `priority` | `integer NOT NULL` | |
| `start_date` | `date NULL` | contribution only |
| `frequency` | `varchar NULL` | `daily`\|`weekly`\|`biweekly`\|`monthly`; contribution only |
| `end_date` | `date NULL` | optional even for a contribution — an open-ended schedule |
| `mode` | `varchar NULL` | `fixed_amount`\|`percent_of_unallocated`\|`remainder`; contribution only |
| `value` | `numeric(18,4) NULL` | contribution only |
| `currency` | `varchar NULL` | FK → `currencies(code)`; contribution only |

- Four CHECKs: `direction`, `frequency`, `mode`, and
  `ck_goal_automations_schedule_matches_direction` —
  `(direction='contribution' AND start_date, frequency, mode, value, currency all NOT NULL) OR (direction='withdrawal' AND those five plus end_date all NULL)`.
  Merging two tables behind a discriminator is only safe if the discriminator
  actually decides which columns are populated.
- Two partial unique indexes:
  - `uq_goal_automations_user_withdrawal_goal (user_id, goal_id) WHERE direction = 'withdrawal'` — a goal appears at most once in the drawdown order, while it may legitimately have several contribution schedules.
  - `uq_goal_automations_user_remainder (user_id) WHERE mode = 'remainder'` — the "one remainder" rule (§4.7).
- Index on `(goal_id, user_id)`.

### 3.8 The trades ledger

#### `trades.broker_connections` — one user's link to a brokerage.

`id`, `user_id`, `natural_key`, `broker varchar NOT NULL`. Unique
`(user_id, natural_key)`. No non-unique indexes. Credentials themselves live in
`public.user_secrets` keyed `(user_id, "broker:{broker}")`, so this table never
holds a pointer to them.

#### `trades.ledger_events` — one immutable row of one user's brokerage ledger.

| Column | Type | Notes |
|---|---|---|
| `id` `user_id` `natural_key` | | |
| `connection_id` | `uuid NOT NULL` | FK → `broker_connections(id) ON DELETE CASCADE` |
| `event_datetime` | `timestamp NOT NULL` | |
| `symbol` | `varchar NOT NULL` | FK → `securities(symbol)` |
| `event_type` | `varchar NOT NULL` | CHECK `DEPOSIT`\|`WITHDRAWAL`\|`BUY`\|`SELL`\|`DIVIDEND`\|`WITHHOLDING`\|`FEE`\|`SPLIT` |
| `amount` | `numeric(18,4) NOT NULL` | **Unsigned magnitude**; direction is in `event_type` (§4.8) |
| `currency` | `varchar NOT NULL` | FK → `currencies(code)` — this was the one currency column in the schema with no constraint of any kind |
| `meta` | `jsonb NOT NULL` | |

Index `ix_ledger_events_connection_id_user_id`.

#### `trades.ledger_event_trade_details` — shares and price, for `BUY`/`SELL` only.

| Column | Type |
|---|---|
| `ledger_event_id` | `uuid` PK → `ledger_events(id) ON DELETE CASCADE` |
| `user_id` | `uuid NOT NULL` |
| `shares` | `numeric(20,8) NOT NULL` |
| `price` | `numeric(18,4) NOT NULL` |

A side table rather than two nullable columns on `ledger_events`: a row exists
only for a trade, and if it exists both fields are guaranteed present, enforced
by the engine rather than only by a pydantic validator. `user_id` is
denormalized from the parent even though `ledger_event_id` determines it,
because an RLS policy needs the column on *this* table or a direct query
against it would never be filtered. Indexes
`ix_ledger_event_trade_details_ledger_event_id_user_id` and
`ix_ledger_event_trade_details_user_id`.

### 3.9 Settings and usage

#### `trades.dashboard_settings` — one user's dashboard preferences.

| Column | Type | Notes |
|---|---|---|
| `user_id` | `uuid` PK | Singleton per user; no `natural_key`, no `version` |
| `target_allocation_pct` | `jsonb NOT NULL` | `RateMap` — values stored as exact decimal **strings**, because JSON has no decimal type and psycopg refuses to serialize a `Decimal` |
| `hysa_bank_id` | `varchar NULL` | Deliberately not a foreign key (see §3.2) |
| `hysa_fixed_rate_pct` | `numeric(12,6) NULL` | |
| `benchmark_symbol_override` | `varchar NULL` | FK → `securities(symbol)` |
| `local_zone` | `varchar NULL` | |
| `tax_enabled` | `boolean NOT NULL` | |
| `tax_regime` | `varchar NULL` | CHECK `NRA` \| `RESIDENT` |
| `residency_status_change_date` | `date NULL` | |
| `w8ben_claimed` | `boolean NOT NULL` | |
| `w8ben_treaty_rate_pct`, `marginal_ordinary_rate_pct`, `qualified_ltcg_rate_pct` | `numeric(12,6) NULL` | |

No `version` column, deliberately: one row per user, edited by the one person
who owns it, every field an idempotent preference — last-write-wins is the
wanted behaviour, not a conflict.

Patched through one statement rather than read-modify-written, so two edits
naming different symbols compose — see
`trades.dashboard.settings.merge_target_allocation`, and known gap 4 for what
that replaced.

#### `trades.sync_runs` — one run of a broker sync, as a row rather than a request in flight.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | `uuid7()`, so the primary key is already the newest-first order |
| `user_id` | `uuid NOT NULL` | FK → `users(id)` `ON DELETE CASCADE` |
| `state` | `varchar NOT NULL` | CHECK `queued` \| `running` \| `succeeded` \| `failed` |
| `step`, `percent` | `varchar`, `double precision` | Progress, written by the runner on its own transaction |
| `error` | `varchar NULL` | Why the *runner* failed — never why a step did |
| `started_at`, `finished_at` | `timestamp NULL` | |
| `synced_at` | `varchar NULL` | Already rendered in the user's display zone, as the wire carries it |
| `new_event_count`, `total_event_count` | `integer NOT NULL` | |
| `steps` | `jsonb NOT NULL` | Each leg's `label`/`ok`/`error`; opaque, never queried across runs |

Indexes: `ix_sync_runs_user_id`, and `uq_sync_runs_active_user` — **unique
on `user_id`, partial on `state IN ('queued','running')`**. That partial index
is the concurrency control: "one sync in flight per user" is a fact about the
database, not about one Python process, so a second `POST /sync-runs` is
refused by Postgres however many workers exist. The plain index is not
redundant beside it — a partial index cannot serve the unqualified `user_id`
lookup the isolation policy and the cascade from `users` both make, which is
why `db.indexes` no longer counts one as covering a foreign key.

`state` is `succeeded` even when the broker leg failed: a sync commits per
successful step, so a partly-successful pull is a completed run that records
what did not work in `steps`. `failed` means the runner itself did not finish
— including a run a container restart abandoned, which the API's own startup
closes so the partial index does not wedge that user's next sync.

#### `accounting.llm_usage` — one (user, provider) call counter.

PK `(user_id, provider)`. `period_start timestamp NOT NULL`,
`used_count integer NOT NULL`, `is_limited boolean NOT NULL`,
`last_error varchar NULL`. No other indexes.

### 3.10 The resolved projection

Two tables that hold no facts of their own. Everything in them is derived
from §3.5 and §3.6 by `accounting.ledger.resolution`, and stored so SQL can
filter, sort and count the values a user actually sees — see §4.9, which is
where the immutable-posting rule this exists alongside is set out.

#### `accounting.resolved_postings` — one posting as the overlay pipeline resolves it.

PK `(user_id, posting_id)`, where `posting_id` is the natural key string
rather than a foreign key into `postings`: a split leg's id
(`f"{posting_id}:split:{n}"`) belongs to no `postings` row. Columns are
deliberately `api.api_models.PostingRow`'s own, plus
`is_real_income_expense`, `is_excluded_from_rule`, and `transaction_row_id`
— the real `transactions.id`, carried because a recompute has to be able to
delete the rows of a transaction that no longer exists.

`currency` foreign-keys `public.currencies` like the other nine currency
columns. Nothing else here is a foreign key; the values are natural keys,
not references.

Indexes: `(user_id, posted_at, transaction_id)` for the page order,
`(user_id, transaction_row_id)` for a recompute, `(user_id, category_id)` and
`(user_id, account_id)` for the filters.

#### `accounting.resolved_postings_dirty` — one transaction whose projection rows are out of date.

PK `(user_id, transaction_id)`. `transaction_id` is a `transactions.id` and
deliberately **not** a foreign key: the row has to survive the deletion of
the transaction it names, because "this transaction is gone" is exactly a
change the projection has to be told about.

Filled by 68 statement-level triggers — four per resolution input table,
because Postgres refuses a transition table on a multi-event trigger —
generated from `accounting.precedence.OVERLAY_SOURCES` by
`accounting.db.projection`. Drained by `repositories.projection.drain` at
the top of every read that trusts the projection.

---

## 4. Cross-cutting design decisions

### 4.1 Money and numeric types

**At the database and in the domain layer, money is exact.** Three SQL types,
declared once in `src/db/base.py`:

| Constant | SQL type | For |
|---|---|---|
| `MONEY` | `NUMERIC(18, 4)` | every monetary amount |
| `SHARES` | `NUMERIC(20, 8)` | fractional share counts |
| `RATE` | `NUMERIC(12, 6)` | every rate or percentage |

All three are `asdecimal=True`, so psycopg hands back a `decimal.Decimal` and
the exactness Postgres already maintains survives the driver boundary instead
of being discarded there. Every column is annotated `Mapped[Decimal]`.

Be precise about what this rewrite actually changed here, because it is easy to
overstate. `MONEY` and `SHARES` were **already** `NUMERIC(18,4)` and
`NUMERIC(20,8)` on `origin/main` — Postgres was always storing and computing
exactly. They were declared `asdecimal=False`, deliberately, so psycopg would
hand back a Python `float` matching every pydantic model's own `amount: float`.
Exactness was therefore thrown away at the driver boundary, on every read, and
every piece of Python arithmetic downstream was float. Three things changed:
`asdecimal=False` → `True`; every domain model's `float` → `Decimal` (`Money`,
`Rate`, `Shares`); and `RATE` is new, replacing six columns that were untyped
`Mapped[float]` and therefore real `double precision` in Postgres
(`simulator_scenarios.horizon_years`, `.annual_rate_pct`,
`dashboard_settings.hysa_fixed_rate_pct`, `.w8ben_treaty_rate_pct`,
`.marginal_ordinary_rate_pct`, `.qualified_ltcg_rate_pct`).

`RATE` is `NUMERIC`, never `double precision`, because a rate is multiplied
*into* a money amount — a float rate reintroduces exactly the drift `MONEY`
exists to prevent. It is wider than money (6 places vs 4) for the same reason:
rounding a rate at the money scale first pushes the error into the product.

`src/db/money.py` is the single policy module:

- `MONEY_SCALE = 4`, `RATE_SCALE = 6`, `SHARES_SCALE = 8`, each with a
  `*_QUANTUM` `Decimal`. Four places for money, not two, so a per-unit price,
  a split ratio or a partial-cent accrual survives a round trip rather than
  being truncated on the way in.
- `ROUNDING = ROUND_HALF_UP` — what a person means by "round to the nearest
  cent", and what finance practice expects. Python's default is
  `ROUND_HALF_EVEN`, better for repeated statistical aggregation and worse for
  a ledger a human reads, so it is passed explicitly at every call rather than
  inherited.
- `quantize_money` / `quantize_rate` / `quantize_shares` are applied at write
  boundaries, so what is persisted is what later arithmetic sees. Letting
  Postgres truncate implicitly would leave the in-memory value and the stored
  value silently disagreeing until the next read.
- `to_decimal` routes a `float` through `str`, because `Decimal(0.1)` faithfully
  reproduces the float's binary error while `Decimal("0.1")` gives the literal
  the author wrote.
- `round_to_currency(value, decimal_places)` is distinct from
  `quantize_money`: the latter is about what the database stores (4 places), the
  former about what the currency is denominated in (`currencies.decimal_places`,
  2 for USD).

**Where exactness stops, and why.** `src/accounting/ledger/frame.py` is the one
boundary. Every aggregation over the ledger — balances, net worth, the income
statement, budgets, goals, and every chart behind them — is computed with Polars
over `LEDGER_FRAME_SCHEMA`, whose `amount` column is `Float64`. Polars' own
`Decimal` dtype is still marked unstable and its aggregation semantics differ
from the Python `decimal` module's, so adopting it would trade a known, bounded
imprecision for an unknown one. `db.money.to_analytics_float` (re-exported there
as `to_analytics_amount`) is the **only** sanctioned `Decimal -> float`
conversion in the codebase, and exists as a named function precisely so every
such crossing is greppable.

So, plainly:

| Layer | Exact? |
|---|---|
| Postgres columns | Yes — `NUMERIC` |
| psycopg / ORM row values | Yes — `Decimal` |
| pydantic domain models (`Money`, `Rate`, `Shares`) | Yes — `Decimal` |
| Polars analytics frames and everything computed from them | **No** — `Float64` |
| JSON on the wire | **No** — JSON numbers (see below) |

The residual imprecision is confined to aggregation, bounded by
`NUMERIC(18,4)` inputs, and re-quantized before anything is stored. The
`_AMOUNT_TOLERANCE`/`_ZERO_SUM_TOLERANCE` constants still present in
`ledger.transfers`, `ledger.duplicates` and `ledger.replay` are float slack for
*this projection specifically*; they are no longer masking imprecision anywhere
money is stored.

**The wire format is still JSON numbers.** `Money`, `Rate` and `Shares` are
`Annotated[Decimal, PlainSerializer(float, when_used="json"), WithJsonSchema({"type": "number"})]`.
Pydantic v2 serializes a bare `Decimal` as a JSON *string*, so typing these
fields `Decimal` without the annotations would have silently changed every
money field in the public API from `12.34` to `"12.34"`, broken the generated TS
client, and done it as an accident of an internal refactor rather than as a
decision. The annotations pin the wire exactly where it was: number in, number
out, unchanged OpenAPI. Flipping this to exact decimal strings end to end —
dropping the two annotations, regenerating the OpenAPI schema and the TS
client — remains possible and is not planned; the `TODO(PR3)` marker that used
to sit in `src/db/money.py` is gone. A
value is exact everywhere in Python and lossy only in the final JSON encode,
which is strictly better than before (lossy from the moment it left Postgres)
and changes no contract.

`RateMap` (`src/db/base.py`) is the one place a JSONB column holds rates:
`target_allocation_pct`. The values are stored as decimal *strings*, which is
the exact representation, not a workaround — `"33.333333"` round-trips to the
same `Decimal` where a JSON number would land on the nearest double. It is only
for a genuine open-ended map (symbols the user picks); a fixed, known set of
rates should be real `RATE` columns.

### 4.2 Row-level security

RLS is the isolation guarantee, not a defence in depth: `app_runtime` can
`SELECT` every table, so one forgotten `.filter_by(user_id=...)` returns other
tenants' rows unless the database itself refuses.

**Two roles.** Migrations run as the superuser from `DATABASE_URL`, which owns
every table and would bypass every policy. The application connects as
`app_runtime` from `DATABASE_URL_APP` — an ordinary, non-superuser, non-owning
role with nothing but `SELECT/INSERT/UPDATE/DELETE` on the three schemas,
created and password-synced by `src/migration/app_role.py`. `DATABASE_URL_APP`
has **no fallback** to `DATABASE_URL`: the app refuses to start rather than
silently connecting as the superuser, which would make RLS exist and do
nothing. `grant_app_runtime` refuses to run in Alembic's offline `--sql` mode,
because rendering `CREATE ROLE ... PASSWORD` to a script would write the
plaintext secret into exactly the artifact people paste into tickets, and it
sends the two role statements through `exec_driver_sql` so the password does not
pass through Alembic's migration logger.

**The tenant set is computed, not configured.** `src/db/tenant.py`:

```
tenant_tables(metadata) -> every table with a `user_id` column
                           + public.users (keyed on its own `id`)
                           - RLS_EXEMPT
```

The baseline migration emits policies from this function, and
`tests/db/test_rls_coverage.py` asserts the live database matches. Adding a
tenant table therefore adds it to the set with no step anyone can forget — "has
`user_id`" *structurally implies* "has a FORCED policy".

The old arrangement could not promise that. **Six** migrations each declared
their own local copy of a `_USER_SCOPED_TABLES` list of
`(schema, table, ownership_column)` triples — the first with 28 entries, each
later table-creating migration with just its own new tables. Two migrations
(`274f8b1a5322`, which created `transfer_rule_exclusions`, and `c0a01fde67fc`,
which created both transfer-link tables) simply did not copy the block, so
**three tables holding real per-user financial linkage shipped with no
row-level isolation at all**: `transfer_links`,
`transfer_linked_transactions`, and what is now
`categorization_rule_exclusions`. Nothing failed; they just quietly had no
policy. `tests/db/test_rls_coverage.py::test_the_previously_uncovered_transfer_tables_are_covered_now`
names them explicitly so the specific regression cannot recur.

What did *not* change is the policy text. Old policies already carried both
`USING` and `WITH CHECK` with identical expressions, and the last old migration
(`fd6052b7ba82`) had already wrapped both sides in `NULLIF(..., '')` — and,
notably, did so by introspecting `pg_policies` rather than from a hardcoded
list. The change here is that **coverage** is derived and asserted rather than
transcribed, and that the coverage test now checks the `WITH CHECK` expression
as well as `USING`.

**The policy.** Per table:

```sql
ALTER TABLE s.t ENABLE ROW LEVEL SECURITY;
ALTER TABLE s.t FORCE ROW LEVEL SECURITY;
CREATE POLICY user_isolation ON s.t
  USING      (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
  WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);
```

- `FORCE` matters as much as `ENABLE`: migrations run as the table owner, and a
  plain `ENABLE` does not apply to the owner, so without `FORCE` the policy
  would be inert for exactly the role that created it.
- Both **read (`USING`)** and **write (`WITH CHECK`)** are constrained. A policy
  that reads correctly but writes permissively is the same leak in the other
  direction, and `test_every_policy_fails_closed_on_an_unset_current_user`
  asserts both expressions (falling back to `USING` when `with_check` is `NULL`,
  matching what Postgres actually enforces).
- `NULLIF(..., '')` is there because `app.current_user_id` is an undeclared
  placeholder GUC: Postgres resets it to the empty string, not `NULL`, so a bare
  cast would raise. Failing closed (zero rows) beats a 500 and beats returning
  everything.
- The GUC is set per transaction by `set_config('app.current_user_id', :id, true)`
  in `src/db/session.py`, from `Depends(get_current_user_id)`.

**Counts, verified in the scratch database:** 34 policies on 34 tables. 38 app
tables − 3 dimension tables − 1 exemption = 34. All 34 have both
`relrowsecurity` and `relforcerowsecurity`.

**The one exemption** is `public.external_identities`, with its reason recorded
in `db.tenant.RLS_EXEMPT` (the coverage test requires a non-empty reason, so
adding an entry has to be a sentence someone wrote). It is looked up *before*
the acting user is known — sign-in resolves a provider identity to a `user_id`,
so a policy keyed on that `user_id` could never match and would lock every user
out. It is recorded as a real hole, not a solved problem. The compensating
controls: the table holds no financial data (only `provider`, `external_id`,
`user_id`); `db/external_identities.py` is the only code that touches it and
offers no listing path, just exact-match lookup on a `(provider, external_id)`
pair the caller must already hold from a verified Clerk session or a
signature-checked webhook; and all it ever returns is an opaque internal id.

The three dimension tables have **no policy and no exemption** — they have no
`user_id` to write a policy against, which is categorically different from a
tenant table that deliberately has none.
`test_no_table_without_a_policy_is_anything_other_than_reference_data` holds
that line.

### 4.3 Concurrency

**One mechanism: per-row optimistic versioning.** `check_and_bump_row_version`
in `src/db/base.py` verifies and bumps in a single atomic statement —

```sql
UPDATE <table> SET version = version + 1
WHERE id = :row_id AND user_id = :user_id
  AND (CAST(:expected_version AS INTEGER) IS NULL OR version = CAST(:expected_version AS INTEGER))
RETURNING version
```

— because a read-then-write would leave a window where two concurrent saves
both read the same "current" version and both proceed. It returns the new
version, `None` if no such row (404), or raises `VersionConflictError` (mapped
to HTTP 409 by one global handler in `trades.api.api`) if the row exists at a
different version. `expected_version = None` deliberately opts a write out of
the check, for idempotent last-write-wins fields like a boolean toggle.

Only **two tables** carry a `version` column: `accounting.goals` and
`accounting.categorization_rules`. Both are excluded from their own tables'
upsert paths, so an unrelated create or reorder never invalidates a version a
client already holds.

**What was deleted:**

| Deleted | What it was |
|---|---|
| `accounting.store_versions` (table) | One counter per user covering every accounting table at once |
| `trades.dashboard_settings_versions` (table) | The same thing for the settings row |
| `src/accounting/db/concurrency.py` (module) | The `StoreVersion` model |
| `db.base.get_version`, `db.base.check_and_bump_version` | The whole-store counter API |
| `X-Expected-Store-Version` (HTTP header) | The side-channel that carried the store counter |
| `X-Expected-Dashboard-Settings-Version` (HTTP header) | Ditto for settings |

The headers were stashed on the request's SQLAlchemy session by a router-level
dependency and read back out deep in the persistence layer. They were removed
because a single counter over ~25 tables meant editing a budget conflicted with
renaming an account, and because the client-side cache of "the last version I
saw" was global — so the frontend had to serialize batches of genuinely
independent writes (accepting several duplicate-merge suggestions, adding two
transfer rules) into sequential loops purely to stop them racing their own
shared header. That sequencing is now plain parallel requests. Two mechanisms
also meant two ways to be wrong; there is now one. Full reasoning in
`docs/optimistic-concurrency-versioning.md`.

### 4.4 Primary keys

Every aggregate-root table's primary key is a surrogate `uuid`, minted by the
database:

```sql
id uuid NOT NULL DEFAULT public.uuid7() PRIMARY KEY
```

`public.uuid7()` is defined in `src/db/base.py` (`UUID7_STATEMENTS`) and
installed by the baseline migration *and* by a `before_create` hook on
`Base.metadata` for the test suite — before the first `CREATE TABLE`, because
Postgres resolves a `DEFAULT` expression when the column is created. It is
schema-qualified so no table's default can be re-pointed by a `search_path`
difference between the migration role and `app_runtime`. Postgres only grew a
built-in `uuidv7()` in 18 and this project runs 16, so it is written out
byte-by-byte per RFC 9562 §5.7: bytes 0–5 are the 48-bit big-endian
millisecond timestamp from `clock_timestamp()` (not `now()`, which is fixed at
transaction start and would give every row of a bulk import one timestamp),
byte 6's high nibble is version `0111`, byte 8's top two bits are variant `10`,
and everything else is random from `gen_random_uuid()`.

**Why this replaced content-derived hashes.** There were previously *three*
id-generation regimes side by side: `db.base.derive_id(user_id, table, natural_key)`
= `uuid.uuid5(_ID_NAMESPACE, f"{user_id}:{table}:{natural_key}")` — a SHA-1
namespace UUID — for every table with a `natural_key`; a client-side
`default=uuid.uuid4` on the twelve join and child tables that had none; and
natural or composite primary keys elsewhere. (`general_budgets` managed to be
in two of them at once: the model declared `default=uuid.uuid4` while its own
docstring recorded that the default was never used, because `store` called
`derive_id` on its `(category_id, subcategory_id)` pair instead.) There is now
one default for every surrogate key, and it is the database's.

Two reasons the hash went:

1. **Insert locality.** A hash scatters inserts uniformly through the B-tree,
   dirtying a different page per row. A time-ordered prefix makes a fresh id
   sort after every id minted before it, so inserts append at the right-hand
   edge.
2. **Hashes drift when their inputs change.** A derived id was only stable while
   its natural key was; renaming or re-keying a row changed its identity
   silently, and the property the derivation was bought for — surviving a
   wholesale delete-and-reinsert without a remap pass — stopped being needed
   the moment those saves became `upsert_and_prune`.

**`natural_key` remains the addressing mechanism.** Everything above the
persistence boundary — every pydantic model, every API path, every domain
function — addresses a row by its natural key *string*; every stored foreign key
holds the opaque `id`. Nothing computes one from the other, because nothing can.
`db.base.natural_keys_by_id` and `db.base.ids_by_natural_key` are the only two
places an `id` and a `natural_key` meet, batched into one query each. A natural
key that names no row raises `UnknownNaturalKeyError` on subscript (`.get()` is
reserved for the one case where absence *is* the answer, i.e. insert-or-update),
which keeps a bad reference as loud as it was when the made-up id reached
Postgres and the foreign key rejected it.

The primary-key shapes that are **not** a surrogate `id`:

| Table | Primary key | Why |
|---|---|---|
| `currencies`, `institutions`, `securities` | the code / symbol itself | Not user-chosen, cannot be renamed, and already the value every referencing column displays. A surrogate would buy stability against a change that cannot happen and charge a join to get the string back |
| `external_identities` | `(provider, external_id)` | One external account links to exactly one internal user |
| `user_secrets` | `(user_id, key)` | |
| `llm_usage` | `(user_id, provider)` | |
| `dashboard_settings` | `(user_id)` | Singleton preferences row |
| `ledger_event_trade_details` | `(ledger_event_id)` | One-to-zero-or-one extension of its parent |
| `posting_tags`, `posting_override_tags`, `posting_merge_duplicates`, `transfer_linked_transactions`, `categorization_rule_exclusions` | the full association tuple | Pure association tables. A surrogate `id` on top of a `UNIQUE` over the same columns was a second index to maintain buying nothing, since nothing references a pairing by an id of its own. `test_no_junction_table_carries_a_surrogate_id` enforces this |

### 4.5 Indexes

Postgres does not index a foreign key for you, and this schema previously had
exactly **one** non-unique index across its ~39 tables —
`ix_postings_user_posted_at` — plus two `unique=True` expression indexes on
`budgets`/`general_budgets`, for three declared `Index` objects in total. Every
other index was whatever Postgres created implicitly for a primary key or a
unique constraint, so **no foreign key column was indexed anywhere**.

It now has **48** plain non-unique indexes plus 3 standalone (partial) unique
indexes — 51 declared `Index` objects, on top of 36 primary keys and 25 unique
constraints, for 112 indexes total in the three schemas.

> Note: `src/db/indexes.py`'s own module docstring says "there were exactly
> three non-unique indexes". That is loose — one of the three was non-unique and
> two were unique. The point it makes (no foreign key had one) is correct.

The walk is computed, not written out by hand:
`db.indexes.ensure_foreign_key_indexes(Base.metadata, schema=...)`, called once
per schema from `src/db/models.py`, `src/accounting/db/__init__.py` and
`src/trades/db/__init__.py`. Adding a foreign key adds its index, with nothing
for anyone to forget — the same argument `db.tenant` makes for policies.

**Composites are ordered `(foreign_key, user_id)`, not `(user_id, foreign_key)`.**
The reasoning is written down in `src/db/indexes.py`'s module docstring. There
are two access paths, and they pull in opposite directions:

- **Cascade and referential checks.** `DELETE FROM categories WHERE id = ?`
  makes Postgres scan every referencing table for `category_id = ?`, with no
  `user_id` in sight. This path needs the foreign key to *lead*.
- **Application reads.** RLS injects
  `user_id = current_setting('app.current_user_id')` into every statement, so a
  real query predicate is always `user_id AND <whatever else>`.

Both predicates are equality, and a composite B-tree serves an
equality/equality pair equally well in either order. So `(fk, user_id)`
satisfies both paths with one index, while `(user_id, fk)` satisfies only the
second — the foreign key is not a leftmost prefix there, so the cascade still
seq-scans. Queries filtering on `user_id` alone are already served by each
table's `uq_*(user_id, natural_key)`.

The one deliberate exception is `ix_transactions_user_posted_at (user_id, posted_at)`,
which is hand-declared rather than produced by the walk: `posted_at` is a
*range* predicate, so it must be the trailing column. This index moved from
`postings` to `transactions` along with the column it indexes, and is strictly
better there — one entry per event rather than one per leg.

**Three classes of column get no index, each for a stated reason:**

1. **References into a dimension table** (`currencies`, `institutions`,
   `securities` — twelve columns). Cascade checks never happen, because
   reference rows are never deleted; reads never filter on them, because
   `currency` is *rendered*, not searched. What an index would cost is real: a
   second B-tree over every posting keyed on a column with two distinct
   values, which no planner would choose. `test_a_reference_into_a_dimension_table_mints_no_index`
   holds this.
2. **Generated columns** (`parent_depth`). Every row holds the same value, so an
   index led by it is pure write cost; the selectivity lives in the composite's
   other column, which gets its own index.
3. **Anything already covered** by a leftmost prefix of an existing index,
   unique constraint or primary key. A `ForeignKeyConstraint` emphatically does
   *not* count as coverage — treating one as such is precisely the mistake that
   leaves foreign keys unindexed.

Index names are `ix_<table>_<cols>`, truncated from the *left* if they would
exceed Postgres' 63-character identifier limit (keeping the distinguishing tail
rather than the repetitive head), so two names cannot silently collide.

### 4.6 Timestamps

Every one of the 38 application tables carries
`created_at timestamptz NOT NULL DEFAULT now()` and
`updated_at timestamptz NOT NULL DEFAULT now()` (with `onupdate=now()`), from
the `Timestamped` mixin in `src/db/base.py`. Only `alembic_version` — Alembic's
own bookkeeping — has neither.

Both are filled by the *database*, not by Python, so a row written by a
migration, a bulk statement or `psql` is timestamped the same way one written
through the ORM is, and every timestamp comes from one clock rather than from
whichever machine ran the code. The rule is "timestamp every row from day one"
precisely because creation time cannot be backfilled: before this rewrite, six
of ~39 tables had `created_at` and exactly one had `updated_at`, so "when did
this change?" was usually unanswerable.

### 4.7 Invariants now enforced by the database

Each of these was Python-only, or unenforced entirely, before this rewrite.

| Invariant | Mechanism | What it refuses |
|---|---|---|
| **Two-level category depth** | generated `depth`/`parent_depth` + `UNIQUE (id, depth)` + `FOREIGN KEY (parent_category_id, parent_depth) REFERENCES categories (id, depth)` | A subcategory of a subcategory, on insert *and* on reparent — re-parenting a row that already has children fails because it would have to leave depth 1 while a child still references it there |
| **Two-level account depth** | the same three constructs on `accounts` | A vault of a vault |
| **`category_id`/`subcategory_id` pairing** | `child_of_category_columns()` — `FOREIGN KEY (subcategory_id, category_id) REFERENCES categories (id, parent_category_id)` **plus** `CHECK (subcategory_id IS NULL OR category_id IS NOT NULL)`, on all 6 tables carrying the pair | A subcategory belonging to some *other* category, and a subcategory with no category beside it. The `CHECK` cannot ride on the FK: default `MATCH SIMPLE` skips the check entirely once any referencing column is `NULL`, and `MATCH FULL` would forbid the legitimate "filed at top level only" row |
| **Per-transaction zero-sum** | `CONSTRAINT TRIGGER postings_balance_at_commit ... DEFERRABLE INITIALLY DEFERRED` calling `accounting.assert_transaction_balances()` | A transaction whose postings do not sum to zero. Deferral is not a convenience — it is the only thing that makes the check possible, since `_write_ledger` writes legs one at a time and every intermediate state is unbalanced. Callers need no `SET CONSTRAINTS`: `INITIALLY DEFERRED` already puts the check at commit for every transaction, so a bulk write is checked once on the final state. **Checked only when all of a transaction's postings share one currency** (`HAVING COUNT(DISTINCT currency) = 1`) — a cross-currency transfer's legs are equal-and-opposite only after a conversion this schema deliberately does not store |
| **One remainder automation** | `uq_goal_automations_user_remainder (user_id) WHERE mode = 'remainder'` | A second automation claiming "whatever is left"; two would each claim the same leftover and the one that ran second would find nothing. Previously a 400 from one endpoint, with nothing stopping a write that did not go through it |
| **One withdrawal entry per goal** | `uq_goal_automations_user_withdrawal_goal (user_id, goal_id) WHERE direction = 'withdrawal'` | A goal appearing twice in the drawdown order |
| **Split-leg ordering** | `uq_posting_split_legs_user_split_ordinal (user_id, posting_split_id, ordinal)` | Two legs of one split claiming the same ordinal — this table shipped with no unique constraint at all, so the order legs came back in was arbitrary |
| **Money positivity, scoped** | `goals.target_amount > 0`; `budgets.amount >= 0`; `other_assets.value >= 0` | A goal already met by definition; a negative spending target; an "asset" that would be silently subtracted from assets rather than counted as debt |
| **Broker link shape** | `CHECK (broker_connection_id IS NULL OR kind = 'external_investment')` + the FK itself | A savings account claiming to mirror a brokerage connection, and a link to a connection that does not exist |
| **Category successor shape** | `CHECK (superseded_by_category_id IS NULL OR retired_at IS NOT NULL)` | A live category claiming to have been merged away |
| **Budget month shape** | `CHECK (month IS NULL OR month ~ '^\d{4}-(0[1-9]\|1[0-2])$')` | `"2024-13"`, which used to store happily and then sort and group as a month that exists |
| **Overlay stage honesty** | `CHECK (stage = '<one value>')` per overlay table; a disjunction on `categorization_rules` | A row claiming a stage its own table is never applied at — an overlay that quietly never runs |
| **Merged-table discriminators** | `ck_goal_automations_schedule_matches_direction`, `ck_categorization_rules_effect_columns`, `ck_suggestions_pending_shape`, `ck_suggestions_dismissed_shape` | Half-populated rows of a merged table: a `withdrawal` carrying half a schedule, a `transfer` rule carrying a category, a `dismissed` suggestion carrying a `posting_id` |
| **Currency / institution / symbol validity** | foreign keys into the three dimension tables | A currency, bank name or ticker that is not a real reference row. Fifteen restated `CHECK (currency IN (...))` clauses are gone, and `trades.ledger_events.currency` gained its first constraint of any kind |
| **No duplicate association rows** | full-tuple primary keys on the five association tables | The same (posting, tag) pairing twice |
| **A transaction in two transfer links** | `uq_transfer_linked_transactions_user_transaction (user_id, transaction_id)` | Exactly what the composite PK allows and this forbids |

**`postings.amount` deliberately has no positivity CHECK.** It is signed by
design: a debit is negative, a credit positive, and every reader sums the
column. The invariant that *both legs of a manual transfer are positive
magnitudes* therefore cannot live on the shared storage; it lives on
`models.ManualTransfer` (`from_amount: Money = Field(gt=0)`,
`to_amount: Money = Field(gt=0)`), the only thing that still expresses them as
distinct directional quantities. `insert_manual_transfers` turns them into the
signed pair `(-from_amount, +to_amount)`, so a negative `from_amount` slipping
through would silently invert the transfer.
`test_postings_amount_is_deliberately_unconstrained` pins the omission so it
reads as a decision rather than an oversight.

**Two things stay in Python, deliberately.** `ledger.replay.validate_balanced`
runs over the imported frame *before* anything is written, so a bad statement
fails the import with a message naming the offending transaction and its running
total instead of surfacing as a `COMMIT`-time trigger abort with no idea which of
ten thousand rows caused it — the engine is the guarantee, the Python check is
the diagnostic. And "a `remainder` automation must be the lowest-priority one"
is a statement about the ordering of a whole list, which no index can express.

The composite foreign key was chosen over a `CONSTRAINT TRIGGER` for the two
depth guards for a stated reason: a trigger enforces the same thing as
procedural PL/pgSQL that has to be kept in step with the model by hand and is
only as good as the events it happens to be declared for, whereas the composite
key is enforced by the same machinery as every other reference, is visible in a
`psql` table description, and cannot be bypassed by a code path nobody thought
of. The trigger is reserved for the one invariant no key can express.

### 4.8 The ledger seam

`accounting` and `trades` remain **two independent ledgers**, in two schemas,
with `accounting` importing nothing from `trades` in Python. Keeping them
separate costs about two extra tables and buys module independence: deleting
either package should not break the other, and `src/migration/env.py` tolerates
either being absent.

They meet in exactly two places.

**1. One typed foreign key.** `accounts.broker_connection_id` →
`trades.broker_connections(id) ON DELETE SET NULL`. It replaces
`Account.external_ref`, a bare `String` set to the literal `"trades"` that
`dashboard.net_worth` string-matched on to decide whose value came from the
investment portfolio. Now the account *names* the connection its value is
pulled from, so "this account mirrors a connection that doesn't exist" is not a
state the database can hold, and `SET NULL` degrades the account to a
manually-valued one when the connection is deleted rather than leaving a
dangling reference. `net_worth.is_trades_linked` is now
`account.broker_connection_id is not None` — one column, not a string match
over two, since the `kind` half is redundant given
`ck_accounts_broker_link_is_an_investment`. This is the one place `accounting`'s
DDL depends on a `trades` table existing; `repositories/accounts.py` reads
across it with raw SQL against the qualified table name rather than importing
`trades.db`, so the Python-side dependency stays what the schema-side
dependency already is — a name.

**2. One normalized sign convention.** The two ledgers disagree about where
direction lives: `accounting` signs the money (`postings.amount` is negative
when money leaves), while `trades` stores an unsigned magnitude in
`ledger_events.amount` and puts direction in `event_type`. Four different places
independently reconstructed a sign with their own
`pl.when(pl.col("event_type") == ...)` expression. `src/trades/ledger/signs.py`
is now the single translation point. It does **not** change what is stored —
the storage convention is fine and rewriting eight event types' worth of history
would be a far larger change than the problem warrants; it changes where the
sign is decided:

```
CASH_EFFECT_SIGN  = {DEPOSIT: +1, WITHDRAWAL: -1, BUY: -1, SELL: +1,
                     DIVIDEND: +1, WITHHOLDING: -1, FEE: -1, SPLIT: 0}
SHARE_EFFECT_SIGN = {BUY: +1, SELL: -1}
```

The convention is **effect on the portfolio's cash balance** — a signed number
you can sum, exactly like `postings.amount`, which is what makes the two ledgers
speak the same language. `SPLIT` is zero rather than a special case every caller
has to remember to filter. `SHARE_EFFECT_SIGN` omits the six event types that
move no shares rather than mapping them to zero. Two callers want the opposite
of the cash convention (money-weighted return treats a deposit as money paid in;
a symbol's "money in" for the month is cash out) and say so by *negating*
`signed_cash_effect()` rather than re-deriving it.
`test_every_ledger_event_type_has_exactly_one_declared_cash_direction` keeps the
table complete.

### 4.9 Immutable postings and the interpretation overlay

**Postings are no longer edited in place.** Every column on `postings` is raw:
what the statement said, or what the user typed into a manual transfer. Nothing
derived or resolved is written back, which is why re-importing a statement can
never silently revert an interpretation. `category_id`/`subcategory_id` are part
of that raw record rather than an exception to it — they are the category the
*file itself* named (only the canonical importer sets them), written once at
import and never rewritten. The in-place category write is deleted.

**All interpretation lives in overlay tables, with precedence declared in
data.** `src/accounting/precedence.py` declares:

```python
OverlayStage = Literal["counterparty", "split", "override", "merge", "link"]
OVERLAY_PRECEDENCE = get_args(OverlayStage)  # declaration order *is* precedence
```

Every overlay table carries a `stage` column CHECK-pinned to the value(s) legal
for it, and the resolver walks `OVERLAY_PRECEDENCE` dispatching to a registered
applier per stage rather than calling them one after another. Before this,
the ordering lived nowhere but the top-to-bottom line order of
`api.dependencies._resolved_postings_and_store`: reading it meant reading a
function body, changing it meant editing a hard-coded sequence, and nothing
stopped a row being written by a stage that had already run.

**How a posting's effective category is determined**, in order:

| Step | Reads | Notes |
|---|---|---|
| 0. Raw read | `transactions` + `postings` (+ `posting_tags`) | `importers.ingest.load_ledger`. The ledger itself, not a layer over it |
| 0.5 Taxonomy resolution | `categories.superseded_by_category_id` / `retired_at` | `ledger.categorization.apply_category_redirects`, following `repositories.taxonomy.load_category_redirects`. A merged-away category resolves to its successor; a deleted one resolves to uncategorized. **Not a stage** — it is a dimension lookup, has no `stage` column and no overlay table, and necessarily precedes all five stages since each reads or writes a category |
| 1. `counterparty` | `categorization_rules` where `effect='transfer'` | Repoints a placeholder counterparty at a real account. First, because every later stage may read the account it resolved |
| 2. `split` | `posting_splits` + `posting_split_legs` | One posting becomes N legs. After `counterparty` so each leg inherits the resolved account; before `override` so the user's last word lands on the split's result |
| 3. `override` | `posting_overrides` + `posting_override_tags`, **plus the `pending` half of `suggestions`** folded into the same `ManualOverride` per posting | The user's direct edit; by definition wins over anything a rule or pattern produced |
| 4. `merge` | `posting_merges` + `posting_merge_duplicates` | Drops whole duplicate transactions. After `override`, because dropping a row first would throw away the override on it |
| 5. `link` | `transfer_links` + `transfer_linked_transactions` | Pairs two transactions. Deliberately last, and not only by preference: stages 2 and 3 rebuild the frame through `LEDGER_FRAME_SCHEMA`, which would silently drop the `is_linked_transfer`/`linked_transaction_id`/`transfer_link_source` columns this stage adds if it ran earlier |

`suggestions` deliberately carries **no `stage`**. A dismissed suggestion only
filters what the detectors propose and never reaches the resolved ledger; a
pending one reaches it only by being folded into its posting's `ManualOverride`,
at the `override` stage `posting_overrides` already declares. Giving it a stage
would declare a precedence it does not have.

A sixth stage, `manual_transfer`, sat between `merge` and `link` and is gone.
`manual_transfers` was a table whose rows had to be turned into postings and
concatenated onto the frame late, because they had no representation in the
ledger to layer over. They do now, so they arrive with the raw ledger and there
is nothing for a stage to generate. Removing it also removed the one stage that
had to be handed the caller's `since`/`until` window separately, because its
rows bypassed `load_ledger`'s own date filter.

The rewrite kept the five interpretation concepts distinct rather than
collapsing them into one generic annotation table, because each changes the
resolved ledger's shape differently: rules repoint, overrides restate, splits
and merges change how many rows a transaction resolves to, links pair.
`transfer_rules` + `category_patterns` merged because they were genuine synonyms
(one matcher over a description, differing only in what they did on a match);
`pending` + `dismissed` suggestions merged because they were one concept at two
lifecycle stages.

### 4.10 Provenance

`transactions.origin` is the discriminator: `imported` (from a statement) or
`manual` (entered by hand). `CHECK (origin IN ('imported','manual'))`.

**Manual transfers are real transactions.** One `transactions` row with
`origin = 'manual'` plus two balancing `postings`. The old `manual_transfers`
table held a date, two accounts, two amounts and a description — everything a
transaction plus two postings already expressed, expressed a second and
incompatible way, which is why its rows needed a resolution stage of their own
before any balance could count them. `ManualTransfer` survives as the API's
vocabulary for the pair and as the home of the pair's positivity invariant, but
it is a shape, not a table.

**`origin` is what makes rebuild-from-source non-destructive.**
`importers.ingest._write_ledger` scopes every prune to `origin = 'imported'`
transactions and their postings, so `rebuild_from_raw_statements` — which
discards the persisted ledger and replays every archived CSV — recomputes the
whole imported half and leaves every manual row exactly where it was. A manual
transfer is reproducible from nothing; nothing a replay does would put one back,
so a rebuild that no longer produces it must not read that as "the user deleted
it." Without that scoping, retiring `manual_transfers` into ordinary
transactions would have made rebuild silently destructive.

**There is no provenance table.** The audit's ~28-table target listed
`raw_statements` (archived-source metadata); the ruling was "metadata only,
never blobs, and skip the table entirely if not needed", and it was not needed.
An `imported` transaction's provenance reference is its archived statement,
addressed by `utils.statement_archive.StatementArchive` — Cloudflare R2
(S3-compatible) when `R2_*` is configured, local disk otherwise — under
`statements/{user_id}/{institution}/{account_id}/{timestamp}`. Per-posting
provenance (an importer's own dedup key, a sha256 prefix from
`importers.common.row_hash`) rides in the `postings.meta` JSONB blob, as it
already did. That archive is
already the source of truth `rebuild_from_raw_statements` and `last_import_at`
read — the account id and kind for each archive are recovered from its own
directory name, and `last_import_at` reads the timestamp encoded in each
filename rather than the file's mtime, so copying `data/` to another machine
cannot make an old import look fresh. A metadata table would shadow that with
nothing to add. A `manual` transaction has no provenance reference at all, which
is the whole of what the discriminator has to distinguish. Confirmed by
introspection: no `raw_statements` table exists in any of the three schemas.

---

## 5. Known gaps and deferred work

Things the schema does **not** guarantee. Re-verified against the code at
1.7.3; the entries the original rewrite listed as "PR 2/3/5 closes it" have
been checked one by one and the closed ones removed.

### Money

- **The API wire format sends money as JSON numbers.** `Money`/`Rate`/`Shares`
  serialize through `PlainSerializer(float, when_used="json")`
  (`src/db/money.py`), so exactness stops at the final JSON encode. This is now
  a settled decision rather than deferred work — see that module's own
  docstring on why the wire format is held exactly where it is.
- **Analytics aggregation is float.** Every balance, net-worth figure, income
  statement line, budget total and chart value is a `Float64` sum, crossing the
  boundary at `db.money.to_analytics_float`. The error is bounded by
  `NUMERIC(18,4)` inputs and re-quantized before storage, but it is not zero,
  and the frontend consumes these numbers. `tests/db/test_money_properties.py`
  covers the conversion boundary; no property test pins the *aggregation* error
  across a whole ledger.

### Read paths

- **Resolved reads still replay the ledger.** `GET /postings` pages by
  transaction (`repositories.ledger.visible_transaction_page`) and
  `GET /ledger/export` pages by posting (`load_ledger_page`), so neither loads
  the full history any more — but the overlay resolution above them still
  replays each page through every stage. `docs/remaining-work.md` **C1** is the
  materialised projection that removes it. `GET /store` never touched the
  ledger and does not now: it returns entities only.
- **The page array is matched on `postings.transaction_id`, never on
  `transactions.id`.** Both are correct — the inner join equates them — but only
  the posting side has an index (`ix_postings_transaction_id_user_id`) for the
  planner to drive a page from. Matching the transaction side made
  `GET /postings` exceed the 15 s `statement_timeout` for any `limit` above
  roughly 300 on a 10k-transaction ledger, against a `PAGE_LIMIT_MAX` of 5,000:
  with no statistics the planner estimated the outer relation at one row and
  chose a nested loop that re-scanned every transaction the tenant owns per
  posting (`loops=20000`, 4.26M buffer hits, 17.7 s). That was **C6**, closed
  in v1.8.0; it is recorded here because the fix is a fact about which index
  this schema offers rather than about the query alone.

  *(The ~65,535-parameter ceiling that used to sit here is closed:
  `repositories.ledger` resolves natural keys with joins rather than `IN (...)`
  lists, so no read binds a parameter per row.)*

### Concurrency

- **`merge_by_natural_key` has an unhandled race.** It resolves
  `(user_id, natural_key) -> id` in one query, then `session.merge()`es each
  row. Two concurrent requests inserting the *same* new natural key both see no
  existing row, both insert, and the second violates
  `uq_*(user_id, natural_key)`. Current impact is a failed request, not
  corruption — the constraint is doing its job. Closing it (an
  `INSERT ... ON CONFLICT DO UPDATE`, or a retry) is still open. Note that
  `merge()` is deliberately the ORM's rather than raw SQL, because `accounts`
  and `categories` carry `GENERATED ALWAYS ... STORED` columns no statement may
  write and their `updated_at` is maintained by the mapper's `onupdate`; a fix
  has to preserve both.
- **Multi-write handlers still commit as they go.** `delete_category` and
  `post_category_rename` each span several repository writes that commit
  individually, so a failure partway through leaves the earlier ones committed.
  The version-conflict half of this is gone with `save_store`; what remains is
  transaction scope. See `docs/known-gaps.md` §1.

### Tooling and dead code

- **`statement_archive` is duplicated.** `accounting.utils.statement_archive`
  and `trades.utils.statement_archive` are two copies of the same module. Not a
  schema problem — but the accounting one is the provenance mechanism §4.10
  relies on, so it is worth knowing there are two.

### Schema-level observations

- **One redundant index.** `ix_ledger_event_trade_details_ledger_event_id_user_id`
  leads with `ledger_event_id`, which is already that table's entire primary
  key. The index walk produces it because `(ledger_event_id, user_id)` is not a
  prefix of any existing index, but the cascade path it exists to serve is
  already covered by the PK. Harmless write cost, not a correctness issue.
- **`goal_contributions.account_id` is recorded but unused.** Nothing in
  `dashboard.goals` reads it, so setting it changes no goal balance, no
  net-worth figure and no unallocated total. The envelope-over-balance
  arithmetic that would use it is a later change.
- **`postings.budget_id` is a real FK nothing sets.** Kept as a typed reference
  so a caller can use it without a migration, but non-null values never occur
  today.
- **Institution and security names are visible across tenants** to anything that
  queries those tables directly. Nothing does — the app only writes a name it
  already has and reads it back off the row — and a bank name or ticker is not
  a financial fact, but the shared namespace is a real trade-off, not an
  oversight.
- **Cross-currency transactions are never balance-checked.** The zero-sum
  trigger skips any transaction whose postings do not all share one currency,
  because the schema deliberately stores no exchange rate. A cross-currency
  transaction with two arbitrary amounts is accepted.
- **Tenant isolation is proved two ways, and both are required checks.**
  `tests/db/test_rls_coverage.py` introspects `pg_policies` on a freshly
  migrated scratch database and proves every tenant table has a forced policy
  with both `USING` and `WITH CHECK`; `tests/db/test_rls_isolation.py` proves
  end to end that one tenant cannot read another's rows *through the restricted
  `app_runtime` role*. Both run in the `Migrations apply and match the models`
  job, which blocks a merge. (Listed here because it is the guarantee this
  schema's whole RLS design exists to make, not because anything is missing.)
- **`other_assets` and `simulator_scenarios` are in the taxonomy aggregate for
  want of a better home**, and are acknowledged as misplaced in that module's
  own docstring.

---

## 6. Reproducing this document's facts

```bash
# a scratch database, migrated from the single baseline
docker exec finance-postgres-dev psql -U finance -d postgres \
  -c "CREATE DATABASE schema_doc_scratch OWNER finance;"
DATABASE_URL='postgresql+psycopg://finance:…@localhost:5433/schema_doc_scratch' \
  uv run alembic upgrade head

# tables, columns, keys, CHECKs, FKs, indexes, triggers, policies
docker exec finance-postgres-dev psql -U finance -d schema_doc_scratch -c '\d+ accounting.postings'

# policy coverage
docker exec finance-postgres-dev psql -U finance -d schema_doc_scratch \
  -c "SELECT count(*) FROM pg_policies;"

docker exec finance-postgres-dev psql -U finance -d postgres \
  -c "DROP DATABASE schema_doc_scratch;"
```

The models and the migrated database were cross-checked programmatically —
`alembic.autogenerate.compare_metadata` against `Base.metadata`, plus a direct
comparison of `CHECK` constraint names, `UNIQUE` constraint names, generated
columns, table names and index names. **They agree exactly.** The only table in
the database that is not in the models is `public.alembic_version`, which is
Alembic's own. No discrepancy was found.

CI checks that same agreement on every PR: the `Migrations apply and match the
models` job runs `alembic check` against a freshly migrated database, so
*models versus database* can never silently drift. **This document**'s table
inventory is gated against the models too, by
`tests/db/test_schema_doc_drift.py` (item E8) — which is why the counts above
and in §1 can be trusted, and why no line of §4's reasoning can be.

Never point any of this at `finance_dev`, `finance_test`, or a remote host.
