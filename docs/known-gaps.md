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

## 5. Drag-to-reorder fires one request per hover

**Where:** `useRowDrag` in `web/src/components/goals/GoalAutomationsPanel.tsx`,
used by both `RecurringAdditionsList` and `WithdrawalPrioritiesList`.

**What:** `onDragOver` recomputes the order and calls the reorder mutation on
every hover event, so dragging a row down a list of ten sends up to nine
requests whose responses can resolve out of order and leave an intermediate
order persisted. It also breaks the rule that no two accounting-store mutations
may be in flight at once.

**Fix direction:** hold the dragged order in local component state, render from
that, and send the ids once from `onDragEnd`.

**Why deferred:** the pattern predates this PR — it is unchanged on `main`,
which called the whole-list `PUT` from the same place — and the fix is local
component state plus an optimistic render, which is PR 4's subject (frontend
performance and optimistic updates). Nothing about the endpoint changes.
