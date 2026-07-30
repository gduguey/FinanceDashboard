# Known gaps / possible future work

Real issues we've consciously **deferred** rather than fixed — recorded here so
they aren't lost. None is urgent: the app is pre-launch with only test data, and
each deserves its own focused, tested change rather than being bundled into a
larger PR. Each entry says where the problem is, what it is, the intended fix,
and why it was not taken in the PR that found it.

## 1. The transaction boundary lives in the HTTP layer

**Where:** 40 `session.commit()` calls in route bodies across
`src/accounting/api/routers/` (11 of the 15 modules). Worst-case handlers:
`delete_category` / `post_category_rename` in
`src/accounting/api/routers/categories.py`.

**What (originally):** these clear the deleted/renamed category off every posting
(and rewrite the affected overrides) *before* `save_store` ran the whole-store
version check, so a conflict at that check left the postings already
uncategorized/remapped with the category-list save rejected.

**Status:** the version-conflict half of this is gone — `save_store` went when
the accounts and taxonomy aggregates moved into `src/accounting/repositories/`,
and its whole-store counter (`accounting.store_versions`) has since been deleted
outright, so there is no late check left to reject a handler that has already
written. Optimistic concurrency is now per-row only; see
`docs/app-stack/optimistic-concurrency-versioning.md`. What remains is narrower:
each of these
handlers still spans several repository writes that commit as they go
(`replace_budgets`, `save_overrides_for_postings`, `retire_categories`,
then `replace_categories`), so a *failure* partway through — an unexpected
`IntegrityError`, a dropped connection — leaves the earlier ones committed.

**Fix direction:** commit once per request in `db.session.get_db`, so a handler
spans one transaction and no route body names a commit at all. That also removes
the `set_rls_user` re-arming hazard `get_db`'s own docstring warns about, which
only exists because a request can commit mid-flight.

**Why deferred (again, out of the API-contract PR):** two concrete blockers, not
just size.

1. `POST /api/v1/trades/sync` deliberately depends on mid-request commits for
   partial-success semantics: `sync_ibkr_account` commits per successful step and
   rolls back per failed one, then `src/trades/api/routers/sync.py:150` re-arms
   RLS and keeps reading. One commit at request end would turn a partly-successful
   sync into all-or-nothing — a behaviour change, not a refactor.
2. Committing at request end commits partial writes for any handler that catches
   an error and still returns normally (e.g. the per-item commits at
   `imports.py:527` and `postings.py:500`). Today those partial writes are rolled
   back by `session_scope`. Each such handler needs its own decision.

Neither is hard; both need failure-injection tests, and both are behavioural
rather than contractual — so this belongs in its own PR rather than one whose
subject is the HTTP contract.

## 2. `POST /api/v1/trades/sync` is synchronous, and its progress is per-process

**Where:** `sync` in `src/trades/api/routers/sync.py`;
`_report_sync_progress` in `src/trades/api/dependencies.py`.

**What:** the endpoint runs the whole IBKR pull plus the price/CPI/HYSA cache
refresh inside the request and answers 200 when it is done, so it advertises a
long-running job as an ordinary write. Two separate consequences:

1. **The 200 is honest today but does not scale.** A client cannot poll, cancel
   or resume; the only progress channel is `GET /sync/progress`.
2. **That progress channel is already wrong.** It reads
   `app.state.sync_progress`, an in-process dict. Under more than one uvicorn
   worker, the poll can land on a worker that never ran the sync and reports
   nothing, while the sync is running fine in another. This is a real bug, not
   only a scaling limit — it is latent purely because the current deployment
   runs a single worker.

**Fix direction:** make the run a resource. `POST /api/v1/trades/sync-runs`
answers `202 Accepted` with a `Location` pointing at
`/api/v1/trades/sync-runs/{run_id}`, which reports the run's step, percent and
terminal state; a background runner does the work. Progress then lives in a
`sync_runs` table, which fixes (2) as a side effect — any worker can read it.

**Why deferred out of the API-contract PR:** an honest `202` needs a durable
job resource, a migration for it, and a background runner — none of which is a
contract change, and shipping `202` without them would replace a truthful 200
with a lie. Note the `303 See Other` originally proposed alongside this is
**dropped permanently**, not deferred: there is no created resource to see, and
`fetch()` follows redirects invisibly, so the SPA could not distinguish it from
the 200 it already gets.

