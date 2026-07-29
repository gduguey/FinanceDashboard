# Known gaps / possible future work

Real issues we've consciously **deferred** rather than fixed — recorded here so
they aren't lost. Neither is urgent: the app is pre-launch with only test data,
and both deserve their own focused, tested change rather than being bundled into
a larger PR. Surfaced during the CodeRabbit review sweep of the decomposed
branches.

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

## 2. First-login lockout if the Clerk `user.created` webhook is slow or lost

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
