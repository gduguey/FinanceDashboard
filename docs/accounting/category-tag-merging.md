# Category and tag merging, and category deleting

What actually happens, table by table and column by column, when a
category or tag rename turns into a merge, or when a category is deleted.
Every step below is listed in the order the real code runs it — see
`docs/accounting/categorization.md` for what categories/tags are;
`src/db/README.md`'s "How a save actually writes to Postgres" for the
wipe-and-reinsert/upsert-and-prune background these mechanisms build on.

## Category rename → merge

`POST /categories/{category_id}/rename` (`accounting.api.routers.store.post_category_rename:408`).
Renaming "Dining" to an existing category's name, "Food & Drink", merges
the two. The matching rule itself lives in `store.plan_category_rename`
(`:250`) and isn't repeated here — a top-level category only ever merges
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

2. **`GET /categories/{category_id}/rename-preview`** (`:348`) — before
   the real rename is ever called, the frontend calls this to find out
   whether it *would* merge. It runs step 1 (`plan_category_rename`) plus
   step 3 below (`remap_category_ids`) against the *current* store,
   without persisting anything, and diffs `store.budgets`/
   `general_budgets` before and after to report exactly which entries
   would be discarded (`api_models.BudgetToDeletePreview`) — see step 3
   for why some are discarded rather than repointed. The UI shows this in
   a confirmation dialog before the user commits to the real rename.

3. **`store.remap_category_ids`** (`:335`) — five in-memory collections
   get every `category_id`/`subcategory_id` field pointing at "Dining"
   swapped to "Food & Drink": `store.rules` (`TransferRule.category_id`/
   `subcategory_id`), `store.category_patterns`
   (`CategoryPattern.category_id`/`subcategory_id`), `store.budgets`
   (`Budget.category_id`/`subcategory_id`), `store.general_budgets`
   (`GeneralBudget.category_id`/`subcategory_id`, and the dict's own key,
   which is whichever of the two is more specific), `store.posting_splits`
   (`PostingSplitLeg.category_id`/`subcategory_id`). If "Dining" and "Food
   & Drink" **both** already had a `Budget`/`GeneralBudget` for the same
   month/category/subcategory, the one that belonged to "Dining" (the
   *merged-away* id) is **dropped** — "Food & Drink"'s own entry always
   survives untouched. This used to raise a 409 and refuse the whole
   rename; now it just proceeds, since step 2's preview already gave the
   user a chance to see this coming and back out.

4. **`accounting.importers.ingest.remap_ledger_category_ids`** (`:241`) —
   the real transaction history, which lives entirely outside
   `AccountingStore` (see `src/db/README.md`). Loads the whole ledger,
   does a plain Polars find-and-replace of `category_id`/`subcategory_id`
   (old id → new id), and writes it back through `_write_ledger`, which
   **updates existing `postings` rows in place** (matched by each
   posting's own stable `id`) — no row is deleted or reinserted here.

5. **Manual overrides**, inline in `post_category_rename` (`:408`, right
   after step 4) — `PostingOverride.category_id`/`subcategory_id` (a
   person's by-hand re-categorization, stored outside `AccountingStore`
   too) get the same old-id → new-id swap via `load_overrides`/
   `save_overrides`, which fully deletes and reinserts every
   `posting_overrides` row for this user (same pattern as the 16
   wipe-and-reinsert tables in `src/db/README.md`).

6. **The scoped repository writes, then `replace_categories`.** Everything
   step 3 repointed *except* the categories dict itself is written by its
   own aggregate's repository, and runs right after step 3 (before step 4,
   not after step 5): `store.budgets`/`general_budgets` through
   `repositories.planning.replace_budgets`/`replace_general_budgets`, and
   `store.category_patterns`/`posting_splits` through
   `repositories.interpretation.replace_category_patterns`/
   `replace_posting_splits`. `repositories.taxonomy.replace_categories`
   then persists the categories dict, with a prune — `categories` is one of
   the three upsert-and-prune tables, and this is the point "Dining"'s row
   is actually deleted. This has to happen *last*:
   `postings.category_id`/`subcategory_id` and `posting_split_legs.
   category_id`/`subcategory_id` are real foreign keys into `categories.id`
   with no `ondelete` clause, so Postgres would reject deleting "Dining"
   at step 6 if steps 3–5 hadn't already repointed everything referencing
   it. See "What happens if you delete something still in use" in
   `src/db/README.md`. The handler's single `session.commit()` follows.

## Category delete

`DELETE /categories/{category_id}` (`accounting.api.routers.store.
delete_category:287`). Deleting "Dining" outright, not merging it into
anything.

1. **`store.category_ids_to_delete`** (`:446`) — a subcategory's delete
   never cascades (it has none of its own); a top-level category's delete
   takes every one of its subcategories down with it. Returns the full set
   of ids being removed, e.g. `{"expense:dining"}`, or
   `{"expense:dining", "expense:dining:fast-food"}` if "Dining" had a
   subcategory.

