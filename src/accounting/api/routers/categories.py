"""Category endpoints — the category tree plus its create, rename-with-merge and delete-with-uncategorize writes."""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    BudgetToDeletePreview,
    CategoryCreate,
    CategoryDeletePreviewResponse,
    CategoryDeleteResponse,
    CategoryRenamePreviewResponse,
    CategoryRenameRequest,
    CategoryRenameResponse,
    SubcategoryCreate,
)
from accounting.api.entities import Category
from accounting.importers.ingest import load_ledger
from accounting.models import Budget
from accounting.models import Category as DomainCategory
from accounting.repositories.interpretation import (
    load_category_patterns,
    load_overrides,
    load_posting_splits,
    replace_category_patterns,
    replace_posting_splits,
    save_overrides_for_postings,
)
from accounting.repositories.planning import load_budgets, replace_budgets
from accounting.repositories.taxonomy import replace_categories, retire_categories
from accounting.taxonomy import (
    CategoryReferences,
    category_ids_to_delete,
    normalize_categories,
    plan_category_rename,
    remap_category_ids,
    seeded_categories,
    slugify,
    uncategorize_category_ids,
)
from db.current_user import get_current_user_id
from db.session import get_db
from http_api.locations import CREATED_WITH_LOCATION, location_of

router = APIRouter()


def _added_categories(before: dict[str, DomainCategory], after: dict[str, DomainCategory]) -> list[DomainCategory]:
    """Which categories a create actually introduced or changed, so only those need writing.

    Returns
    -------
    list[Category]
    """
    return [category for category_id, category in after.items() if before.get(category_id) != category]


