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
| `base.py` | The shared SQLAlchemy `Base` every table (in every module) is declared on, plus `derive_id`/`natural_keys_by_id`, a shared helper for turning a human-chosen string into a stable internal id. |
| `models.py` | The two tables that live outside any one module's own schema: `users` and `user_secrets`. |
| `current_user.py` | Which user is making the current request. Today there's only one person using this app and no login page yet, so this always returns the same fixed id — swapping in real login later only means changing this one function. |
| `encryption.py` | Encrypts/decrypts anything stored in `user_secrets.ciphertext`. |
| `secrets.py` | `get_secret`/`set_secret`/`delete_secret` — the only way any code in this repo reads or writes a credential. |
| `backup.py` | Dumps the whole database and uploads it somewhere durable. |

## Stable ids: `derive_id`, and why its namespace constant is a plain constant, not a secret

Most tables' `id` column isn't a random id — it's computed from something
human-chosen (an account's `institution:kind:last4`, a category's own
name-based key, ...) via `db.base.derive_id`, so that re-deriving it later
from the same input always gives back the exact same id (see that
function's own docstring for why: some tables get fully rewritten on every
save, and re-imports need to recognize "this already exists" instead of
creating a duplicate).

The mechanism is `uuid.uuid5(namespace, name)` — a **hash function**, not a
random generator: think of it as a recipe. Feed it the exact same two
ingredients twice, and you get the exact same output both times, no
randomness involved; feed it different ingredients, and the output comes
out completely different. The two ingredients it needs:

- **`name`** — the actual thing being described, e.g.
  `"<user id>:accounts:chase:checking:1234"`.
- **`namespace`** — a second ingredient the recipe requires that's always
  the *same* value no matter what's being described. It doesn't need to
  mean anything; it just needs to never change.

`_ID_NAMESPACE` is that second ingredient: one arbitrary UUID, generated a
single time, hardcoded directly in `base.py`. Change it, and every id this
function has ever produced would come out different if recomputed — every
foreign key pointing at an old id would suddenly point at nothing.

**Should it live in `.env`, or somewhere with backups, instead of hardcoded
in source?** No — that would make it *less* safe, not more. `.env` files
are gitignored on purpose (that's what makes them safe for actual secrets)
and exist only as loose, uncommitted copies on whichever machines they've
been manually pasted onto — no version history, no diff if someone edits
one character by mistake, and nothing stopping the value from silently
drifting between a laptop's `.env` and the server's `.env.docker`. This
constant needs the exact opposite properties: it must be **identical in
every environment, forever**, and a plain constant in versioned source
code already guarantees both, for free — every clone of this repo has the
same value, every change to it shows up in `git log`/`git blame` as an
ordinary, reviewable commit, and reverting it is a normal `git revert`.
Putting it in `.env` instead would introduce the exact failure mode it's
protecting against: a copy-paste slip, or someone regenerating "a new
one" thinking it's like `APP_SECRETS_ENCRYPTION_KEY`, would quietly break
every id derivation in that one environment.

This isn't a workaround specific to this app, either — it's how `uuid5` is
meant to be used. The `uuid` module itself ships several of these same
fixed namespace constants built in (`uuid.NAMESPACE_DNS`,
`uuid.NAMESPACE_URL`, ...), hardcoded in the Python standard library's own
source, unchanged since the format was standardized. `_ID_NAMESPACE` is
the same idea at this app's scale: mint one arbitrary constant, commit it,
never touch it again.

The real risk isn't "it gets deleted" (`git revert` fixes that
immediately) — it's a one-character edit slipping through review
unnoticed. `tests/db/test_base.py` pins the exact expected output of
`derive_id` for a fixed input, so a change to `_ID_NAMESPACE` fails a test
immediately instead of silently shipping.

## Primary key patterns across every table

Every table in this database falls into exactly one of three key shapes.
Which one a new table should use isn't a free choice — it follows directly
from two questions: *does this table get bulk-rewritten or re-imported?*
and *does anything else foreign-key against its `id`?*

