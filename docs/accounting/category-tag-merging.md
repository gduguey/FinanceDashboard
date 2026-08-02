# Category and tag merging, and category deleting

What actually happens, table by table and column by column, when a
category or tag rename turns into a merge, or when a category is deleted.
Every step below is listed in the order the real code runs it — see
`docs/accounting/categorization.md` for what categories/tags are;
`src/db/README.md`'s "How a save actually writes to Postgres" for the
wipe-and-reinsert/upsert-and-prune background these mechanisms build on.

## Category rename → merge

`POST /categories/{category_id}/rename`
(`accounting.api.routers.categories.post_category_rename`).
Renaming "Dining" to an existing category's name, "Food & Drink", merges
the two. The matching rule itself lives in `taxonomy.plan_category_rename`
and isn't repeated here — a top-level category only ever merges
into another top-level category of the same `classification`; a
subcategory only ever merges into a sibling under the same
`parent_category_id`.

1. **Plan, in memory — nothing written yet.** `plan_category_rename`
   decides whether this rename collides with an existing, different
   category. If it does, it returns `id_remap = {"expense:dining":
   "expense:food-drink"}` and an updated `categories` dict with "Dining"
   already removed. Any of "Dining"'s own subcategories either merge into
   a same-named sibling already under "Food & Drink" or get reparented
   onto it (`parent_category_id` updated, same id kept).

2. **`GET /categories/{category_id}/rename-preview`** — before
   the real rename is ever called, the frontend calls this to find out
   whether it *would* merge. It runs step 1 (`plan_category_rename`) plus
   step 3 below (`remap_category_ids`) against the rows as they stand,
   without persisting anything, and diffs the budgets before and
   after to report exactly which entries would be discarded
   (`api_models.BudgetToDeletePreview`) — matched on the
   `(month, category_id, subcategory_id)` identity rather than on
   `budget_id`, which step 3 rebuilds. See step 3 for why some are
   discarded rather than repointed. The UI shows this in
   a confirmation dialog before the user commits to the real rename.

2b. **The rows the plan *adds*, written first.** A merge can introduce a
   category as well as remove one: reparenting "Dining"'s first real
   subcategory under "Food & Drink" makes `normalize_categories` mint
   `expense:food-drink:other`, and `id_remap` names that id as the
   successor of "Dining"'s own "Other". Everything from step 3 onwards
   resolves a successor natural key through `db.base.ids_by_natural_key`,
   which **subscripts** — a key with no row raises rather than resolving
   to `None` — so the added rows go in before any of them, through
   `replace_categories(..., prune=False)`. The set is the difference
   between the planned tree and the stored one, not anything the request
   names. Item A9: without it, merging into a target that had no
   subcategories yet was a 500.

3. **`taxonomy.remap_category_ids`** — the three collections
   that name a category and aren't the tree itself (loaded together as a
   `taxonomy.CategoryReferences`, one repository `load_*` each) get every
   `category_id`/`subcategory_id` field pointing at "Dining" swapped to
   "Food & Drink": `category_patterns`
   (`CategoryPattern.category_id`/`subcategory_id`), `budgets`
   (`Budget.category_id`/`subcategory_id` — per-month and general rows
   alike, since a general budget is just a `Budget` with no `month`), and
   `posting_splits`
   (`PostingSplitLeg.category_id`/`subcategory_id`). A `TransferRule` has
   no category fields of its own, so there is nothing there to repoint.

   One mapping to keep straight throughout: `CategoryPattern` and
   `TransferRule` are separate API resources but **one table**,
   `accounting.categorization_rules`, discriminated by `effect`
   (`categorize` vs `transfer`) — which is why the summary table at the
   bottom names that table twice, once per effect, with different
   behaviour on each row. A repointed budget's
   `budget_id` is **rebuilt** from its new
   `(month, category_id, subcategory_id)` triple (see
   `repositories.planning.budget_row_key`) rather than left naming the
   merged-away category — otherwise the next single-cell upsert for the
   surviving category would mint a second row the table's unique index
   rejects. If "Dining" and "Food & Drink" **both** already had a `Budget`
   for the same month/category/subcategory, the one that belonged to
   "Dining" (the *merged-away* id) is **dropped** — "Food & Drink"'s own
   entry always survives untouched. This used to raise a 409 and refuse the whole
   rename; now it just proceeds, since step 2's preview already gave the
   user a chance to see this coming and back out.