## 3. First-login lockout if the Clerk `user.created` webhook is slow or lost

**Where:** `resolve_current_user_id` in `src/trades/api/auth.py`; user
provisioning in `src/trades/api/webhooks.py`.

**What:** A user's internal account row is created only when Clerk's
`user.created` webhook arrives. Until then, a validly-signed-in user gets a 401
("no account found for this session yet"). A delayed or lost webhook locks them
out even though nothing is actually wrong. It fails *closed* — no security hole —
but it's a reliability gap.

**Fix direction:** just-in-time provisioning — when `resolve_current_user_id`
sees a valid Clerk session with no linked row, create the row on the spot instead
of waiting for the webhook. It needs the user's email, which isn't in the session
token, so it requires a Clerk Backend API call (`clerk_backend_api`) from inside
the auth path.

**Why deferred:** it adds a live external call to the most security-sensitive code
path (every request's auth check), so it deserves careful, isolated implementation
and tests — not a quick bolt-on.

## 4. Two read-modify-write paths can lose a concurrent write

**Where:** `_reorder_automations` in `src/accounting/api/routers/goals.py`
(behind `PUT /goal-automations/{contributions,withdrawals}/order`);
`patch_target_allocation` in `src/trades/api/routers/settings.py`.

**What:** both read a whole collection, compute the new state in Python, and
write the whole thing back, with nothing detecting that the state changed in
between.

1. `_reorder_automations` reads the direction's automations, then
   `replace_goal_automations` deletes every row for that direction and
   reinserts the ones built from that snapshot. A `POST
   /goal-automations/{direction}` or `PATCH /goal-automations/{automation_id}`
   committing between the read and the delete is deleted and replaced by the
   stale version — a lost create or lost update, with no 409 and no error to
   either caller. Unlike goals (`expected_version`) and budgets (an atomic
   `ON CONFLICT ... RETURNING`), automations carry no per-row version at all.
2. `patch_target_allocation` merges the request's `symbol -> percentage` patch
   into the map it just read and calls `save_settings`, which rewrites the
   whole row. Two patches naming *different* symbols therefore do not compose:
   the later commit drops the earlier one, which contradicts the
   non-clobbering merge-patch semantics the endpoint's own media type
   promises.

**Fix direction:** for (1), either `UPDATE ... SET priority = :priority WHERE
natural_key = :id` per already-validated row — no delete, nothing to lose — or
`SELECT ... FOR UPDATE` on the direction's rows before the delete so a
concurrent write is detected rather than overwritten. For (2), a row lock or a
database-side JSON merge, so the read and the write are one statement.

**Why deferred out of the API-contract PR:** both are behavioural rather than
contractual — the methods, statuses and bodies are right and stay right after
the fix — and both need failure-injection tests that interleave two requests,
which is not a shape this suite has yet. (1) also wants a decision on whether
automations should carry a `version` like every other editable row, which is a
schema change with a migration. They belong with gap 1, whose fix is the same
subject: making one request one transaction.

## 5. Drag-to-reorder fires one request per hover — FIXED

**Where:** `useRowDrag` in `web/src/components/goals/GoalAutomationsPanel.tsx`,
used by both `RecurringAdditionsList` and `WithdrawalPrioritiesList`.

**What it was:** `onDragOver` recomputed the order and called the reorder
mutation on every hover event, so dragging a row down a list of ten sent up to
nine requests whose responses could resolve out of order and leave an
intermediate order persisted.

**Fixed in PR 4.** The dragged order is held in local component state, rendered
from there, and sent once from `onDragEnd`. The local copy is kept — not
cleared at drop — until the server's own order agrees with it, so the rows do
not snap back and forth while the request is in flight. The ordering itself is
`moveItem`/`sameOrder` in `web/src/lib/reorder.ts`, unit-tested. Nothing about
the endpoint changed.

Note the original entry also said this "breaks the rule that no two
accounting-store mutations may be in flight at once". **There is no such rule
any more.** PR 1 deleted the shared `X-Expected-Store-Version` header and moved
to per-row versioning, so concurrent mutations no longer race on shared state —
see `docs/app-stack/optimistic-concurrency-versioning.md`. PR 4 audited all
eleven components that await a mutation and found every remaining case to be a
genuine data dependency (create-then-set-opening-balance) or a deliberate
rate limit (the LLM categorization loop), not a workaround for that rule.

## 6. The transactions table filters, sorts and counts in the browser, over the whole ledger

**Where:** `web/src/lib/accountingApi.ts`'s `postings()`, which pages
`GET /postings` until the collection is exhausted, and
`web/src/lib/transactionFilters.ts`, which applies the thirteen predicates and
the count to whatever it returns.

**What:** the screen is O(total history) rather than O(what is shown). On the
`finance_speed` scratch database (170k transactions / 340k postings / 70k
overrides) `postings()` is 34 sequential requests at ~1,750 ms each — about
**59 s** before the first row renders. PR 4 scoped that cost to the one screen
that needs it (the sidebar's onboarding check now asks for a one-row page, 67
ms, and no write outside the Transactions page refetches the list any more),
and made the client-side half cheap — one pass per posting instead of thirteen,
a debounced search, and a virtualized table — but the fetch itself is unchanged.

**Intended fix:** send the filter, the sort and the page to the server, and
return the page plus two counts (transactions for the window, postings for the
figure beside the table).

**Why not taken in PR 4, which owned it.** It was attempted and the blocker is
structural, not a matter of effort. The thirteen predicates read *resolved*
values — the category a redirect or an override rewrote, the account a rule
repointed, the description a merge rewrote, the amount a split changed, the
`is_linked_transfer` a link set. Resolution is the Polars overlay pipeline in
`accounting.precedence`/`accounting.ledger.*`, and `visible_transaction_page`
already documents why filtering or ordering on a pre-pipeline column selects a
different set than a client sees. That leaves two ways to filter server-side,
and both were measured or costed:

- **Mirror the pipeline in SQL.** Rule matching alone
  (`ledger.categorization.rule_matches_by_transaction`: eligibility by leg
  count, substring match per active rule, exclusions, lowest-priority-wins)
  is a second implementation of the subtlest part of the app, which would then
  have to agree with the first forever. The refactor's own standard — a
  translation that is 95% right will look right — argues against it.
- **Resolve the whole ledger per request and filter in Polars.** Measured on
  `finance_speed`: `_resolve_postings` unpaged is **12.4 s** (6.7 s
  `load_ledger`, 2.4 s `load_overrides`, 2.4 s the override stage). Better than
  59 s and nowhere near a page load.

The same wall blocks the two aggregates the screen derives. Distinct months is
cheap and exact in SQL (**64 ms**, `posted_at` is the one column no overlay
rewrites) and could land on its own; the needs-categorizing count is a resolved
predicate and cannot.

**Fix direction:** materialize the resolved projection — a table or matview of
the resolved posting rows, maintained when an overlay changes — so SQL can
filter and count the values the client actually sees, with one implementation
of resolution rather than two. That is a database change with its own
migration, backfill and invalidation design, which is why it is its own PR
rather than the tail of a frontend one. Filter-shaped bulk actions
(`validate-pending`, `pattern-suggest-category/bulk` resolving a filter instead
of a list of ids) depend on it and are deferred with it.

## 7. Reordering an automation is drag-only, so a keyboard cannot do it

The two reorderable lists in `web/src/components/goals/GoalAutomationsPanel.tsx`
— "Recurring additions" and "Withdrawal priority" — are driven entirely by
`draggable` plus `onDragStart`/`onDragOver`/`onDragEnd`. There is no move-up /
move-down control and no keyboard path to the operation, in this file or any
other: `onDragStart` appears nowhere else in `web/src`.

PR 5 turned Biome's accessibility rules on, and this is the one place where the
rule pointed at something a lint fix cannot close. The rows are now `<ol>`/`<li>`
rather than `<div>`s, which is a real improvement — the order *is* the meaning
in both lists, and a screen reader now announces the position of each row — but
it does not make the reorder reachable. Nor is there a truthful ARIA role that
would: `aria-grabbed` and `aria-dropeffect` were deprecated and removed in ARIA
1.2, so nothing expresses "draggable region" any more.

`role="none"` would have made the rule pass. It was declined: it asserts the
interaction is presentational, and here the drag is the *only* means of
performing a real operation, so it would have been a suppression wearing an
attribute's clothes.

**Fix direction:** move-up / move-down buttons beside the existing
`GripVertical` handle, reusing `lib/reorder.ts`'s `moveItem` — the same function
`onDragEnd` already calls, so the two paths cannot disagree. That is a UI change
with its own design question (whether the buttons are always visible or appear
on focus), which is why it is not bundled into a lint-gate PR.

## 8. Two `pytest` runs against the same database destroy each other

`tests/conftest.py`'s session-scoped `_db_engine` opens `DATABASE_URL_TEST`
and, before yielding, runs `DROP SCHEMA IF EXISTS accounting CASCADE`,
the same for `trades`, then `Base.metadata.drop_all` / `create_all`. That is
correct for one run and destructive for two: a second session starting while
the first is mid-suite drops the tables the first is still using, and the
first then fails in whatever test happens to be executing.

The failures do not look like a fixture problem. They surface as a scattered
handful of unrelated assertion errors and `ProgrammingError`s — a different
set each time, in whichever tests were in flight — so they read as a real
regression in whatever change is being tested. This cost real time during
PR 5: two concurrent runs produced first "5 failed, 4 errors" and then
"4 failed, 7 errors" on a **different** set including a trades
statements-export test, and both were artefacts. CI was green on the same
commit throughout, because CI runs one suite against its own container.

Nothing warns about it. There is no lock, and no check that the database is
not already in use. `tests/db/test_rls_coverage.py` and
`test_rls_isolation.py` are immune by construction — `tests/db/conftest.py`
gives them a uuid-suffixed scratch database each — which is the shape the fix
should take.

**Fix direction:** derive the database name per run rather than taking it
verbatim, the way the RLS conftest already does — suffix
`DATABASE_URL_TEST`'s database with `os.environ.get("PYTEST_XDIST_WORKER",
"")` plus a per-session token, create it, migrate or `create_all` into it,
and drop it at the end. That fixes the human-runs-two-terminals case and the
`pytest-xdist` case with one mechanism. A cheaper stopgap is a
`pg_advisory_lock` taken in `_db_engine` so the second run blocks instead of
corrupting, but that serialises rather than parallelises, and it still needs
the lock to be released on a crashed run.

## 9. Five numbers on screen do not mean what their labels say

Measured against current code during PR 5 and deferred whole, because these
are user-visible semantics and deserve review attention a lint PR would
swamp. Each still reproduces unless noted.

**Unallocated money conflates a cumulative flow with available cash.**
`dashboard/goals.py:157` computes `net_income - float(total_contributed)`,
where `net_income_expense_total` sums every real income/expense leg from the
start of the ledger (`income_statement.py:280-311`). It ignores balances,
opening balances and transfers, so it is a lifetime flow, not money you have.
It is presented as spendable in `FinancialHealthStrip.tsx:107-112`
("Not yet assigned to any goal") and `GoalsPage.tsx:337-344`, and it *gates a
write* at `api/routers/goals.py:891-892`.

**Historical foreign currency is converted at one rate for all history.**
Narrower than first stated: net worth already threads an as-of date
(`routers/dashboard.py:205,271,319`) and so does goals (`goals.py:680`). Only
the income statement, budgets and the spend curve pass none
(`routers/dashboard.py:366,394`, `routers/budgets.py:140,182`), so
`dependencies.py:426` defaults them to today. Note the app never converts at a
*spot* rate at all: `market_data/exchange_rates.py:111` returns a trailing
30-day mean, deliberately, to suppress single-day noise. **Decided fix:**
evaluate that same mean as of each posting's own date — not the report date,
and not spot. Amend `docs/accounting/currency-handling.md` in the same change
so the rationale survives and the change is not later mistaken for a
regression back to spot rates. This one has a performance cost (a per-row rate
join replacing a scalar multiply) and should land after a latency gate exists.

**A card refund reads as income.** `income_statement.py:272-273` splits legs
on `amount >= 0` with no account-kind test; `credit_card` is known only to
`net_worth.py:34`. A refund to a credit card is therefore counted as income,
which also inflates unallocated money above.

**"A shortfall shows a green +" does not reproduce** and is not part of this
gap. The Sankey shortfall is deliberately grey and dashed
(`CashflowSankeyChart.tsx:92,130-134`), negative unallocated is rose, and
budget overspend is `text-destructive`. The real adjacent defect is
`web/src/lib/format.ts:95-98`: `signColor` returns emerald for `value >= 0`,
so exactly zero reads as a gain, and every expense-like caller compensates by
negating its argument (`signColor(-swing.delta)`). Fixing the helper means
auditing those call sites, not just the helper.

**The Overview hero cards have no tooltips.** `OverviewPage.tsx` imports none,
and `FinancialHealthStrip`'s local `StatCard` (61-83) has no slot for one.
`components/ui/info-tooltip.tsx` and `lib/glossary.ts` already exist and are
used under `components/investments/` and `shared/ChartCard.tsx` — the whole
money side of the app has none.

**"Alpha vs HYSA" labels two different quantities, neither of which is
alpha.** `trades/dashboard/overview.py:158` is `value - hysa_value`, a dollar
difference in terminal values benchmarked against real published rates.
`trades/dashboard/holdings.py:59-61` is an excess return benchmarked against
`config.returns.hysa_annual_rate`, a flat `0.04` whose own docstring
(`config.py:213-214`) calls it a placeholder. The two are measured against
different rates under one label. `glossary.ts:55-58` already describes the
computation correctly — only the word "alpha" is wrong.

## 10. Nothing stops PR 2's and PR 4's performance gains from regressing

There is no performance job in any workflow. The only gate that exists is the
bundle budget (`web/tooling/budget.ts`, `LANDING_BUDGET = 200_000`,
`CHUNK_BUDGET = 90_000`), which runs inside `npm run build` and covers bytes
only — nothing measures latency, and nothing measures what the browser
actually experiences.

**Fix direction, in two parts.** A `PerformanceObserver` reporting INP, LCP
and CLS, which is the only way to see the interaction cost PR 4 spent itself
reducing. Note the landing budget currently has **3,053 B of headroom**
(196,947 of 200,000), so the shim either fits or the budget is raised in the
same commit with the reason in the message, as its own docstring sanctions.
And a latency gate over the paginated read paths, seeded at a CI-affordable
volume — roughly 10k transactions rather than the 170k of the `finance_speed`
scratch database, since the failure mode worth catching is a complexity
regression and that is visible at 10k. Document the chosen thresholds and
their derivation in the test file rather than as bare constants, for the same
reason the bundle budget documents its own: a number you can argue with beats
one that just gets raised.

## 11. A posting's identity embeds its description, so an enriched statement double-counts

`row_hash(account_id, posting_date, str(amount), description)` — documented in
`docs/accounting/adding-accounts.md` — is the natural key a re-imported row is
recognised by. Because `description` is an *input*, a bank that posts
`PENDING TESCO` and later enriches it to `TESCO STORES 1234` produces a
different key for the same real transaction, and it is imported twice. In a
money app that is a silent double-count.

PR 1 named this when it closed D3: sequential primary keys fix insert
locality but "do not fix orphaning on hash drift: that requires the natural
key to stop embedding the drifting description". One of the three drift
classes is already closed — PR 1 made all four importers hash
`f"{row.amount:.4f}"`, so amount *formatting* drift no longer re-mints a key.
Description drift and pending-to-posted drift remain.

`docs/accounting/canonical-csv-import.md` documents parsing thoroughly and
says nothing about identity at all, so there is no contract to test against
yet. Deciding it comes first.

**Fix direction:** a posting's identity is the bank-provided id where one
exists, falling back to account, date, amount and a sequence discriminator,
with description excluded. The discriminator is the hard part and the reason
this is not a small change: two genuinely distinct same-day, same-amount
transactions must not collapse into one, and losing a real transaction is
worse than duplicating one. Write the contract into
`canonical-csv-import.md`, then test it. This sits naturally alongside the
materialized resolved projection of gap 6, since both change how a posting is
identified and addressed.

## 12. Known gap 4's lost updates are still open after the savepoint work

PR 5 fixed `merge_by_natural_key`'s concurrent first insert (savepoint-scoped
retry, bounded by `NATURAL_KEY_MERGE_ATTEMPTS`), which was the same concurrency
theme, but did not reach the two read-modify-write paths of gap 4. They are
unchanged and their fix directions still stand, with one refinement recorded
during PR 5: `patch_target_allocation` should **not** be given a version
column. `trades/db/models.py:173-175` deliberately records that this one row is
last-write-wins, and a DB-side `jsonb` merge composes by construction — which
is what `application/merge-patch+json` actually promises, and a stronger
guarantee than versioning, since two PATCHes naming different symbols would
both survive rather than one winning.
