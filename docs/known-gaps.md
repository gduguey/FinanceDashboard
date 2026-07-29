# Known gaps / possible future work

Real issues we've consciously **deferred** rather than fixed — recorded here so
they aren't lost. Neither is urgent: the app is pre-launch with only test data,
and both deserve their own focused, tested change rather than being bundled into
a larger PR. Surfaced during the CodeRabbit review sweep of the decomposed
branches.

## 1. Category delete/rename still commits its side-effect writes as it goes

**Where:** `delete_category` / `post_category_rename` in
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

**Fix direction:** run the whole handler in one transaction, committing once at
the end, instead of letting each repository call commit for itself. That means
giving the repositories a "flush, don't commit" contract everywhere (several
already have it) and moving the commit up to the router.

**Why deferred:** it's a cross-cutting change to every repository's transaction
contract, not something to bundle into an aggregate extraction, and it needs its
own failure-injection tests to be worth anything.

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
