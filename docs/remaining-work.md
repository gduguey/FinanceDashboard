# Remaining work after the five-PR refactor

The five-PR VISION refactor landed as `v1.2.0` … `v1.7.0`. This is the triaged
backlog that came out of it: the entries in `known-gaps.md`, plus a repo-wide
audit, each one decided rather than left open. Item ids are stable — cite them
in branch names, commits and PR descriptions.

`KEPT` items are planned. `DROPPED` items were considered and consciously
declined, with the reason recorded so nobody re-opens them from an audit
document six months from now.

Two rules for anyone working this list:

**Version bumps follow the policy, not the previous PR.** `PATCH` for anything
whose user-visible behaviour does not change on purpose — which most of this
list is; `MINOR` only for something additive; `MAJOR` only when deploying needs
manual intervention beyond `git pull` and a restart. Say in the commit body
which rule applied. (The policy itself is at `docs/server-setup/versioning.md`,
which is gitignored — see G8.)

**Check whether it is already handled before building anything.** Read the
neighbouring modules and their docstrings first. A1 and A3c on this list were
both queued as real bugs and both withdrawn on exactly that basis: the machinery
already existed one module over. Docstrings count as documentation. If you
conclude something is broken, state what you checked that would have covered it
and did not.

## A — wrong numbers or lost data

| id | item | status |
|----|------|--------|
| A1 | Import double-count via description drift | **DROPPED** — not a gap. `ledger.duplicates` already surfaces "the same real-world purchase imported twice" via fuzzy description matching inside a 3-day window, as a suggestion a human confirms; `importers.ingest._reassign_colliding_transaction_ids` handles the inverse case (two genuinely identical rows get distinct ids). `importers.common.row_hash`'s docstring documents why identity is only account/date/amount/description. The canonical CSV path has no bank-provided id to prefer instead. |
| A2 | `docs/accounting/adding-accounts.md` teaches `str(amount)` hashing | **KEPT** — the tutorial is copy-paste guidance and still teaches the amount-formatting bug the real importers fixed (`1500.0` / `1500.00` / `1500` hash three ways). Update both code blocks to `f"{amount:.4f}"` and carry the explanatory comment. |
| A3a | "Unallocated money" ignores opening balances | **KEPT** — the computation matches its own docstring, but it counts only income and expense flows, so any opening balance is invisible to it. Include opening balances. |
| A3b | Historical FX converted at today's rate | **KEPT** — income statement, budgets and spend curve pass no as-of date. Convert per posting date using the trailing-30-day mean as of that date, matching `currency-handling.md`'s anti-noise rationale. Net worth and goals already thread an as-of date. |
| A3c | Card refund counts as income | **DROPPED** — not a gap. `dashboard.income_statement.real_income_expense_legs` already excludes any transaction marked a transfer, manually or by a `TransferRule`. Resolving a refund is the general mechanism working as designed; hardcoding "positive amount on a liability account is not income" would be per-case special-casing. |
| A3d | `signColor` paints exactly zero green | **KEPT** — `value >= 0` returns emerald. Fix the helper and audit the callers that compensate with `signColor(-x)`. |
| A3e | "Alpha vs HYSA" labels two different quantities, neither of which is alpha | **KEPT** — rename each to what it computes, and move the hardcoded `0.04` placeholder rate into configuration. |
| A4a | Automation reorder can lose a concurrent write | **DROPPED** — one user, two tabs, a deliberate drag in both at once. The consequence is a reorder that visibly does not stick, and is retried. Not worth a lock. |
| A4b | Target-allocation save can lose a concurrent write | **KEPT** — a database-side `jsonb` merge, **not** a version column: `trades.db.models` deliberately documents this row as last-write-wins. `application/merge-patch+json` promises that patching two different symbols composes; today it does not. |

## B — guarantees that are not enforced

| id | item | status |
|----|------|--------|
| B1 | The tenant-isolation CI job is not a required check | **KEPT** — the job running `alembic check`, the RLS coverage test and the isolation suite cannot block a merge, and the release job is `needs: test` alone. Add it to the ruleset's required contexts and to the release job's dependencies. |
| B2 | Lint warnings do not fail the build | **KEPT** — `biome lint` exits 0 on warnings; four rules are pinned to `error` as a workaround, so any new warn-level rule is unenforced. Fail on warnings, with a documented exception for the rule intentionally left as a warning. |
| B3 | Nothing protects the performance gains | **KEPT** — no latency gate, no web-vitals instrumentation. The bundle budget exists and is real. |
| B4 | Two `pytest` runs destroy each other | **KEPT** — the suite drops and recreates one shared database at session start. Give each run its own. |

## C — speed