4. **The real transaction history — nothing at all happens to it.**
   This step used to be `accounting.importers.ingest.remap_ledger_category_ids`:
   load the whole ledger, find-and-replace `category_id`/`subcategory_id`,
   write every posting back. It is deleted. A posting's category is the
   one the *file* named — raw import provenance — and rewriting it was
   DB-audit finding D14: a derived, resolved value stored on the raw row,
   which the next re-import could silently revert, at the cost of a
   full-ledger read-modify-write on every rename. The merge reaches those
   postings through step 6's retirement instead, resolved on read by
   `repositories.taxonomy.load_category_redirects` +
   `ledger.categorization.apply_category_redirects` (which run before the
   first overlay stage — see `accounting.precedence`). `GET /ledger/export`
   therefore still shows the merged-away id, and `GET /postings` shows the
   survivor; that difference is the whole point.

5. **Manual overrides**, inline in `post_category_rename`, right after
   step 4 — `PostingOverride.category_id`/`subcategory_id` (a person's
   by-hand re-categorization, deliberately outside `CategoryReferences`
   too) get the same old-id → new-id swap. `load_overrides` reads them
   all, the handler filters down to the overrides that actually reference
   a merged-away id, and `save_overrides_for_postings` writes back **only
   those postings**. That scoping is the point: `save_overrides`, the
   whole-table write this replaced, deleted and reinserted every
   `posting_overrides` row for the user, so a rename racing an unrelated
   override edit on a different posting silently erased it.

6. **The scoped repository writes, then `retire_categories`, then
   `replace_categories`.** Everything step 3 repointed *except* the
   categories dict itself is written by its own aggregate's repository,
   and runs right after step 3: `budgets` through
   `repositories.planning.replace_budgets`, and
   `category_patterns`/`posting_splits` through
   `repositories.interpretation.replace_category_patterns`/
   `replace_posting_splits`. Then
   `repositories.taxonomy.retire_categories` takes "Dining" out of the
   live tree — `retired_at` set, `superseded_by_category_id` pointing at
   "Food & Drink" — **without deleting its row**. That is what makes the
   whole thing safe: `postings.category_id`/`subcategory_id` are real
   foreign keys into `categories.id` with no `ondelete` clause, and a row
   that is never deleted can never be deleted out from under them.
   `replace_categories` finally persists the live tree with a prune (its
   prune skips retired rows, since no caller-supplied tree contains one).
   The handler's single `session.commit()` follows.

   A merge is one hop, always: retiring a category that others already
   point at repoints them at its own successor in the same pass, so a
   tombstone never names another tombstone, and deleting a merge target
   later degrades the earlier merge into a delete.

## Category delete

`DELETE /categories/{category_id}`
(`accounting.api.routers.categories.delete_category`). Deleting "Dining" outright, not merging it into
anything.

1. **`taxonomy.category_ids_to_delete`** — a subcategory's delete
   never cascades (it has none of its own); a top-level category's delete
   takes every one of its subcategories down with it. Returns the full set
   of ids being removed, e.g. `{"expense:dining"}`, or
   `{"expense:dining", "expense:dining:fast-food"}` if "Dining" had a
   subcategory.

