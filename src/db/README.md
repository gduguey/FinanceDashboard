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
| `models.py` | The three tables that live outside any one module's own schema: `users`, `user_secrets`, and `external_identities`. |
| `current_user.py` | Which user is making the current request — a placeholder name FastAPI resolves to the real Clerk-session-derived identity at runtime; see "Which user is making this request" below for exactly how. Deliberately has no `DEFAULT_USER_ID` or any other fallback identity — that's a test-only concept, defined in `tests/conftest.py` instead. |
| `external_identities.py` | `lookup_user_id`/`link_identity` — the only place a `(provider, external id)` pair is ever read or written; see "The two tables here" below. |
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

## The three tables here: `users`, `user_secrets`, and `external_identities`

- **`users`** — one row per person using the app, created automatically
  (via `trades.api.webhooks`) the moment someone accepts a Clerk invite —
  every other table's `user_id` column foreign-keys to this table. Just
  `id` (a random UUID, unrelated to anything Clerk-specific) and `email`.
  `users` deliberately knows nothing about Clerk, or any other identity
  provider — that mapping lives in `external_identities` instead (below),
  so this core table (and everything foreign-keyed to it) stays usable
  even if this app ever swaps identity providers.
  `hashed_password`/`is_active`/`is_superuser`/`is_verified` are
  vestigial — kept only because this table was originally shaped to match
  what a different auth library expected, before Clerk became this app's
  real identity provider; nothing reads them anymore.
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

RLS is **opt-in per table**, not a database-wide switch. The
`817ace9deb09` migration has a plain list, `_USER_SCOPED_TABLES` — every
`(schema, table, ownership_column)` that should be isolated by user —
covering `users`, `user_secrets`, every `accounting.*` table,
`broker_connections`, `ledger_events`, and so on. Its `upgrade()` just
loops over that list and runs this on each one:

```sql
ALTER TABLE some_table ENABLE ROW LEVEL SECURITY;
ALTER TABLE some_table FORCE ROW LEVEL SECURITY;
CREATE POLICY user_isolation ON some_table
  USING (user_id = current_setting('app.current_user_id', true)::uuid)
  WITH CHECK (user_id = current_setting('app.current_user_id', true)::uuid);
```

A table gets this protection by being added to `_USER_SCOPED_TABLES` —
there's no separate "disable RLS" command anywhere; a table simply never
gets it if it's never added to that list. `external_identities` is the
one deliberate case of that (see "The three tables here" above, and "Why
`external_identities` can't have RLS" below) — everything else this app
owns is in the list.

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
`GET /api/overview`:

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
one. That's not automatic today (nothing currently listens for Clerk's
`user.deleted` event) — the manual step this requires is a single-row
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

So `external_identities` is simply never added to `817ace9deb09`'s
`_USER_SCOPED_TABLES` list (see "Row-Level Security" above) — there's no
special "turn RLS off" command involved, it's protected the way any table
not in that list is: not at all, by omission. That's an acceptable,
deliberate trade-off specifically *because* this table holds no financial
data, only an identity mapping (`provider`, `external_id`, `user_id`) —
every other table this app owns, which does hold real user data, stays in
the list.

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

`db.backup.run_backup(retention_count=14)` (run as `python -m db.backup`)
does four things, in order:

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
4. **Prunes** old backups (`prune_old_backups`) down to the newest 14 —
   whichever backend is active (R2 or local disk), backups are sorted
   newest-first by their timestamp-prefixed filename (already
   lexicographically = chronologically sortable) and anything beyond the
   newest 14 is deleted. Only ever runs after a successful verify + upload,
   so a failed backup never causes a good one to be pruned away.

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