| id | item | status |
|----|------|--------|
| C1 | The transactions page filters, sorts and counts in the browser over the whole ledger | **KEPT** — blocked on the resolved values existing as columns. Materialise a resolved-category projection, recomputed when interpretation changes, then move filter/sort/count server-side and retire the page-until-exhausted loop. Largest remaining piece. |
| C2 | The distinct-months list is derived client-side | **KEPT** — cheap and exact in SQL (measured 64 ms). Ship standalone rather than waiting on C1. |
| C3 | `trades`' `GET /ledger/export` is unbounded | **KEPT** — its accounting twin is paged and clamped, and `http-api-contract.md` claims both are. The one bounded-reads asymmetry left between the ledgers. |
| C4 | `GET /api/lots` is unbounded | **KEPT** — closed lots are FIFO-matched by replaying the whole trades ledger, so a `LIMIT` would bound the payload and not the work. Persist matched lots first. |
| C5 | Dashboards read full history inside their date window | **DROPPED** — owner's call. |

## D — reliability

| id | item | status |
|----|------|--------|
| D1 | First-login lockout | **KEPT** — the internal account row is only created by Clerk's `user.created` webhook, so a valid session with no row is rejected, permanently if the webhook is lost. Provision on first authenticated request. |
| D2 | Sync blocks the request and tracks progress per-process | **KEPT** — make the sync a durable record that can be polled, with a background runner. |
| D3 | Transactions commit inside route handlers | **DROPPED** — 30 `commit()` calls across 11 router modules, but the failure it guards against is now rare (the whole-store version check that used to trigger it routinely is gone) and recoverable. Fixing it means auditing every handler that catches an error, and changing sync's partial-success semantics. Revisit only after D2, which removes that blocker for free. |

## E — documentation that describes code that no longer exists

Every one of these was made stale by the refactor itself.

| id | item | status |
|----|------|--------|
| E1 | `web/README.md` — says the linter is barely configured and the test framework is not installed; both were done. Also points at the wrong module for route loading. | **KEPT** |
| E2 | `src/db/README.md` — documents four `users` columns as vestigial (three do not exist; the survivor is a live soft-delete marker), and its wipe-and-reinsert table list is wrong in three ways and cites a retired endpoint. | **KEPT** |
| E3 | `docs/trades/ibkr_flex_api.md` — says broker credentials come from `.env`; they are per-user encrypted rows. | **KEPT** |
| E4 | `docs/accounting/architecture.md` and `categorization.md` — say a transfer rule may also set a category; a CHECK constraint forbids it. | **KEPT** |
| E5 | `docs/accounting/category-tag-merging.md` — documents a deleted column and a deleted whole-table write path, for the riskiest operation in the app. | **KEPT** |
| E6 | Assorted — `architecture.md` names a module that is a package, four `trades` docs name functions that do not exist, `README.md` under-reports the broken notebooks, stale line-number citations throughout. | **KEPT** |
| E7 | `SCHEMA.md` — 97 KB, hand-maintained, no generator and no drift check, still framed as a feature-branch artefact. | **KEPT** |

## F — artefacts outside the repo

| id | item | status |
|----|------|--------|
| F1 | The owner-seed script fix is gitignored | **DROPPED** — owner's call. |
| F2 | Audit annotations are gitignored | **DROPPED** — owner's call. |
| F3 | The performance harness and its seeded database live in a temp directory | **DROPPED** — owner's call. |
| F4 | The concurrency-policy document is gitignored, and four tracked files cite it as definitive | **KEPT** — move it into tracked `docs/`. |

## G — hygiene

| id | item | status |
|----|------|--------|
| G1 | Docker builds the frontend on Node 20; CI uses Node 22, and nothing pins either — so the bundle budget measures a toolchain that is not the one shipped | **KEPT** |
| G2 | `uv` is pulled from an unpinned tag in a Dockerfile that otherwise insists on `--locked` | **KEPT** |
| G3 | Three dead helpers: `ledger.frame.empty_ledger_frame`, `repositories.interpretation.clear_rule_exclusions`, `repositories.interpretation.replace_transfer_links` | **KEPT** |
| G4 | Unused and duplicated dependencies, six `DB_*` env vars absent from `.env.example`, two undeclared imports | **KEPT** |
| G5 | `npm ci` fails on eight commits inside PR 4's history | **KEPT** — lowest value here; only a bisect hits it. |
| G6 | `known-gaps.md`'s own figures have drifted (gap 1 says 40 commits, it is 30; gap 10 omits a third budget) | **KEPT** |
| G7 | Automation reorder is drag-only, with no keyboard path | **DROPPED** — owner's call. |
| G8 | `docs/server-setup/versioning.md` is gitignored, and CI's version-bump gate enforces the policy it holds | **KEPT** — same shape as F4: the rule a gate enforces should not live outside the repo. Move the policy into tracked `docs/`. |

## H — operations

| id | item | status |
|----|------|--------|
| H1 | Staging runs `v1.3.0` | **DROPPED** — owner's call. |
| H2 | `gduguey/staging-latest` points at pre-refactor code | **DROPPED** — owner's call. |