2. **`GET /categories/{category_id}/delete-preview`** (`:258`) — counts
   how many raw ledger postings currently carry any id from step 1 as
   their own `category_id` or `subcategory_id`
   (`_posting_count_for_categories`, `:243`), without deleting anything.
   The frontend shows this count in a confirmation dialog ("N transactions
   will become uncategorized") only when it's greater than zero — deleting
   a category with no postings just happens immediately, no popup.

3. **`store.uncategorize_category_ids`** (`:473`) — the delete-side
   counterpart to `remap_category_ids` (step 3 above), except there's no
   replacement id to repoint at, so references are either cleared or the
   whole row is dropped, depending on whether the field is optional:
   - `TransferRule.category_id`/`subcategory_id` and
     `PostingSplitLeg.category_id`/`subcategory_id` are nullable — cleared
     to `NULL`, row otherwise untouched.
   - `Budget.category_id`, `GeneralBudget.category_id`, and
     `CategoryPattern.category_id` are **not** nullable. A row whose own
     `category_id` is being deleted has nothing left to be, so the whole
     row is **dropped**. A row only referencing a deleted id through its
     (nullable) `subcategory_id` — e.g. a budget for "Food & Drink"
     overall that also had a `subcategory_id` of "Dining" — keeps
     existing, just with `subcategory_id` cleared, becoming a
     category-level budget again.

4. **`accounting.importers.ingest.uncategorize_ledger_postings`**
   (`:269`) — same shape as `remap_ledger_category_ids`, but replacing
   every matching `category_id`/`subcategory_id` with `NULL` instead of a
   new id. Updates `postings` rows in place, same as step 4 of the merge
   case.

5. **Manual overrides**, inline in `delete_category` (`:287`, right after
   step 4) — any `PostingOverride.category_id`/`subcategory_id` pointing
   at a deleted id is cleared the same way, via `load_overrides`/
   `save_overrides`.

6. **The scoped repository writes, then `replace_categories`** — the same
   split as step 6 of the merge case, in the same position:
   `replace_budgets`/`replace_general_budgets` and
   `replace_category_patterns`/`replace_posting_splits` persist whatever
   step 3 cleared or dropped (running right after step 3), then
   `replace_categories` persists the categories dict with "Dining" (and any
   subcategories) removed. Same ordering requirement as the merge case:
   everything referencing "Dining" has to be cleared *before*
   `replace_categories` prunes, or it would hit the same foreign-key
   rejection.

## Tag rename → merge

`POST /tags/{tag_id}/rename` (`accounting.api.routers.store.
post_tag_rename:569`). Renaming tag "Trip" to an existing tag's name,
"Travel", merges the two. Built from scratch for this — nothing like it
existed before; the only way to "rename" a tag used to be delete-and-
recreate, which orphaned every reference to the old id.

1. **`store.plan_tag_rename`** (`:561`) — pure name match, case-
   insensitive, no classification or parent to scope it by (`Tag` has
   neither). No collision → `name` updated in place, same `tag_id`, empty
   remap. Collision → "Trip" removed from the tags dict, returns
   `{"tag:trip": "tag:travel"}`.

2. **`GET /tags/{tag_id}/rename-preview`** (`:533`) — same idea as the
   category preview: runs step 1 without persisting, reports whether it
   would merge and into what, for the same confirm-before-merge dialog.

3. **`repositories.taxonomy.remap_tag_ids`** — two places outside the `tags`
   table itself need fixing, for two different reasons:
   - **`posting_tags`** (`accounting.db.core.PostingTag`, a real,
     database-enforced foreign key: `posting_tags.tag_id → tags.id`) — a
     direct `UPDATE posting_tags SET tag_id = <Travel's id> WHERE tag_id =
     <Trip's id>`. If a posting already had **both** "Trip" and "Travel"
     applied, updating would violate `posting_tags`' own
     `(user_id, posting_id, tag_id)` uniqueness — for those postings, the
     "Trip" row is **deleted** instead (the "Travel" row it already had is
     untouched).
   - **`posting_overrides.tag_ids_override`** — a raw Postgres array of
     tag-id strings, **not** foreign-keyed to anything, so nothing in the
     database would catch a stale "Trip" string left in there on its own.
     Every override's array gets an explicit find-and-replace
     (`"tag:trip"` → `"tag:travel"`, de-duplicated in case an override
     already listed both) via `load_overrides`/`save_overrides`.

4. **`repositories.taxonomy.replace_tags`** — `tags` is upsert-and-prune too
   (same three-table list as `accounts`/`categories`): this is where
   "Trip"'s row is actually deleted, after step 3 has already repointed
   everything that could still reference it.

## What's deleted vs. just edited, at a glance

| Table | Category merge | Category delete | Tag merge |
|---|---|---|---|
| `categories` / `tags` | old entry pruned | target + subcategories pruned | old entry pruned |
| `transfer_rules`, `posting_split_legs` | `category_id`/`subcategory_id` repointed | cleared to `NULL` | — |
| `category_patterns`, `budgets`, `general_budgets` | repointed; merged-away collision dropped | row dropped if its own `category_id` is deleted, else `subcategory_id` cleared | — |
| `posting_tags` | — | — | `tag_id` repointed; deleted if it'd duplicate an existing row |
| `postings` (real ledger) | `category_id`/`subcategory_id` updated in place | cleared to `NULL` in place | no column of its own — join table only |
| `posting_overrides` | `category_id`/`subcategory_id` repointed | cleared to `NULL` | `tag_ids_override` array entries replaced |

No row in `postings` is ever deleted by any of these three operations —
only a column value changes, and only on the rows that actually
referenced what was renamed/deleted.
