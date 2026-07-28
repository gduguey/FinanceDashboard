# Known gaps / possible future work

Real issues we've consciously **deferred** rather than fixed — recorded here so
they aren't lost. Neither is urgent: the app is pre-launch with only test data,
and both deserve their own focused, tested change rather than being bundled into
a larger PR. Surfaced during the CodeRabbit review sweep of the decomposed
branches.

## 1. Category delete/rename can leave a half-applied state on a concurrent conflict

**Where:** `delete_category` / `post_category_rename` in
`src/accounting/api/routers/store.py`.

**What:** These clear the deleted/renamed category off every posting (and rewrite
the affected overrides) *before* `save_store` runs the whole-store version check.
Those side-effect writes commit as they go, so if the version check then fails
(someone else changed the store since the page loaded), the postings are already
uncategorized/remapped but the category-list save is rejected — an inconsistent
in-between state.

**Fix direction:** run the store-version check at the *start* of the handler,
before the side-effect writes, so a conflict aborts before anything is committed.
Fiddly because that check also bumps and re-stashes the version (see
`docs/app-stack/optimistic-concurrency-versioning.md`), so it needs its own tests
for the conflict path to make sure it doesn't reject saves that are actually fine.

**Why deferred:** narrow race (two writes touching the same store at the same
instant) and, pre-launch, there's no real concurrent load — too risky to bundle
with unrelated fixes.

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
