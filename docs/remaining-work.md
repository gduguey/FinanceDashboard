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
which rule applied. (The policy itself is at `docs/versioning.md`.)

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
| A2 | `docs/accounting/adding-accounts.md` teaches `str(amount)` hashing | **DONE** (PR C) — both code blocks now hash `f"{row.amount:.4f}"` and declare the row model's `amount` as `Money`, carrying `importers.chase.checking`'s own explanation of why. `row_hash`'s bullet says it too, so copying the helper list rather than the snippet still gets it. |
| A3a | "Unallocated money" ignores opening balances | **DONE** (PR B) — `dashboard.goals.unallocated_balance` adds every real account's opening balance, signed and summed the way `dashboard.net_worth` already treats those rows, contributing nothing before the balance's own `as_of_date`. Opening balances are their own table and never become postings, so nothing is double-counted. |
| A3b | Historical FX converted at today's rate | **DONE** (PR B) — `exchange_rates.smoothed_rate_series` evaluates the same trailing 30-day mean on every calendar day, and `ledger.currency.with_converted_amount` joins each row to its own date's rate. Goal contributions and the unallocated residual moved with it: they are cumulative flows with the identical defect. A row outside the cached two years clamps to the nearest end rather than converting at a null rate that `sum` would silently skip. `GET /income-statement/category-totals` joined the latency gate; the join did not separate from run-to-run noise at 10k. |
| A3c | Card refund counts as income | **DROPPED** — not a gap. `dashboard.income_statement.real_income_expense_legs` already excludes any transaction marked a transfer, manually or by a `TransferRule`. Resolving a refund is the general mechanism working as designed; hardcoding "positive amount on a liability account is not income" would be per-case special-casing. |
| A3d | `signColor` paints exactly zero green | **DONE** (PR B) — zero and `-0` both return the neutral colour. All twenty call sites audited: the four that negate their argument do so because growth is the bad direction there, and none relied on zero being green. The three inline `>= 0 ? '+'` prefixes moved with it. |
| A3e | "Alpha vs HYSA" labels two different quantities, neither of which is alpha | **DONE** (PR B) — now `excess_value_vs_hysa_usd` and `excess_return_vs_hysa_pct`, renamed through the API and the generated client so the word survives nowhere. The `0.04` moved nowhere and needed no new key: it is already `raw_hysa_rate_lookup`'s fallback for days the chosen bank published nothing, and the closed-lot figure now reads that lookup — the user's own bank, override and tax setting — instead of the constant. Its docstring, which still called it a placeholder for an unimplemented module, was the stale part. |
| A3f | The contribution gate silently dropped every non-USD row | **DONE** (PR B) — found while closing A3a. Three of the four callers of `unallocated_balance` passed no `DisplayCurrency`, so the default `{"USD": 1.0}` left-joined a EUR posting or contribution to a null rate, nulled its amount, and `sum` skipped it: the figure refusing a contribution disagreed with the one `GET /goals/summary` displayed. `all_goal_balances` in the withdrawal path had the same defect against the shortfall it is compared with. |
| A4a | Automation reorder can lose a concurrent write | **DROPPED** — one user, two tabs, a deliberate drag in both at once. The consequence is a reorder that visibly does not stick, and is retried. Not worth a lock. |
| A4b | Target-allocation save can lose a concurrent write | **KEPT** — a database-side `jsonb` merge, **not** a version column: `trades.db.models` deliberately documents this row as last-write-wins. `application/merge-patch+json` promises that patching two different symbols composes; today it does not. |
| A5 | Nothing tells a user their oldest figures were converted at a clamped rate | **KEPT** — raised by PR B, deliberately not built there. The rate cache holds two years, so any posting older than that converts at the oldest trailing mean on file (`ledger.currency.with_converted_amount`), which is documented in `currency-handling.md` and invisible on screen. Every other approximation in the app is labelled; this one should be too, probably as a note on an income statement whose window starts before the cache does. The alternative — widening the fetch window — is a data-acquisition change with its own cost and failure modes and would be its own item. |
| A6 | A recurring addition funds in one currency and is stored labelled another | **KEPT** — raised by CodeRabbit on PR B, pre-existing and deliberately not fixed there. `ledger.goal_automations.run_recurring_additions` takes the unallocated pool as a bare float (USD, see A3f) and returns bare floats; `api.routers.goals.post_run_recurring_additions` then persists each one with `currency=automation.currency or "USD"`. A `percent`/`remainder` rule on a EUR automation therefore stores a USD-derived amount labelled EUR, and every later conversion compounds the error. Fixing it is a decision about what currency the pool is denominated in, not a patch: either convert each funded amount into the automation's currency on the way out, or refuse a non-base-currency automation. |
| A7 | `with_converted_amount` turns an unknown currency into a null amount | **KEPT** — raised by CodeRabbit on PR B. Both the scalar and the per-date paths left-join their rate table, so a row whose currency is in neither becomes a null amount that every `sum` then skips — the same silent-drop shape A3f existed to remove. It cannot happen through the API today, because the rate table is built from `_currencies_in_use`, which is derived from the very rows being converted; nothing in the function enforces that, and a future caller assembling its own frame would not inherit it. A loud failure needs a `collect()`, which would break the laziness every caller composes on, so this wants either a cheap eager guard at the `DisplayCurrency` boundary or a schema-level guarantee. |
| A8 | A trade and a cash transaction could silently collide on `event_id` | **DONE** (PR C) — found while checking E6's claim that `ibkr_flex_api.md` documented the wrong cash dedup key. The doc said `ibkr:cash:{transactionID}` and `preprocessing._standardize_ibkr_cash_transactions` emitted `ibkr:{transactionID}`, the same shape a `<Trade>` gets — so the app relied on an undocumented IBKR guarantee that the two namespaces never coincide. `main._merge_ledger` dedupes with `.unique(subset="event_id", keep="last")`, so a collision would have dropped one of the two events with no error. The code moved to the doc's key rather than the doc to the code, restoring the convention `ibkr:{transactionID}:fee` already followed. |

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
| E1 | `web/README.md` — says the linter is barely configured and the test framework is not installed; both were done. Also points at the wrong module for route loading. | **DONE** (PR C) — the lint half did **not** hold: PR A had already rewritten that section and it matches `web/biome.json`, so it was left alone. What was stale: `jsdom` and three Testing Library packages are installed and `vitest.config.ts` sets `environment: 'jsdom'` for every file, and `routeTable.tsx` scans `src/routes/`, not `App.tsx`. `web/src/lib/routing.ts`'s comment made the same attribution. |
| E2 | `src/db/README.md` — documents four `users` columns as vestigial (three do not exist; the survivor is a live soft-delete marker), and its wipe-and-reinsert table list is wrong in three ways and cites a retired endpoint. | **DONE** (PR C) — recounted from the code: **eight** tables are wiped, not fifteen (the section's own bullets summed to thirteen), plus `goal_automations` scoped to one `direction`. `goals`, `goal_contributions`, `transfer_links` and `transfer_linked_transactions` are not in the set and are written per row. Six of the seven `PUT` routes it cited are retired, so entries now name their `replace_*` function. Two further claims were false in the other direction: `repositories.ledger` and `repositories.accounts.insert_manual_transfers` do reach `transactions`/`postings`, and `webhooks._deactivate_user` does handle `user.deleted`. |
| E3 | `docs/trades/ibkr_flex_api.md` — says broker credentials come from `.env`; they are per-user encrypted rows. | **DONE** (PR C) — rewritten around the three real layers (`db.secrets`, `trades.broker_credentials`, `trades.brokers.ibkr.credentials`). `IbkrFlexCredentials`' own docstring pointed `resolve_ibkr_credentials` at the broker-agnostic module rather than the IBKR one, and was corrected with it. |
| E4 | `docs/accounting/architecture.md` and `categorization.md` — say a transfer rule may also set a category; a CHECK constraint forbids it. | **DONE** (PR C) — three sites, not two: `GuidePage.tsx` told a user the same thing in the app. |
| E5 | `docs/accounting/category-tag-merging.md` — documents a deleted column and a deleted whole-table write path, for the riskiest operation in the app. | **DONE** (PR C) — worse than recorded: it attributed the tag-merge repointing to `load_overrides`/`save_overrides`, and `repositories.taxonomy.remap_tag_ids` does it in direct SQL over `posting_tags` and `posting_override_tags` with no override load or save in the path. `load_overrides` was **not** deleted and is still what both category handlers call; only the write half moved to the scoped `save_overrides_for_postings`. All fourteen line-number citations were deleted rather than repaired. |
| E6 | Assorted — `architecture.md` names a module that is a package, four `trades` docs name functions that do not exist, `README.md` under-reports the broken notebooks, stale line-number citations throughout. | **DONE** (PR C) — "four `trades` docs" was two. `decision_counterfactual_value` has no real counterpart and its row was deleted; `update_adjusted_price_cache` is `refresh_adjusted_price_history`, which is deliberately *not* incremental, so the doc had the behaviour backwards too. `docs/architecture.md`'s "exactly 11 local imports" is 19, in three categories rather than two. Two source files cited `DATABASE_SCHEMA.md`, a filename this repo has never had. The IBKR cash dedup key was a real defect, not a doc error — see A8. |
| E7 | `SCHEMA.md` — 97 KB, hand-maintained, no generator and no drift check, still framed as a feature-branch artefact. | **DONE** (PR C) — re-headed as a standing reference and moved to `docs/schema.md`, with the rewrite narrative split into `docs/archive/schema-rewrite.md`. Generating it was declined: a generator covers §3's inventory but not §4's rationale or §3's *Notes* column, which is where the document's value is, so it would trade the useful half for the mechanical one. Its "Known gaps" section was re-verified line by line — it promised "PR 2/3/5 closes it" for work shipped in v1.3.0–v1.7.0 and described `tests/db/test_rls_isolation.py` as unwritten when it is a required check. The drift gate is E8. |
| E8 | Nothing checks `docs/schema.md` against the models | **KEPT** — raised by PR C while closing E7. `alembic check` gates *models versus database* on every PR; nothing gates *document versus either*, and `git log` shows three commits on the file across the whole post-rewrite history. Cheapest useful gate: a test that parses §3's `#### \`schema.table\`` headings and asserts the set equals `db.base.Base.metadata.tables`, plus the table counts in §1. That catches a table added, removed or renamed without the document moving — the drift that actually bites — and deliberately not a stale sentence, which no parser can see. |

## F — artefacts outside the repo

| id | item | status |
|----|------|--------|
| F1 | The owner-seed script fix is gitignored | **DROPPED** — owner's call. |
| F2 | Audit annotations are gitignored | **DROPPED** — owner's call. |
| F3 | The performance harness and its seeded database live in a temp directory | **DROPPED** — owner's call. |
| F4 | The concurrency-policy document is gitignored, and four tracked files cite it as definitive | **DONE** (PR C) — nine tracked citations, not four, two of them docstrings that reach the SPA through the generated client. Now `docs/optimistic-concurrency-versioning.md`. |

## G — hygiene

| id | item | status |
|----|------|--------|
| G1 | Docker builds the frontend on Node 20; CI uses Node 22, and nothing pins either — so the bundle budget measures a toolchain that is not the one shipped | **DONE** (PR C) — the premise was wrong: `web/tooling/budget.ts` runs inside `npm run build`, so the budget was enforced in both places. The real defects were that Node 20 is end-of-life and that the recorded numbers came from 22. Now `node:22-bookworm-slim`, which also collapses the musl/glibc divergence, with `.nvmrc` as the single declaration that `engines` and both workflows follow. Measured after: 196,749 B of the 200,000 B landing budget. |
| G2 | `uv` is pulled from an unpinned tag in a Dockerfile that otherwise insists on `--locked` | **DONE** (PR C) — pinned to `ghcr.io/astral-sh/uv:0.8.22`. |
| G3 | Three dead helpers: `ledger.frame.empty_ledger_frame`, `repositories.interpretation.clear_rule_exclusions`, `repositories.interpretation.replace_transfer_links` | **DONE** (PR C) — all three verified as definition-only across `src/`, `tests/`, `notebooks/`, `web/` and `docs/`, with no `__all__` entry and no dynamic reference, and deleted. Two more of the same shape were found and deliberately not touched — see G9. |
| G4 | Unused and duplicated dependencies, six `DB_*` env vars absent from `.env.example`, two undeclared imports | **DONE** (PR C) — `starlette` and `botocore` declared, the duplicate `mypy` collapsed, `plotly` removed with its mypy override, and exactly six `DB_*` variables added to `.env.example`. `python-dotenv` and `nbclient` are **not** removable packages: `pydantic-settings` requires the first and twelve `BaseSettings` classes use `env_file`, and `jupyter` reaches the second through `nbconvert`. Only their redundant direct declarations went. |
| G5 | `npm ci` fails on eight commits inside PR 4's history | **DROPPED** (PR C) — ten commits, not eight, and the cause is pruned optional lock entries (`@emnapi/core`, `@emnapi/runtime`, `node-gyp-build`) rather than a `package.json`/lockfile desync. Fixing it means rewriting merged mainline history for a bisect ergonomics problem. No guard added either: CI already runs `npm ci` on every PR head, and intermediate-commit breakage is inherent to PR-level CI rather than a hole worth new machinery for. |
| G6 | `known-gaps.md`'s own figures have drifted (gap 1 says 40 commits, it is 30; gap 10 omits a third budget) | **DONE** (PR A) — gap 1 corrected to 30, gap 10 now lists `ROUTE_BUDGET` and carries the current landing headroom, and gaps 8 and 10 are marked closed. |
| G7 | Automation reorder is drag-only, with no keyboard path | **DROPPED** — owner's call. |
| G8 | `docs/server-setup/versioning.md` is gitignored, and CI's version-bump gate enforces the policy it holds | **DONE** (PR C) — now `docs/versioning.md`, with its link to the still-gitignored `docker.md` replaced by the tracked paths it describes. |
| G9 | Two more helpers are dead in production but load-bearing for tests | **KEPT** — raised by PR C while closing G3, and deliberately left there. `repositories.interpretation.save_overrides` (13 test call sites) and `replace_rule_exclusions` (5) have no production caller: both are whole-table writes that the scoped `save_overrides_for_postings` and `_sync_rule_exclusions` replaced. Deleting them means rewriting those tests onto the scoped paths — which is the point, because until that happens those suites exercise a write path production never takes, and a test that covers dead code proves nothing about live code. That is a testing-validity question, not a dead-code sweep. |

## H — operations

| id | item | status |
|----|------|--------|
| H1 | Staging runs `v1.3.0` | **DROPPED** — owner's call. |
| H2 | `gduguey/staging-latest` points at pre-refactor code | **DROPPED** — owner's call. |
