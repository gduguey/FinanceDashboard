# Backups: the whole database, and the four market-data caches

This repo has **two unrelated backup systems**, not one — easy to conflate
since both write to the same Cloudflare R2 bucket (or the same local
`data/backups/` fallback), but they protect different things, on
different schedules, with different restore behavior:

1. **The whole Postgres database** (`db.backup`) — every real record: accounts,
   categories, tags, postings, overrides, transfer rules, budgets, users,
   everything. One dump, once a day, kept for a fixed history.
2. **Four flat-file market-data caches** — daily prices, CPI, HYSA rates,
   exchange rates. These never go into Postgres at all (see
   `trades.db.models`' own module docstring: "identical for every user,"
   so there's no per-user data to isolate). Each keeps exactly **one**
   backup slot, restored automatically the moment corruption is detected.

Mixing these up matters: **the database backup never auto-restores**
(restoring it is a deliberate, manual, human decision) — but **the cache
backups do**, silently, every time. Read the wrong section and you'll
draw the wrong conclusion about what happens automatically.

## 1. The whole database — `db.backup`

Run via `python -m db.backup`, once daily at **3:00 UTC** as a `crontab`
entry on the deploy VM (see `docs/server-setup/production.md`'s Step
16) — nothing in this repo schedules it on its own.

`run_backup()` does four things, strictly in this order:

1. **Dump** — `pg_dump --format=custom`, connecting as `finance`
   (`DatabaseSettings`, the migration-owning superuser role, never the
   RLS-restricted `app_runtime` role the app itself uses day to day — a
   backup has to see every row in every table regardless of Row-Level
   Security).
2. **Verify** — before touching anything else, the dump is proven
   restorable by actually restoring it into a disposable scratch
   database (`CREATE DATABASE backup_verify_<uuid>`, `pg_restore --clean
   --if-exists`, then `DROP DATABASE ... WITH (FORCE)` in a `finally`
   block regardless of outcome). If this fails, the run stops here —
   nothing is uploaded, nothing existing is touched.
3. **Upload** — named `{UTC timestamp}.dump` (e.g. `20260713T030000Z.dump`
   — sorts alphabetically the same as chronologically, on purpose) and
   written to R2 under `backups/postgres/` if `R2_ACCOUNT_ID`/
   `R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`/`R2_BUCKET_NAME`/
   `R2_ENDPOINT_URL` are all set, otherwise to local disk under
   `data/backups/postgres/`.
4. **Prune** — deletes everything beyond the newest `BACKUP_RETENTION_COUNT`
   backups (an env var, defaults to 14 — about two weeks daily). Only
   ever runs after a successful verify + upload, so a bad dump can never
   cause a good one to be pruned away.

**Restoring one is always manual.** Nothing in this repo watches for a
problem and restores automatically — that's a deliberate choice, not a
gap (auto-restoring in place is genuinely risky: wrong point-in-time,
false-positive trigger, etc.). To actually restore:

```
pg_restore --clean --if-exists -d <DATABASE_URL> <dump-file>
```

using the `finance` owner role (never `app_runtime` — restoring needs
DDL/superuser privileges the restricted runtime role doesn't have).

## 2. The four market-data caches

| Dataset | Module | Cache file(s) | Fetched/refreshed by |
|---|---|---|---|
| Daily prices (raw + adjusted) | `trades.market_data.prices` | `data/trades/prices/<symbol>.csv` (one pair per symbol) | `trades.market_data.price_sync` |
| CPI index | `trades.market_data.cpi` | `data/trades/cpi/*.csv` | `trades.market_data.daily_sync` |
| HYSA rate history | `trades.market_data.hysa_rates` | `data/trades/hysa_rates/*.csv` | `trades.market_data.daily_sync` |
| Exchange rate history | `accounting.market_data.exchange_rates` | `data/accounting/exchange_rates/rates.csv` | `accounting.market_data.fx_sync` |

None of these are user-scoped or in Postgres — they're the same data
regardless of who's using the app, so they live as plain CSVs on disk,
refreshed by their own cron job (see the schedule table below).

### How the backup/restore mechanism works

Two near-identical modules do this — `accounting.utils.cache_backup` and
`trades.utils.cache_backup` — deliberately separate copies rather than a
shared import (this repo's `db` layer is the only thing both `accounting`
and `trades` are allowed to depend on; neither package depends on the
other).

- **Backup**: every time one of these jobs successfully fetches and
  writes its cache file, it immediately calls `backup_cache_file`, which
  uploads that file as the *one* "last known good" copy — to R2 (prefix
  `cache-backups/accounting` or `cache-backups/trades`) if configured,
  otherwise to local disk under `data/backups/cache/accounting/` or
  `data/backups/cache/trades/`. No history, no retention logic — each
  success simply overwrites whatever was backed up before.
- **This only guards against on-disk corruption, not a failed fetch.** A
  failed fetch (network error, upstream API down) never touches the
  existing file at all — every writer here only calls
  `write_csv_atomic` after a fully successful fetch, so a failed fetch
  just leaves the existing file stale-but-valid, needing no recovery.
- **Restore is automatic and reactive** — triggered the moment something
  tries to *read* a corrupted file, not by any kind of health check.
  Every reader goes through `trades.utils.io_utils.read_csv_recovering_from_corruption`:
  it tries to parse the file; if that fails, it calls `restore_cache_file`
  to pull the one backed-up copy and retries the read, transparently, no
  human involved. If there's no backup, or the restored copy is itself
  unparseable, it raises loudly instead of silently treating the cache
  as empty.

This is the opposite restore behavior from the database backup above —
worth restating plainly: **a corrupted cache file heals itself the next
time it's read; a corrupted database does not, and never will without
someone deliberately running `pg_restore`.**

### Cron schedule (all times UTC, deploy VM's own `crontab`)

| Job | Times | Why that many, why then |
|---|---|---|
| `db.backup` | 3:00 | Once daily, ahead of everything else below (not a real dependency — just a clock time that doesn't collide) |
| `trades.market_data.daily_sync` (CPI + HYSA) | 4:00 | Neither has a precise publish time worth chasing (CPI is monthly; HYSA changes on no fixed schedule), so once a day is enough |
| `accounting.market_data.fx_sync` (exchange rates) | 14:00, 14:45, 15:30 | Brackets the ECB's ~16:00 CET daily reference rate across both CEST and CET (summer/winter clocks) |
| `trades.market_data.price_sync` (prices) | 20:30, 21:15, 22:00 | Brackets US markets' 4pm ET close across both EDT and EST |

The three-times-a-day jobs exist because a plain `crontab` entry can only
fire at a fixed UTC clock time — it has no concept of "4pm Eastern" — so
rather than pick one UTC time that would silently drift an hour off twice
a year at the DST changeover, each runs a few times in a tight window
spanning both cases. Every one of these times is only actually correct if
the VM's system clock is set to UTC — nothing here converts timezones at
runtime, it's a fixed schedule built on that assumption (see
`docs/server-setup/production.md`, which says so explicitly).

## Quick reference

- **Configure R2** (optional for both systems — everything falls back to
  local disk without it): `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
  `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_ENDPOINT_URL` in `.env`.
- **Tune database retention**: `BACKUP_RETENTION_COUNT` in `.env`
  (defaults to 14).
- **Check the cron jobs are actually live**: `ssh oci-finance-dashboard 'crontab -l'`.
- **Check past runs**: `/var/log/finance-backup.log`,
  `/var/log/finance-price-sync.log`, `/var/log/finance-fx-sync.log`,
  `/var/log/finance-daily-sync.log` on the deploy VM.
- **Restore the database**: manual only —
  `pg_restore --clean --if-exists -d <DATABASE_URL> <dump-file>`.
- **Restore a cache file**: never manual — happens automatically the
  next time it's read, if it's ever found corrupted.

See `src/db/README.md` for how the database backup fits into this repo's
broader wipe-vs-upsert persistence conventions, and
`docs/trades/market_data.md` for more on the price/CPI/HYSA caching
behavior itself (not just its backup).
