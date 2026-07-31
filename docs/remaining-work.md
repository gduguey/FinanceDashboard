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
| A3a | "Unallocated money" ignores opening balances | **DONE** (PR B) — `dashboard.goals.unallocated_balance` adds every real account's opening balance, signed and summed the way `dashboard.net_worth` already treats those rows, contributing nothing before the balance's own `as_of_date`. Opening balances are their own table and never become postings, so nothing is double-counted. |
| A3b | Historical FX converted at today's rate | **DONE** (PR B) — `exchange_rates.smoothed_rate_series` evaluates the same trailing 30-day mean on every calendar day, and `ledger.currency.with_converted_amount` joins each row to its own date's rate. Goal contributions and the unallocated residual moved with it: they are cumulative flows with the identical defect. A row outside the cached two years clamps to the nearest end rather than converting at a null rate that `sum` would silently skip. `GET /income-statement/category-totals` joined the latency gate; the join did not separate from run-to-run noise at 10k. |
| A3c | Card refund counts as income | **DROPPED** — not a gap. `dashboard.income_statement.real_income_expense_legs` already excludes any transaction marked a transfer, manually or by a `TransferRule`. Resolving a refund is the general mechanism working as designed; hardcoding "positive amount on a liability account is not income" would be per-case special-casing. |
| A3d | `signColor` paints exactly zero green | **DONE** (PR B) — zero and `-0` both return the neutral colour. All twenty call sites audited: the four that negate their argument do so because growth is the bad direction there, and none relied on zero being green. The three inline `>= 0 ? '+'` prefixes moved with it. |
| A3e | "Alpha vs HYSA" labels two different quantities, neither of which is alpha | **DONE** (PR B) — now `excess_value_vs_hysa_usd` and `excess_return_vs_hysa_pct`, renamed through the API and the generated client so the word survives nowhere. The `0.04` moved nowhere and needed no new key: it is already `raw_hysa_rate_lookup`'s fallback for days the chosen bank published nothing, and the closed-lot figure now reads that lookup — the user's own bank, override and tax setting — instead of the constant. Its docstring, which still called it a placeholder for an unimplemented module, was the stale part. |
| A3f | The contribution gate silently dropped every non-USD row | **DONE** (PR B) — found while closing A3a. Three of the four callers of `unallocated_balance` passed no `DisplayCurrency`, so the default `{"USD": 1.0}` left-joined a EUR posting or contribution to a null rate, nulled its amount, and `sum` skipped it: the figure refusing a contribution disagreed with the one `GET /goals/summary` displayed. `all_goal_balances` in the withdrawal path had the same defect against the shortfall it is compared with. |
| A4a | Automation reorder can lose a concurrent write | **DROPPED** — one user, two tabs, a deliberate drag in both at once. The consequence is a reorder that visibly does not stick, and is retried. Not worth a lock. |
| A4b | Target-allocation save can lose a concurrent write | **KEPT** — a database-side `jsonb` merge, **not** a version column: `trades.db.models` deliberately documents this row as last-write-wins. `application/merge-patch+json` promises that patching two different symbols composes; today it does not. |
| A5 | Nothing tells a user their oldest figures were converted at a clamped rate | **KEPT** — raised by PR B, deliberately not built there. The rate cache holds two years, so any posting older than that converts at the oldest trailing mean on file (`ledger.currency.with_converted_amount`), which is documented in `currency-handling.md` and invisible on screen. Every other approximation in the app is labelled; this one should be too, probably as a note on an income statement whose window starts before the cache does. The alternative — widening the fetch window — is a data-acquisition change with its own cost and failure modes and would be its own item. |

## B — guarantees that are not enforced

| id | item | status |
|----|------|--------|
| B1 | The tenant-isolation CI job is not a required check | **DONE** (PR A) — `Migrations apply and match the models` and `Read-path latency` added to the `protect-main` ruleset's required contexts; the tag job is `needs: [test, migrations, performance]`. |
| B2 | Lint warnings do not fail the build | **DONE** (PR A) — `lint` and `check` pass `--error-on-warnings`. Three of the four pins were redundant and are gone; `noUselessFragments` stays, because the preset gives it `info` and failing on warnings does not promote info. The intentional exception, `useComponentExportOnlyModules`, moved to `info` so it keeps a non-failing level. |
| B3 | Nothing protects the performance gains | **DONE, with the web-vitals half dropped** (PR A) — the latency gate over the paginated read paths ships as its own required job (`tests/performance/`). The `PerformanceObserver` shim was declined: no sink, no consumer, and it duplicates what DevTools and Lighthouse report natively for landing-path bytes. It returns as a new item only if a real destination exists. |
| B4 | Two `pytest` runs destroy each other | **DONE** (PR A) — every run creates and drops its own database, through the same `tests/support/scratch_db.py` the RLS suites now use. |

## C — speed

| id | item | status |
|----|------|--------|
| C1 | The transactions page filters, sorts and counts in the browser over the whole ledger | **KEPT** — blocked on the resolved values existing as columns. Materialise a resolved-category projection, recomputed when interpretation changes, then move filter/sort/count server-side and retire the page-until-exhausted loop. Largest remaining piece. |
| C2 | The distinct-months list is derived client-side | **KEPT** — cheap and exact in SQL (measured 64 ms). Ship standalone rather than waiting on C1. |
| C3 | `trades`' `GET /ledger/export` is unbounded | **KEPT** — its accounting twin is paged and clamped, and `http-api-contract.md` claims both are. The one bounded-reads asymmetry left between the ledgers. |
| C4 | `GET /api/lots` is unbounded | **KEPT** — closed lots are FIFO-matched by replaying the whole trades ledger, so a `LIMIT` would bound the payload and not the work. Persist matched lots first. |
| C5 | Dashboards read full history inside their date window | **DROPPED** — owner's call. |
| C6 | `GET /postings` falls off a cliff between `limit=300` and `limit=400` | **KEPT** — found by PR A's latency gate while calibrating it. On a 10k-transaction ledger, `limit=300` answers in 362 ms and `limit=400` exceeds the 15 s `statement_timeout` and returns a 500. `PAGE_LIMIT_MAX` is 5,000, so this is a legal request, and the discontinuity is far too sharp to be volume — it reads like a planner flip on the `transactions.id = ANY(:uuid[])` array in `ledger_statement`. The default page is 200, which is why nothing has hit it. Diagnose the plan first; the fix may be an index, a `LIMIT`-side rewrite, or lowering `PAGE_LIMIT_MAX` to a size the query can actually serve. Sits with C1, which rewrites this read path anyway. |

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
| G6 | `known-gaps.md`'s own figures have drifted (gap 1 says 40 commits, it is 30; gap 10 omits a third budget) | **DONE** (PR A) — gap 1 corrected to 30, gap 10 now lists `ROUTE_BUDGET` and carries the current landing headroom, and gaps 8 and 10 are marked closed. |
| G7 | Automation reorder is drag-only, with no keyboard path | **DROPPED** — owner's call. |
| G8 | `docs/server-setup/versioning.md` is gitignored, and CI's version-bump gate enforces the policy it holds | **KEPT** — same shape as F4: the rule a gate enforces should not live outside the repo. Move the policy into tracked `docs/`. |

## H — operations

| id | item | status |
|----|------|--------|
| H1 | Staging runs `v1.3.0` | **DROPPED** — owner's call. |
| H2 | `gduguey/staging-latest` points at pre-refactor code | **DROPPED** — owner's call. |