**1. `derive_id`-derived `id`, plus a `natural_key` column** — for tables
that get fully rewritten on every save, or re-imported from an external
source (so "insert, or recognize this already exists" has to work without
a lookup). This is the majority of user-owned tables:
`accounting.accounts`, `categories`, `tags`, `transactions`, `postings`,
`manual_transfers`, `other_assets`, `posting_merges`,
`dismissed_suggestions`, `goals`, `goal_contributions`,
`recurring_additions`, `transfer_rules`, `category_patterns`, `budgets`,
`simulator_scenarios`; `trades.broker_connections`, `ledger_events`.

**2. A plain random `uuid.uuid4()` surrogate `id`** — for tables that are
never bulk-rewritten and have no re-import/dedup concept, just an ordinary
"create one row, maybe delete it later" lifecycle. Uniqueness (where it
matters) comes from a separate `UniqueConstraint`, not the id itself:
`public.users`; `accounting.posting_tags`, `posting_splits`,
`posting_split_legs`, `posting_merge_duplicates`, `posting_overrides`,
`posting_pending_suggestions`, `general_budgets`,
`withdrawal_priority_entries`.

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
pattern rather than `derive_id`: neither is ever bulk-rewritten or
re-imported, and nothing else in the schema foreign-keys against either
one's identity — so a surrogate id would just be one more column with no
job to do. Using `derive_id` here would be following the majority
pattern out of habit rather than for a reason that actually applies.

## The two tables here: `users` and `user_secrets`

- **`users`** — one row per person using the app. Right now there's
  exactly one user (`current_user.DEFAULT_USER_ID`) and no real login —
  every other table's `user_id` column already foreign-keys to this table,
  so adding real authentication later is wiring a login page on top of a
  table that already has the columns it needs (`hashed_password`,
  `is_active`, `is_superuser`, `is_verified`), not a schema change.
- **`user_secrets`** — one row per `(user_id, key)` pair: a broker token,
  an LLM API key, whatever comes next. `key` is a caller-chosen name like
  `"broker:ibkr"` or `"llm:gemini"`; `kind` groups secrets by shape
  (`"broker_credentials"`, `"llm_api_key"`) so "every broker credential
  across every user" is a real filter, not string-matching on `key`.
  `ciphertext` is never plaintext — see Encryption below. This is the only
  place a credential is ever stored; nothing in this app keeps a secret in
  a `.env` file, a JSON file on disk, or anywhere else.

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

Every user-owned table gets a Postgres policy (added by the
`817ace9deb09` migration) roughly equivalent to:

```sql
ALTER TABLE some_table ENABLE ROW LEVEL SECURITY;
ALTER TABLE some_table FORCE ROW LEVEL SECURITY;
CREATE POLICY user_isolation ON some_table
  USING (user_id = current_setting('app.current_user_id', true)::uuid);
```

This means: even if a query somewhere in the code forgot its own
`WHERE user_id = ...` filter, Postgres itself still refuses to return
another user's rows — the database enforces isolation as a second,
independent layer, not just the application's own filtering.

For this to mean anything, Postgres has to know *which* user is making the
current request. `db.session.get_db` (the FastAPI dependency every route
uses to get a database session) sets that, once, right at the start of
every request:

```python
session.execute(
    text("SELECT set_config('app.current_user_id', :user_id, true)"),
    {"user_id": str(get_current_user_id())},
)
```

The `true` third argument (`is_local`) scopes this to the current
transaction only — it's automatically forgotten the moment the request's
transaction ends, so it can never leak into a pooled connection's next,
unrelated request.

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

`db.backup.run_backup()` (run as `python -m db.backup`) does three things,
in order:

1. **Dumps** the whole database with `pg_dump --format=custom`, connecting
   as `finance` (`DatabaseSettings`, never `AppRuntimeDatabaseSettings`) —
   a backup has to see every row in every table regardless of RLS, which
   is exactly the privilege the restricted `app_runtime` role must never
   implicitly have.
2. **Names** the dump by timestamp: `{UTC timestamp}.dump`.
3. **Uploads** it to Cloudflare R2 under `backups/postgres/`, if R2 is
   configured (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
   `R2_BUCKET_NAME`, `R2_ENDPOINT_URL` all set) — otherwise it falls back
   to writing the same file to local disk, under `data/backups/postgres/`.

**This does not run by itself.** `python -m db.backup` is just a command —
nothing in this repo schedules it automatically. Making it run on a
recurring basis (e.g. daily) is an infrastructure-level setup step, done
once, outside of this code; it isn't part of what `docker compose up`
brings up on its own.

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