@router.get("/categories/{category_id}")
def get_category(
    category_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Category:
    """Return one category — top-level or sub — by id.

    Serves both, because a subcategory is a `Category` with a
    `parent_category_id` living in the same flat, `category_id`-keyed map
    (see `models.Category`): there is one address space, so one route
    covers it, and `POST /categories/{parent_id}/subcategories` can point
    its `Location` here too.

    This is the address the two creates below advertise. It exists so that
    `Location` names something a client can actually fetch — until PR 3
    there was no way to read a single created row back at all, only the
    whole collection or `GET /store`.

    Returns
    -------
    Category

    Raises
    ------
    HTTPException
        404 if no category has this id.
    """
    category = seeded_categories(session, user_id).get(category_id)
    if category is None:
        raise HTTPException(status_code=404, detail=f"Category {category_id!r} not found")
    return Category.from_domain(category)


@router.post("/categories", status_code=201, responses=CREATED_WITH_LOCATION)
def post_category(
    request: CategoryCreate,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Category:
    """Create a new top-level category, refusing a same-classification, same-name duplicate.

    Only ever adds a category, which is the whole reason the retired
    whole-tree `PUT /categories` is not missed: there, a client-computed
    id that happened to collide with an existing one silently overwrote
    it, and everything the request left out was pruned. Here a name
    collision is rejected outright and no other row is touched.

    A genuine `201`, not a hedge: the id is derived from the name
    (`{classification}:{slug}`), but the two 409s below mean this route
    can only ever bring a category into existence. It never replaces one,
    so `201 Created` is the whole truth about what happened — which is
    why this stays a `POST` on the collection rather than becoming the
    `PUT /{id}` the genuinely-upserting routes became.

    Returns
    -------
    Category
        The category just persisted, including its computed `category_id`.

    Raises
    ------
    HTTPException
        409 if a top-level category of the same classification already
        has this name (case-insensitive), or a distinct name collides with an existing category's slug id.
    """
    existing_categories = seeded_categories(session, user_id)
    normalized_name = request.name.strip().lower()
    collision = any(
        category.parent_category_id is None
        and category.classification == request.classification
        and category.name.strip().lower() == normalized_name
        for category in existing_categories.values()
    )
    if collision:
        raise HTTPException(
            status_code=409, detail=f"A {request.classification} category named {request.name!r} already exists"
        )

    category_id = f"{request.classification}:{slugify(request.name)}"
    if category_id in existing_categories:
        # The name-collision check above is case-insensitive on the name, but the
        # id is a lossy slug — two distinct names can still collide on it and
        # silently overwrite the existing category. Reject instead.
        raise HTTPException(
            status_code=409, detail=f"The name {request.name!r} is too similar to an existing category — pick another"
        )
    new_category = DomainCategory(
        category_id=category_id,
        name=request.name,
        classification=request.classification,
        parent_category_id=None,
        color=request.color,
    )
    # Only the rows this create actually adds get written — never the rest of
    # the tree, and never a prune. `normalize_categories` runs because it can
    # mint an "Other" catch-all alongside a new category, so "what this adds"
    # isn't always just the one row the request named.
    categories = normalize_categories({**existing_categories, category_id: new_category})
    replace_categories(session, user_id, _added_categories(existing_categories, categories), prune=False)
    session.commit()
    location_of(http_request, response, "get_category", category_id=category_id)
    return Category.from_domain(new_category)


@router.post("/categories/{parent_id}/subcategories", status_code=201, responses=CREATED_WITH_LOCATION)
def post_subcategory(
    parent_id: str,
    request: SubcategoryCreate,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Category:
    """Create a new subcategory under `parent_id`, refusing a same-name sibling duplicate.

    A genuine `201` for the same reason as `post_category`: the sibling
    and slug 409s below leave creation as the only outcome. Its `Location`
    points at `GET /categories/{category_id}`, not at a route nested under
    the parent — a subcategory is addressed like any other category.

    Returns
    -------
    Category
        The subcategory just persisted, including its computed `category_id`.

    Raises
    ------
    HTTPException
        404 if `parent_id` doesn't exist; 409 if a sibling subcategory
        already has this name (case-insensitive), or a distinct name collides with an existing subcategory's slug id.
    """
    existing_categories = seeded_categories(session, user_id)
    parent = existing_categories.get(parent_id)
    if parent is None:
        raise HTTPException(status_code=404, detail=f"Category {parent_id!r} not found")

    normalized_name = request.name.strip().lower()
    collision = any(
        category.parent_category_id == parent_id and category.name.strip().lower() == normalized_name
        for category in existing_categories.values()
    )
    if collision:
        raise HTTPException(
            status_code=409, detail=f"A subcategory named {request.name!r} already exists under {parent.name!r}"
        )

    category_id = f"{parent_id}:{slugify(request.name)}"
    if category_id in existing_categories:
        # See post_category: the name check is case-insensitive, but the slug id
        # is lossy — guard against two distinct names colliding on it.
        raise HTTPException(
            status_code=409,
            detail=f"The name {request.name!r} is too similar to an existing subcategory — pick another",
        )
    new_category = DomainCategory(
        category_id=category_id,
        name=request.name,
        classification=parent.classification,
        parent_category_id=parent_id,
        color=request.color,
    )
    # See `post_category`: additive only, and `normalize_categories` may add
    # the parent's "Other" catch-all alongside this first real subcategory.
    categories = normalize_categories({**existing_categories, category_id: new_category})
    replace_categories(session, user_id, _added_categories(existing_categories, categories), prune=False)
    session.commit()
    location_of(http_request, response, "get_category", category_id=category_id)
    return Category.from_domain(new_category)


def _category_references(session: Session, user_id: uuid.UUID) -> CategoryReferences:
    """Load every row a category rename or delete has to repoint or clear, from each row's own repository.

    Returns
    -------
    CategoryReferences
    """
    return CategoryReferences(
        category_patterns=load_category_patterns(session, user_id),
        budgets=load_budgets(session, user_id),
        posting_splits=load_posting_splits(session, user_id),
    )


def _write_category_references(references: CategoryReferences, session: Session, user_id: uuid.UUID) -> None:
    """Write each repointed/cleared collection back through its own repository.

    Always called *before* the category rows they used to reference are
    pruned or retired, so a foreign key never briefly points at a row
    that is about to disappear.
    """
    replace_budgets(session, user_id, references.budgets)
    replace_category_patterns(session, user_id, references.category_patterns.values())
    replace_posting_splits(session, user_id, references.posting_splits.values())


def _categories_leaving_the_tree(
    categories: dict[str, DomainCategory], category_id: str
) -> tuple[set[str], dict[str, DomainCategory]]:
    """Every id a delete of `category_id` actually removes, and the tree that survives it.

    Not the same set as `taxonomy.category_ids_to_delete`, and the
    difference is item A10. That function answers what the *request*
    names — the category plus, for a top-level one, its subcategories.
    `normalize_categories` then removes one more row nobody asked about:
    a parent left with no real subcategory loses its "Other" catch-all,
    because "Other" alongside nothing is meaningless.

    Every step of the delete has to work from *this* set instead, because
    that catch-all is a real row with real references. Budgets and
    category patterns foreign-key into it (`NO ACTION`), so pruning it
    with a budget still pointing at it is an `IntegrityError` and a 500;
    a posting filed under it is worse still, since that is raw import
    provenance the delete is not allowed to invalidate. Retiring it with
    the rest is what makes both safe.

    Returns
    -------
    tuple[set[str], dict[str, Category]]
        The ids leaving the live tree, and the tree left behind.
    """
    named = category_ids_to_delete(categories, category_id)
    remaining = normalize_categories({
        existing_id: category for existing_id, category in categories.items() if existing_id not in named
    })
    return set(categories) - set(remaining), remaining


def _posting_count_for_categories(category_ids: set[str], session: Session, user_id: uuid.UUID) -> int:
    """How many raw ledger postings currently carry any of `category_ids` as their category or subcategory.

    Returns
    -------
    int
    """
    ledger = load_ledger(session, user_id)
    if ledger.is_empty():
        return 0
    matches = ledger.filter(ledger["category_id"].is_in(category_ids) | ledger["subcategory_id"].is_in(category_ids))
    return matches.height


@router.get("/categories/{category_id}/delete-preview")
def get_category_delete_preview(
    category_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategoryDeletePreviewResponse:
    """Report how many postings deleting `category_id` would uncategorize, before actually deleting it.

    A caller can show a confirmation dialog with this count first, and
    only actually call `DELETE /categories/{category_id}` once the user
    accepts.

    Counts over `_categories_leaving_the_tree`, the same set the delete
    itself acts on, so the number in the confirmation dialog is the
    number the delete will report having uncategorized.

    Returns
    -------
    CategoryDeletePreviewResponse

    Raises
    ------
    HTTPException
        404 if `category_id` doesn't exist.
    """
    categories = seeded_categories(session, user_id)
    if category_id not in categories:
        raise HTTPException(status_code=404, detail=f"Category {category_id!r} not found")

    removed_ids, _remaining = _categories_leaving_the_tree(categories, category_id)
    return CategoryDeletePreviewResponse(posting_count=_posting_count_for_categories(removed_ids, session, user_id))


@router.delete("/categories/{category_id}")
def delete_category(
    category_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategoryDeleteResponse:
    """Delete a category (and, for a top-level one, every subcategory with it), uncategorizing its postings.

    Every posting currently carrying `category_id` (or one of its
    subcategories) reads as uncategorized afterwards — the same state a
    posting that was never categorized at all is already in — without a
    single posting row being written. The category is *retired* rather
    than deleted (see `accounting.db.core.Category` and
    `repositories.taxonomy.retire_categories`): its row stays, so the raw
    import provenance on those postings keeps a valid foreign key, and it
    leaves the live tree with no successor, which is what
    `repositories.taxonomy.load_category_redirects` resolves to "nothing".

    Anything else referencing the deleted id(s) *is* rewritten, because
    those references are the user's own decisions rather than raw
    provenance: cleared where the field is optional (`PostingSplitLeg`,
    and any `subcategory_id`) or dropped entirely where it isn't
    (`Budget`, `CategoryPattern` both require a `category_id`) — see
    `taxonomy.uncategorize_category_ids`.

    "The deleted id(s)" means `_categories_leaving_the_tree`, not the
    ones the request names. Deleting a category's last real subcategory
    also removes the parent's now-pointless "Other" catch-all, and that
    row has references of its own.

    Answers 200 with a body rather than the 204 the other row deletes answer:
    this delete has effects beyond the row it names — it cascades to
    subcategories, re-derives the survivors' "Other" catch-alls, and
    uncategorizes an arbitrary number of postings — so the resulting tree and
    the count of affected postings are not derivable from the request. A 204
    here would mean the caller could not tell the user what just happened.

    Returns
    -------
    CategoryDeleteResponse
        The category tree that survives, and how many postings now read as
        uncategorized.

    Raises
    ------
    HTTPException
        404 if `category_id` doesn't exist.
    """
    categories = seeded_categories(session, user_id)
    if category_id not in categories:
        raise HTTPException(status_code=404, detail=f"Category {category_id!r} not found")

    # Every id that actually leaves the tree, which is a superset of the ones
    # the request names — see `_categories_leaving_the_tree` (A10).
    removed_ids, remaining_categories = _categories_leaving_the_tree(categories, category_id)
    posting_count = _posting_count_for_categories(removed_ids, session, user_id)

    # The planning and interpretation tables both reference categories, so their
    # cleared/dropped rows land before `replace_categories` prunes the category
    # rows they used to point at.
    _write_category_references(
        uncategorize_category_ids(_category_references(session, user_id), removed_ids), session, user_id
    )

    def clear(field_id: str | None) -> str | None:
        return None if field_id in removed_ids else field_id

    # Only the overrides that actually reference a deleted category/subcategory get touched — every
    # other posting's override is left alone, unlike the whole-table writer this
    # replaced, which rewrote every posting's override on every category delete.
    overrides = load_overrides(session, user_id)
    changed_overrides = {
        posting_id: override.model_copy(
            update={"category_id": clear(override.category_id), "subcategory_id": clear(override.subcategory_id)}
        )
        for posting_id, override in overrides.items()
        if override.category_id in removed_ids or override.subcategory_id in removed_ids
    }
    save_overrides_for_postings(list(changed_overrides.keys()), changed_overrides, session, user_id)

    # This, not the prune below, is what takes the categories out of the live
    # tree — with no successor, so every posting imported under one resolves to
    # uncategorized. Retiring rather than deleting is what makes the stored
    # postings' foreign keys safe without rewriting a single one of them.
    retire_categories(session, user_id, dict.fromkeys(removed_ids))

    # Last. The whole tree is passed because a delete genuinely is
    # category-graph-wide — a top-level delete takes its subcategories with it,
    # and the survivors' "Other" catch-alls were re-derived by
    # `normalize_categories` above. Its prune never touches a retired row.
    replace_categories(session, user_id, remaining_categories.values())
    session.commit()
    return CategoryDeleteResponse(
        categories={
            category_id: Category.from_domain(category) for category_id, category in remaining_categories.items()
        },
        uncategorized_posting_count=posting_count,
    )


@router.get("/categories/{category_id}/rename-preview")
def get_category_rename_preview(
    category_id: str,
    name: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategoryRenamePreviewResponse:
    """Report whether renaming `category_id` to `name` would merge it into an existing category.

    Calls the same pure `plan_category_rename`/`remap_category_ids`
    `post_category_rename` itself uses, but never persists anything — a
    caller can show a confirmation dialog first (including which budgets,
    if any, would be silently discarded — see `budgets_to_delete`), and
    only actually call `POST /categories/{category_id}/rename` once the
    user accepts.

    Returns
    -------
    CategoryRenamePreviewResponse
        `will_merge` is true if this rename would fold into an existing
        category rather than just changing a name; `target_name` is that
        existing category's name, or `None` when `will_merge` is false;
        `budgets_to_delete` lists every `Budget` entry (per-month or
        general) the merged-away category holds that the merge target
        already has one for, and which would therefore be discarded (see
        `taxonomy.remap_category_ids`).

    Raises
    ------
    HTTPException
        404 if `category_id` doesn't exist.
    """
    existing_categories = seeded_categories(session, user_id)
    if category_id not in existing_categories:
        raise HTTPException(status_code=404, detail=f"Category {category_id!r} not found")

    _categories, id_remap = plan_category_rename(existing_categories, category_id, name)
    target_id = id_remap.get(category_id)
    target_name = existing_categories[target_id].name if target_id is not None else None

    references = _category_references(session, user_id)
    updated = remap_category_ids(references, id_remap)
    # Matched on the identity triple rather than on `budget_id`, because a
    # repointed budget's id is rebuilt from its new category (see
    # `taxonomy.remap_category_ids`) — and *counted*, because two budgets can
    # land on the same triple and only one survives. Walking the source
    # budgets untouched-first consumes the survivors in the same order the
    # merge itself arbitrated them, so the entry reported as discarded is
    # always the merged-away category's, never the target's.
    remaining = Counter((budget.month, budget.category_id, budget.subcategory_id) for budget in updated.budgets)

    def post_merge_key(budget: Budget) -> tuple[str | None, str, str | None]:
        """Build the `(month, category_id, subcategory_id)` identity this budget would have after the merge.

        Returns
        -------
        tuple[str | None, str, str | None]
        """
        subcategory_id = (
            id_remap.get(budget.subcategory_id, budget.subcategory_id) if budget.subcategory_id is not None else None
        )
        return budget.month, id_remap.get(budget.category_id, budget.category_id), subcategory_id

    budgets_to_delete: list[BudgetToDeletePreview] = []
    for budget in sorted(references.budgets, key=lambda b: b.category_id in id_remap or b.subcategory_id in id_remap):
        key = post_merge_key(budget)
        if remaining[key] > 0:
            remaining[key] -= 1
            continue
        budgets_to_delete.append(
            BudgetToDeletePreview(month=budget.month, amount=budget.amount, currency=budget.currency)
        )
    return CategoryRenamePreviewResponse(
        will_merge=target_id is not None, target_name=target_name, budgets_to_delete=budgets_to_delete
    )


@router.post("/categories/{category_id}/rename")
def post_category_rename(
    category_id: str,
    request: CategoryRenameRequest,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategoryRenameResponse:
    """Rename a category or subcategory, merging it into an existing same-named one if there is one.

    A merge repoints every reference to the merged-away id that is a user
    decision — manual per-posting overrides, transfer rules, category
    patterns, budgets, and posting splits — onto the surviving id, and
    *retires* the merged-away category rather than deleting it (see
    `accounting.db.core.Category`). Stored postings are not touched at all:
    the category one was imported under is raw provenance, and the
    retirement's own successor is what makes it resolve to the survivor
    from now on (`repositories.taxonomy.load_category_redirects`). See
    `taxonomy.plan_category_rename` for the exact matching rules: a top-level
    category only merges into another top-level category of the same
    classification; a subcategory only merges into a sibling under the
    same parent. If the merge target already has a budget for a month the
    merged-away category also budgeted, the merged-away category's budget
    is discarded (see `taxonomy.remap_category_ids`) — call
    `GET /categories/{category_id}/rename-preview` first to warn about
    that before committing to the rename.

    A merge can also *add* a category: reparenting the merged-away
    category's first real subcategory under the target makes
    `taxonomy.normalize_categories` mint the target's own "Other"
    catch-all. Those rows are written first, additively, because every
    step after them resolves a natural key that has to already exist.

    Returns
    -------
    CategoryRenameResponse
        `categories` is the full tree after the change; `merged` is true
        if this rename actually folded into an existing category rather
        than just changing a name.

    Raises
    ------
    HTTPException
        404 if `category_id` doesn't exist.
    """
    existing_categories = seeded_categories(session, user_id)
    if category_id not in existing_categories:
        raise HTTPException(status_code=404, detail=f"Category {category_id!r} not found")

    categories, id_remap = plan_category_rename(existing_categories, category_id, request.name)

    # Every row the plan *introduces*, written before anything resolves one of
    # them. A merge can mint a category that has no row yet — reparenting the
    # first real subcategory under the target makes `normalize_categories` give
    # the target its own "Other" catch-all — and `id_remap` names that new id as
    # a successor. Both steps below resolve successor natural keys through
    # `ids_by_natural_key`, which subscripts on purpose, so a successor with no
    # row is an `UnknownNaturalKeyError` rather than a silent `None`. Taken from
    # the difference between the planned tree and the stored one rather than
    # from the request, because the request never names the catch-all.
    replace_categories(
        session,
        user_id,
        [category for new_id, category in categories.items() if new_id not in existing_categories],
        prune=False,
    )

    # Repointed budgets, category patterns and posting splits are written by
    # their own repositories, before `replace_categories` below prunes the
    # merged-away category rows they used to reference.
    _write_category_references(remap_category_ids(_category_references(session, user_id), id_remap), session, user_id)

    if id_remap:

        def remap(category_id: str | None) -> str | None:
            """Look up `category_id`'s new id, or leave it unchanged if it wasn't merged away.

            Returns
            -------
            str or None
            """
            return id_remap.get(category_id, category_id) if category_id is not None else None

        # Only the overrides that actually reference a merged-away category/subcategory get
        # touched — see the equivalent note in `delete_category` for why.
        overrides = load_overrides(session, user_id)
        changed_overrides = {
            posting_id: override.model_copy(
                update={"category_id": remap(override.category_id), "subcategory_id": remap(override.subcategory_id)}
            )
            for posting_id, override in overrides.items()
            if override.category_id in id_remap or override.subcategory_id in id_remap
        }
        save_overrides_for_postings(list(changed_overrides.keys()), changed_overrides, session, user_id)

    # The merged-away categories leave the live tree here, each naming the one
    # it folded into — which is the whole of how the postings imported under
    # them start resolving to the survivor, with no posting rewritten.
    retire_categories(session, user_id, dict(id_remap))

    # With a prune: every *live* reference was repointed above, and a retired
    # row is exempt from the prune (see `replace_categories`).
    replace_categories(session, user_id, categories.values())
    session.commit()

    return CategoryRenameResponse(
        categories={renamed_id: Category.from_domain(category) for renamed_id, category in categories.items()},
        merged=bool(id_remap),
    )