2. **`GET /categories/{category_id}/delete-preview`** — counts
   how many raw ledger postings currently carry any id from step 1 as
   their own `category_id` or `subcategory_id`
   (`_posting_count_for_categories`), without deleting anything.
   The frontend shows this count in a confirmation dialog ("N transactions
   will become uncategorized") only when it's greater than zero — deleting
   a category with no postings just happens immediately, no popup.

3. **`taxonomy.uncategorize_category_ids`** — the delete-side
   counterpart to `remap_category_ids` (step 3 above), except there's no
   replacement id to repoint at, so references are either cleared or the
   whole row is dropped, depending on whether the field is optional:
   - `PostingSplitLeg.category_id`/`subcategory_id` are nullable — cleared
     to `NULL`, row otherwise untouched. A `TransferRule` has no category
     fields at all (step 3 of the merge case above says the same), so
     there is nothing there to clear either.
   - `Budget.category_id` and `CategoryPattern.category_id` are **not**
     nullable. A row whose own `category_id` is being deleted has nothing
     left to be, so the whole row is **dropped**. A row only referencing a
     deleted id through its (nullable) `subcategory_id` — e.g. a budget
     for "Food & Drink" overall that also had a `subcategory_id` of
     "Dining" — keeps existing, just with `subcategory_id` cleared,
     becoming a category-level budget again (with its `budget_id` rebuilt,
     as in the merge case). If clearing it would collide with a budget
     that already holds that identity, the untouched one wins and the
     cleared one is dropped.

4. **The real transaction history — again, nothing happens to it.** The
   deleted counterpart here was `uncategorize_ledger_postings`, which
   nulled out every matching `category_id`/`subcategory_id` in place; see
   step 4 of the merge case for why both are gone. A category retired
   with *no* successor is exactly "resolves to uncategorized", which is
   the same state a posting that was never categorized is already in.

5. **Manual overrides**, inline in `delete_category`, right after step 4 —
   any `PostingOverride.category_id`/`subcategory_id` pointing at a
   deleted id is cleared the same way, and written back through the same
   scoped `load_overrides` + `save_overrides_for_postings` pair.

6. **The scoped repository writes, then `retire_categories`, then
   `replace_categories`** — the same split as step 6 of the merge case, in
   the same position: `replace_budgets` and
   `replace_category_patterns`/`replace_posting_splits` persist whatever
   step 3 cleared or dropped, then `retire_categories` retires "Dining"
   (and every subcategory step 1 cascaded to) with **no** successor, then
   `replace_categories` persists the tree without them. Everything
   referencing "Dining" that is *live* — budgets, patterns, split legs,
   overrides — still has to be cleared first, because those references
   really are rewritten; the postings don't, because their reference is
   never rewritten and its target is never deleted.

## Tag rename → merge

`POST /tags/{tag_id}/rename`
(`accounting.api.routers.tags.post_tag_rename`). Renaming tag "Trip" to an existing tag's name,
"Travel", merges the two. Built from scratch for this — nothing like it
existed before; the only way to "rename" a tag used to be delete-and-
recreate, which orphaned every reference to the old id.

1. **`taxonomy.plan_tag_rename`** — pure name match, case-
   insensitive, no classification or parent to scope it by (`Tag` has
   neither). No collision → `name` updated in place, same `tag_id`, empty
   remap. Collision → "Trip" removed from the tags dict, returns
   `{"tag:trip": "tag:travel"}`.

2. **`GET /tags/{tag_id}/rename-preview`** — same idea as the
   category preview: runs step 1 without persisting, reports whether it
   would merge and into what, for the same confirm-before-merge dialog.

3. **`repositories.taxonomy.remap_tag_ids`** — two join tables outside
   `tags` itself store a tag id, and both are handled identically, in
   direct SQL, with no override load/save anywhere in the path:
   - **`posting_tags`** (`accounting.db.core.PostingTag`) — which tags a
     person applied to a real posting.
   - **`posting_override_tags`**
     (`accounting.db.corrections.PostingOverrideTag`) — which tags a
     person's *override* replaces a posting's tags with. This used to be
     `posting_overrides.tag_ids_override`, a loose Postgres array of tag-id
     strings that no constraint could check; it is now one row per
     (override, tag) pair with a real `tag_id → tags.id` foreign key, and
     the model docstring says it mirrors `PostingTag` exactly.

   Each gets a `DELETE` of the rows that would collide followed by an
   `UPDATE … SET tag_id = <Travel's id> WHERE tag_id = <Trip's id>`. The
   delete comes first because both tables key on the tag: if a posting (or
   an override) already carried **both** "Trip" and "Travel", the update
   alone would violate that composite primary key, so the "Trip" row is
   dropped and the "Travel" row it already had is left untouched.

   Because both are now real foreign keys, the database itself would
   refuse a stale reference — which is exactly what the array could not
   do, and the reason this step existed as a hand-written find-and-replace
   before.

4. **`repositories.taxonomy.replace_tags`** — `tags` is upsert-and-prune,
   the same shape `accounts` and `categories` use: this is where "Trip"'s
   row is actually deleted, after step 3 has already repointed everything
   that could still reference it. It has to be that shape rather than a
   wipe, because `posting_tags.tag_id` and `posting_override_tags.tag_id`
   are real foreign keys into it — and their `ON DELETE CASCADE` is what
   makes pruning a tag remove it from every posting carrying it.

## What's deleted vs. just edited, at a glance

| Table | Category merge | Category delete | Tag merge |
|---|---|---|---|
| `categories` / `tags` | category **retired**, naming its successor; tag pruned | category + subcategories **retired**, no successor | old tag entry pruned |
| `categorization_rules` (transfer effect) | — no category columns to repoint | — same | — |
| `posting_split_legs` | `category_id`/`subcategory_id` repointed | cleared to `NULL` | — |
| `categorization_rules` (categorize effect), `budgets` | repointed; merged-away collision dropped | row dropped if its own `category_id` is deleted, else `subcategory_id` cleared | — |
| `posting_tags` | — | — | `tag_id` repointed; deleted if it'd duplicate an existing row |
| `postings` (real ledger) | **untouched** — resolves to the survivor on read | **untouched** — resolves to uncategorized on read | no column of its own — join table only |
| `posting_overrides` | `category_id`/`subcategory_id` repointed, for the referencing rows only | cleared to `NULL`, for the referencing rows only | — |
| `posting_override_tags` | — | — | `tag_id` repointed; deleted if it'd duplicate an existing row |

No row in `postings` is written *at all* by any of these three
operations — not deleted, not updated. That is the invariant the D14 fix
bought: the stored ledger is what was imported, and everything else is
resolved on top of it. Tag merging is the one that still writes a real
join row (`posting_tags.tag_id` is repointed), because a tag applied to a
posting is a user decision recorded in its own table, not a fact copied
off a statement.
