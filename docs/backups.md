# Backups: the whole database, and five market-data caches

This repo has **two unrelated backup systems**, not one — easy to conflate
since both write to the same Cloudflare R2 bucket (or the same local
`data/backups/` fallback) and both keep the same amount of history via the
same `BACKUP_RETENTION_COUNT` env var, but they differ in the two ways
that actually matter: whether a bad backup gets caught before it's kept,
and whether restoring one is automatic or requires a human.

1. **The whole Postgres database** (`db.backup`) — every real record:
   accounts, categories, tags, postings, overrides, transfer rules,
   budgets, users, everything. One dump, once a day.
2. **Five flat-file market-data caches** — raw daily prices, adjusted
   daily prices, CPI, HYSA rates, exchange rates. These never go into
   Postgres at all (see `trades.db.models`'s own module docstring:
   "identical for every user," so there's no per-user data to isolate).

| | Verified before it's trusted? | Restored automatically? |
|---|---|---|
| **Database** | Yes — restored into a scratch DB first | **No — always manual** |
| **The 5 caches** | No — bytes are backed up as-is | **Yes — automatic on read** |

That table is the one thing worth remembering above everything else
below: the two systems are *opposite* on both of the questions that
matter most.

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
2. **Verify — the check the cache backups below don't have.** Before
   touching anything else, the dump is proven restorable by actually
   restoring it into a disposable scratch database
   (`CREATE DATABASE backup_verify_<uuid>`, `pg_restore --clean
   --if-exists`, then `DROP DATABASE ... WITH (FORCE)` in a `finally`
   block regardless of outcome). If this fails, the run stops here —
   nothing is uploaded, nothing existing is touched. This is a real
   correctness check, not just "did the file write successfully": a dump
   that's syntactically fine but can't actually rebuild the database
   never gets kept.
3. **Upload** — named `{UTC timestamp}.dump` (e.g. `20260713T030000Z.dump`
   — sorts alphabetically the same as chronologically, on purpose) and
   written to R2 under `backups/postgres/` if `R2_ACCOUNT_ID`/
   `R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`/`R2_BUCKET_NAME`/
   `R2_ENDPOINT_URL` are all set, otherwise to local disk under
   `data/backups/postgres/`.
4. **Prune** — deletes everything beyond the newest `BACKUP_RETENTION_COUNT`
   backups (see [Retention](#retention-shared-by-both-systems) below).
   Only ever runs after a successful verify + upload, so a bad dump can
   never cause a good one to be pruned away.

**Restoring one is always manual — nothing auto-restores the database.**
That's a deliberate choice, not a gap: auto-restoring in place is
genuinely risky (wrong point-in-time, a false-positive trigger deciding
"this looks broken" when it isn't). To actually restore:

```
pg_restore --clean --if-exists -d <DATABASE_URL> <dump-file>
```

using the `finance` owner role (never `app_runtime` — restoring needs
DDL/superuser privileges the restricted runtime role doesn't have).

## 2. The five market-data caches

| # | Dataset | Module | File | Refreshed by |
|---|---|---|---|---|
| 1 | Raw daily close | `trades.market_data.prices` | `data/trades/prices/<symbol>.csv` | `price_sync`, 3×/day |
| 2 | Adjusted daily close | `trades.market_data.prices` | `data/trades/prices/<symbol>.adjusted.csv` | `price_sync`, 3×/day |
| 3 | CPI index | `trades.market_data.cpi` | `data/trades/cpi/*.csv` | `daily_sync`, 1×/day |
| 4 | HYSA rate history | `trades.market_data.hysa_rates` | `data/trades/hysa_rates/*.csv` | `daily_sync`, 1×/day |
| 5 | Exchange rate history | `accounting.market_data.exchange_rates` | `data/accounting/exchange_rates/rates.csv` | `fx_sync`, 3×/day |

Raw and adjusted price are two separate datasets, not one — separate
files, separate backup histories, and (see below) genuinely different
fetch strategies, even though both come from `trades.market_data.prices`
and refresh on the same schedule.

### Backup: no verification step, unlike the database

`backup_cache_file` uploads whatever bytes are currently on disk —
**no check that they're well-formed or correct**, nothing equivalent to
the database's scratch-restore proof above. A bug that wrote
valid-but-wrong data (parses fine, values are nonsense) would get backed
up exactly as readily as a good write. Keeping several versions instead
of one slot (see Retention) means a single bad write doesn't destroy
every fallback at once, but it's not a substitute for real verification —
there's currently no way to prove a cached price is *correct*, only that
the *file* parses.

### Restore: automatic, unlike the database

Triggered the moment something tries to *read* a corrupted file — no
health check, no schedule. Every reader goes through
`trades.utils.io_utils.read_csv_recovering_from_corruption`: it tries to
parse the file; if that fails, it calls `restore_cache_file`, which finds
whichever version is newest and restores it, transparently, no human
involved. If there's no backup at all, or the restored copy is itself
unparseable, it raises loudly instead of silently treating the cache as
empty.

This only guards against on-disk *corruption* (a botched write, a Docker
volume issue) — never a failed *fetch*. A failed fetch (network error,
upstream API down) never touches the existing file in the first place;
every writer here only calls `write_csv_atomic` after a fully successful
fetch, so a failed fetch just leaves the existing file stale-but-valid,
needing no recovery.

### Two datasets that deliberately aren't backed by incremental fetches

Most of these caches only fetch what's genuinely new since last time. Two
exceptions, both about *fetching*, not backup:

- **Raw prices (dataset 1) always re-fetch the trailing 2 days**
  (`_SETTLEMENT_BUFFER_DAYS = 2` in `prices.py`), even though those days
  are already cached. A price fetched while the market is still open can
  be live and still-moving — if the cache trusted it as final the moment
  it was first fetched, a stale, never-corrected number would sit there
  forever. Re-checking the last 2 days on every run means an early,
  provisional value always gets overwritten once the real close is known.
- **Adjusted prices (dataset 2) never fetch incrementally at all** —
  `refresh_adjusted_price_history` re-downloads and overwrites the
  *entire* cached history, every run, on purpose (its own docstring:
  *"Deliberately not incremental, unlike `update_price_cache`"*). Yahoo
  retroactively recalculates the adjusted close for a symbol's whole
  history every time it pays a new dividend or splits — an incremental
  fetch would leave old, already-cached dates silently wrong with no way
  to know which ones changed. Full re-fetch is the only way this dataset
  stays correct.

## Retention (shared by both systems)

One env var controls how much history is kept everywhere:
`BACKUP_RETENTION_COUNT` (defaults to 14 if unset). Both systems reuse
the exact same pruning logic (`db.timestamped_backups`) and the exact
same setting — not a separate count per dataset — but each keeps its
*own* count of *its own* history:

- **Database**: the newest 14 daily dumps (~2 weeks).
- **Each of the 5 caches, independently**: the newest 14 versions of
  *that* file. Since prices and exchange rates back up up to 3×/day but
  CPI/HYSA back up once/day, "14 versions" is a different amount of
  *time* per dataset — roughly 4–5 days of history for the 3×/day caches,
  ~2 weeks for the 1×/day ones. Pruning `prices/AAPL.csv`'s old versions
  never touches `prices/MSFT.csv`'s or anything else's — every key has
  its own independent history.

## Cron schedule (all times UTC, deploy VM's own `crontab`)

| Job | Times | Why that many, why then |
|---|---|---|
| `db.backup` | 3:00 | Once daily, ahead of everything below (not a real dependency — just a clock time that doesn't collide) |
| `trades.market_data.daily_sync` (CPI + HYSA) | 4:00 | Neither has a precise publish time worth chasing (CPI is monthly; HYSA changes on no fixed schedule), so once a day is enough |
| `accounting.market_data.fx_sync` (exchange rates) | 14:00, 14:45, 15:30 | Brackets the ECB's ~16:00 CET daily reference rate across both CEST and CET (summer/winter clocks) |
| `trades.market_data.price_sync` (raw + adjusted prices) | 20:30, 21:15, 22:00 | Brackets US markets' 4pm ET close across both EDT and EST |

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
- **Tune retention** for the database and all 5 caches at once:
  `BACKUP_RETENTION_COUNT` in `.env` (defaults to 14).
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
