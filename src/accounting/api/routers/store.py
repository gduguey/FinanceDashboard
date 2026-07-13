"""Persisted-entity CRUD — mirrors `accounting.store`: accounts, categories, tags, rules, budgets, other assets."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    AccountCloseRequest,
    AccountCloseResponse,
    AccountCreate,
    AccountIdResponse,
    AccountingStoreResponse,
    AccountUpdate,
    BudgetToDeletePreview,
    CategoryCreate,
    CategoryDeletePreviewResponse,
    CategoryDeleteResponse,
    CategoryRenamePreviewResponse,
    CategoryRenameRequest,
    CategoryRenameResponse,
    SubcategoryCreate,
    TagCreate,
    TagRenamePreviewResponse,
    TagRenameRequest,
    TagRenameResponse,
)
from accounting.api.dependencies import _account_has_postings, _resolved_postings_and_store, state
from accounting.importers.ingest import load_ledger, remap_ledger_category_ids, uncategorize_ledger_postings
from accounting.models import (
    SUPPORTED_CURRENCIES,
    Account,
    Budget,
    Category,
    CategoryPattern,
    Currency,
    GeneralBudget,
    OpeningBalance,
    OtherAsset,
    SimulatorScenario,
    Tag,
    TransferRule,
)
from accounting.store import (
    category_ids_to_delete,
    get_store_version,
    load_overrides,
    load_store,
    normalize_categories,
    plan_category_rename,
    plan_tag_rename,
    remap_category_ids,
    remap_tag_ids,
    save_overrides,
    save_store,
    slugify,
    uncategorize_category_ids,
)
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()


@router.get("/store")
def get_store(
    session: Annotated[Session, Depends(get_db)], user_id: Annotated[uuid.UUID, Depends(get_current_user_id)]
) -> AccountingStoreResponse:
    """Return every persisted accounting entity: accounts, categories, tags, rules, other assets.

    Returns
    -------
    AccountingStoreResponse
        `accounts`, `categories`, `tags`, `opening_balances` (each a dict
        keyed by id), `transfer_rules`, `other_assets`, `budgets` (each a
        list). `version` is this user's current save counter (see
        `store.get_store_version`) — a client should remember it and send
        it back as the `X-Expected-Store-Version` header on its next
        mutating request, so `save_store` can detect if something else
        changed this data in the meantime.
    """
    _postings, store = _resolved_postings_and_store(state.config, session, user_id)
    return AccountingStoreResponse(
        accounts=store.accounts,
        categories=store.categories,
        tags=store.tags,
        transfer_rules=store.rules,
        other_assets=store.other_assets,
        opening_balances=store.opening_balances,
        manual_transfers=store.manual_transfers,
        budgets=store.budgets,
        simulator_scenarios=store.simulator_scenarios,
        posting_splits=store.posting_splits,
        posting_merges=store.posting_merges,
        general_budgets=store.general_budgets,
        category_patterns=store.category_patterns,
        goals=store.goals,
        goal_contributions=store.goal_contributions,
        recurring_additions=store.recurring_additions,
        withdrawal_priorities=store.withdrawal_priorities,
        version=get_store_version(session, user_id),
    )


@router.get("/currencies")
def get_currencies() -> list[Currency]:
    """List every currency this app knows how to hold money in.

    Returns
    -------
    list[Currency]
        One entry per supported currency.
    """
    return list(SUPPORTED_CURRENCIES.values())


@router.post("/categories")
def post_category(
    request: CategoryCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Category:
    """Create a new top-level category, refusing a same-classification, same-name duplicate.

    Unlike `put_categories` (a whole-tree replace, where a client-computed
    id that happens to collide with an existing one silently overwrites
    it), this only ever adds a category — a name collision is rejected
    outright rather than clobbering the existing entry.

    Returns
    -------
    Category
        The category just persisted, including its computed `category_id`.

    Raises
    ------
    HTTPException
        409 if a top-level category of the same classification already
        has this name (case-insensitive).
    """
    store = load_store(session, user_id)
    normalized_name = request.name.strip().lower()
    collision = any(
        category.parent_category_id is None
        and category.classification == request.classification
        and category.name.strip().lower() == normalized_name
        for category in store.categories.values()
    )
    if collision:
        raise HTTPException(
            status_code=409, detail=f"A {request.classification} category named {request.name!r} already exists"
        )

    category_id = f"{request.classification}:{slugify(request.name)}"
    new_category = Category(
        category_id=category_id,
        name=request.name,
        classification=request.classification,
        parent_category_id=None,
        color=request.color,
    )
    store = store.model_copy(
        update={"categories": normalize_categories({**store.categories, category_id: new_category})}
    )
    save_store(store, session, user_id)
    return new_category


@router.post("/categories/{parent_id}/subcategories")
def post_subcategory(
    parent_id: str,
    request: SubcategoryCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Category:
    """Create a new subcategory under `parent_id`, refusing a same-name sibling duplicate.

    Returns
    -------
    Category
        The subcategory just persisted, including its computed `category_id`.

    Raises
    ------
    HTTPException
        404 if `parent_id` doesn't exist; 409 if a sibling subcategory
        already has this name (case-insensitive).
    """
    store = load_store(session, user_id)
    parent = store.categories.get(parent_id)
    if parent is None:
        raise HTTPException(status_code=404, detail=f"Category {parent_id!r} not found")

    normalized_name = request.name.strip().lower()
    collision = any(
        category.parent_category_id == parent_id and category.name.strip().lower() == normalized_name
        for category in store.categories.values()
    )
    if collision:
        raise HTTPException(
            status_code=409, detail=f"A subcategory named {request.name!r} already exists under {parent.name!r}"
        )

    category_id = f"{parent_id}:{slugify(request.name)}"
    new_category = Category(
        category_id=category_id,
        name=request.name,
        classification=parent.classification,
        parent_category_id=parent_id,
        color=request.color,
    )
    store = store.model_copy(
        update={"categories": normalize_categories({**store.categories, category_id: new_category})}
    )
    save_store(store, session, user_id)
    return new_category


@router.put("/categories")
def put_categories(
    categories: dict[str, Category],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> dict[str, Category]:
    """Replace the whole category tree, enforcing the "Other" catch-all subcategory invariant.

    Returns
    -------
    dict[str, Category]
        The categories just persisted, keyed by `category_id` — may
        include an "Other" subcategory the caller didn't submit, or omit
        one it did (see `store.normalize_categories`).
    """
    store = load_store(session, user_id)
    store = store.model_copy(update={"categories": normalize_categories(categories)})
    save_store(store, session, user_id)
    return store.categories


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

    Returns
    -------
    CategoryDeletePreviewResponse

    Raises
    ------
    HTTPException
        404 if `category_id` doesn't exist.
    """
    store = load_store(session, user_id)
    if category_id not in store.categories:
        raise HTTPException(status_code=404, detail=f"Category {category_id!r} not found")

    ids_to_delete = category_ids_to_delete(store.categories, category_id)
    return CategoryDeletePreviewResponse(posting_count=_posting_count_for_categories(ids_to_delete, session, user_id))


@router.delete("/categories/{category_id}")
def delete_category(
    category_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategoryDeleteResponse:
    """Delete a category (and, for a top-level one, every subcategory with it), uncategorizing its postings.

    Every posting currently carrying `category_id` (or one of its
    subcategories) as its own `category_id`/`subcategory_id` has that
    field cleared rather than left dangling — the same "uncategorized"
    state a posting that was never categorized at all is already in.
    Anything else referencing the deleted id(s) is cleared where the
    field is optional (`TransferRule`, `PostingSplitLeg`) or dropped
    entirely where it isn't (`Budget`, `GeneralBudget`, `CategoryPattern`
    all require a `category_id`) — see `store.uncategorize_category_ids`.

    Returns
    -------
    CategoryDeleteResponse

    Raises
    ------
    HTTPException
        404 if `category_id` doesn't exist.
    """
    store = load_store(session, user_id)
    if category_id not in store.categories:
        raise HTTPException(status_code=404, detail=f"Category {category_id!r} not found")

    ids_to_delete = category_ids_to_delete(store.categories, category_id)
    posting_count = _posting_count_for_categories(ids_to_delete, session, user_id)

    remaining_categories = {
        existing_id: category for existing_id, category in store.categories.items() if existing_id not in ids_to_delete
    }
    store = uncategorize_category_ids(store, ids_to_delete)
    store = store.model_copy(update={"categories": normalize_categories(remaining_categories)})

    # Every reference to a deleted category must be cleared *before* `save_store`
    # deletes that category row below — postings and manual overrides both
    # foreign-key into `categories`, so the delete would otherwise fail with a
    # constraint violation (same ordering `post_category_rename` needs).
    uncategorize_ledger_postings(ids_to_delete, session, user_id)

    def clear(field_id: str | None) -> str | None:
        return None if field_id in ids_to_delete else field_id

    overrides = load_overrides(session, user_id)
    overrides = {
        posting_id: override.model_copy(
            update={"category_id": clear(override.category_id), "subcategory_id": clear(override.subcategory_id)}
        )
        for posting_id, override in overrides.items()
    }
    save_overrides(overrides, session, user_id)

    save_store(store, session, user_id)
    return CategoryDeleteResponse(categories=store.categories, uncategorized_posting_count=posting_count)


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
        `budgets_to_delete` lists every `Budget`/`GeneralBudget` entry the
        merged-away category holds that the merge target already has one
        for, and which would therefore be discarded (see
        `store.remap_category_ids`).

    Raises
    ------
    HTTPException
        404 if `category_id` doesn't exist.
    """
    store = load_store(session, user_id)
    if category_id not in store.categories:
        raise HTTPException(status_code=404, detail=f"Category {category_id!r} not found")

    categories, id_remap = plan_category_rename(store.categories, category_id, name)
    target_id = id_remap.get(category_id)
    target_name = store.categories[target_id].name if target_id is not None else None

    updated = remap_category_ids(store.model_copy(update={"categories": categories}), id_remap)
    dropped_budget_ids = {budget.budget_id for budget in store.budgets} - {
        budget.budget_id for budget in updated.budgets
    }
    dropped_general_keys = set(store.general_budgets) - set(updated.general_budgets)
    budgets_to_delete = [
        BudgetToDeletePreview(month=budget.month, amount=budget.amount, currency=budget.currency)
        for budget in store.budgets
        if budget.budget_id in dropped_budget_ids
    ] + [
        BudgetToDeletePreview(
            month=None, amount=store.general_budgets[key].amount, currency=store.general_budgets[key].currency
        )
        for key in dropped_general_keys
    ]
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

    A merge repoints every reference to the merged-away id — postings
    already in the ledger cache, manual per-posting overrides, transfer
    rules, category patterns, budgets, and posting splits — onto the
    surviving id, then removes the merged-away category entirely. See
    `store.plan_category_rename` for the exact matching rules: a top-level
    category only merges into another top-level category of the same
    classification; a subcategory only merges into a sibling under the
    same parent. If the merge target already has a budget for a month the
    merged-away category also budgeted, the merged-away category's budget
    is discarded (see `store.remap_category_ids`) — call
    `GET /categories/{category_id}/rename-preview` first to warn about
    that before committing to the rename.

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
    store = load_store(session, user_id)
    if category_id not in store.categories:
        raise HTTPException(status_code=404, detail=f"Category {category_id!r} not found")

    categories, id_remap = plan_category_rename(store.categories, category_id, request.name)
    store = remap_category_ids(store.model_copy(update={"categories": categories}), id_remap)

    # Every reference to a merged-away category must be repointed *before* `save_store`
    # deletes that category row below — postings and manual overrides both foreign-key
    # into `categories`, so the delete would otherwise fail with a constraint violation.
    remap_ledger_category_ids(id_remap, session, user_id)
    if id_remap:

        def remap(category_id: str | None) -> str | None:
            """Look up `category_id`'s new id, or leave it unchanged if it wasn't merged away.

            Returns
            -------
            str or None
            """
            return id_remap.get(category_id, category_id) if category_id is not None else None

        overrides = load_overrides(session, user_id)
        overrides = {
            posting_id: override.model_copy(
                update={"category_id": remap(override.category_id), "subcategory_id": remap(override.subcategory_id)}
            )
            for posting_id, override in overrides.items()
        }
        save_overrides(overrides, session, user_id)

    save_store(store, session, user_id)

    return CategoryRenameResponse(categories=store.categories, merged=bool(id_remap))


@router.put("/tags")
def put_tags(
    tags: dict[str, Tag],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> dict[str, Tag]:
    """Replace the whole tag list.

    Returns
    -------
    dict[str, Tag]
        The tags just persisted, keyed by `tag_id`.
    """
    store = load_store(session, user_id)
    store = store.model_copy(update={"tags": tags})
    save_store(store, session, user_id)
    return store.tags


@router.post("/tags")
def post_tag(
    request: TagCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Tag:
    """Create a new tag, refusing a same-name (case-insensitive) duplicate.

    Unlike `put_tags` (a whole-list replace, where a client-computed id
    that happens to collide with an existing one silently overwrites it),
    this only ever adds a tag — a name collision is rejected outright
    rather than clobbering the existing entry.

    Returns
    -------
    Tag
        The tag just persisted, including its computed `tag_id`.

    Raises
    ------
    HTTPException
        409 if a tag with this name (case-insensitive) already exists.
    """
    store = load_store(session, user_id)
    normalized_name = request.name.strip().lower()
    collision = any(tag.name.strip().lower() == normalized_name for tag in store.tags.values())
    if collision:
        raise HTTPException(status_code=409, detail=f"A tag named {request.name!r} already exists")

    tag_id = f"tag:{slugify(request.name)}"
    new_tag = Tag(tag_id=tag_id, name=request.name)
    store = store.model_copy(update={"tags": {**store.tags, tag_id: new_tag}})
    save_store(store, session, user_id)
    return new_tag


@router.get("/tags/{tag_id}/rename-preview")
def get_tag_rename_preview(
    tag_id: str,
    name: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TagRenamePreviewResponse:
    """Report whether renaming `tag_id` to `name` would merge it into an existing tag.

    Calls the same pure `plan_tag_rename` `post_tag_rename` itself uses,
    but never persists anything — a caller can show a confirmation dialog
    first, and only actually call `POST /tags/{tag_id}/rename` once the
    user accepts.

    Returns
    -------
    TagRenamePreviewResponse
        `will_merge` is true if this rename would fold into an existing
        tag rather than just changing a name; `target_name` is that
        existing tag's name, or `None` when `will_merge` is false.

    Raises
    ------
    HTTPException
        404 if `tag_id` doesn't exist.
    """
    store = load_store(session, user_id)
    if tag_id not in store.tags:
        raise HTTPException(status_code=404, detail=f"Tag {tag_id!r} not found")

    _tags, id_remap = plan_tag_rename(store.tags, tag_id, name)
    target_id = id_remap.get(tag_id)
    target_name = store.tags[target_id].name if target_id is not None else None
    return TagRenamePreviewResponse(will_merge=target_id is not None, target_name=target_name)


@router.post("/tags/{tag_id}/rename")
def post_tag_rename(
    tag_id: str,
    request: TagRenameRequest,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TagRenameResponse:
    """Rename a tag, merging it into an existing same-named tag if there is one.

    A merge repoints every reference to the merged-away id — the
    `posting_tags` join table and every posting override's
    `tag_ids_override` array (see `store.remap_tag_ids`) — before the
    merged-away tag itself is deleted, so a foreign key never briefly
    points at a row about to disappear.

    Returns
    -------
    TagRenameResponse
        `tags` is the full tag map after the change; `merged` is true if
        this rename actually folded into an existing tag rather than just
        changing a name.

    Raises
    ------
    HTTPException
        404 if `tag_id` doesn't exist.
    """
    store = load_store(session, user_id)
    if tag_id not in store.tags:
        raise HTTPException(status_code=404, detail=f"Tag {tag_id!r} not found")

    tags, id_remap = plan_tag_rename(store.tags, tag_id, request.name)
    store = store.model_copy(update={"tags": tags})

    # Every reference to a merged-away tag must be repointed *before* `save_store`
    # deletes that tag row below — `posting_tags` foreign-keys into `tags`, so the
    # delete would otherwise fail with a constraint violation.
    remap_tag_ids(id_remap, session, user_id)
    save_store(store, session, user_id)

    return TagRenameResponse(tags=store.tags, merged=bool(id_remap))


@router.put("/transfer-rules")
def put_transfer_rules(
    rules: list[TransferRule],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[TransferRule]:
    """Replace the whole transfer-rule list.

    Returns
    -------
    list[TransferRule]
        The transfer rules just persisted.
    """
    store = load_store(session, user_id)
    store = store.model_copy(update={"rules": rules})
    save_store(store, session, user_id)
    return store.rules


@router.put("/category-patterns")
def put_category_patterns(
    category_patterns: dict[str, CategoryPattern],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> dict[str, CategoryPattern]:
    """Replace the whole category-pattern list — the description-match suggestion source, distinct from `TransferRule`.

    Returns
    -------
    dict[str, CategoryPattern]
        The patterns just persisted, keyed by `pattern_id`.
    """
    store = load_store(session, user_id)
    store = store.model_copy(update={"category_patterns": category_patterns})
    save_store(store, session, user_id)
    return store.category_patterns


@router.put("/other-assets")
def put_other_assets(
    other_assets: list[OtherAsset],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[OtherAsset]:
    """Replace the whole manually-entered-asset list.

    Returns
    -------
    list[OtherAsset]
        The assets just persisted.
    """
    store = load_store(session, user_id)
    store = store.model_copy(update={"other_assets": other_assets})
    save_store(store, session, user_id)
    return store.other_assets


@router.put("/budgets")
def put_budgets(
    budgets: list[Budget],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[Budget]:
    """Replace the whole budget list, across every month.

    Returns
    -------
    list[Budget]
        The budgets just persisted.
    """
    store = load_store(session, user_id)
    store = store.model_copy(update={"budgets": budgets})
    save_store(store, session, user_id)
    return store.budgets


@router.put("/general-budgets")
def put_general_budgets(
    general_budgets: dict[str, GeneralBudget],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> dict[str, GeneralBudget]:
    """Replace the whole general-budget map, keyed by `category_id` — the same amount applies to every month.

    Stored, edited, and displayed completely separately from `Budget`'s
    per-month rows (see `models.GeneralBudget`); this never falls back to
    or overwrites a per-month budget, or vice versa.

    Returns
    -------
    dict[str, GeneralBudget]
        The general budgets just persisted.
    """
    store = load_store(session, user_id)
    store = store.model_copy(update={"general_budgets": general_budgets})
    save_store(store, session, user_id)
    return store.general_budgets


@router.put("/simulator/scenarios")
def put_simulator_scenarios(
    scenarios: list[SimulatorScenario],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[SimulatorScenario]:
    """Replace the whole saved-scenario list.

    Returns
    -------
    list[SimulatorScenario]
        The scenarios just persisted.
    """
    store = load_store(session, user_id)
    store = store.model_copy(update={"simulator_scenarios": scenarios})
    save_store(store, session, user_id)
    return store.simulator_scenarios


@router.post("/accounts")
def post_account(
    account: AccountCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Account:
    """Register a new account, generating its id.

    Returns
    -------
    Account
        The account just persisted, including its newly-generated `account_id`.
    """
    store = load_store(session, user_id)
    new_account = Account(
        account_id=uuid.uuid4().hex,
        name=account.name,
        kind=account.kind,
        institution=account.institution,
        currency=account.currency,
        last_four=account.last_four,
        parent_account_id=account.parent_account_id,
        external_ref=account.external_ref,
        meta=account.meta,
    )
    store = store.model_copy(update={"accounts": {**store.accounts, new_account.account_id: new_account}})
    save_store(store, session, user_id)
    return new_account


@router.put("/accounts/{account_id}")
def put_account(
    account_id: str,
    update: AccountUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Account:
    """Update an account — full edit if it has no postings yet, name/meta-only afterward.

    Returns
    -------
    Account
        The account after the update.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist; 400 if institution/kind/currency
        changed on an account that already has postings.
    """
    store = load_store(session, user_id)
    existing = store.accounts.get(account_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")

    locked_fields_changed = (
        update.institution != existing.institution
        or update.kind != existing.kind
        or update.currency != existing.currency
    )
    if locked_fields_changed and _account_has_postings(account_id, state.config, session, user_id):
        raise HTTPException(
            status_code=400,
            detail="This account already has transactions — only its display name and meta can be edited",
        )

    updated = existing.model_copy(
        update={
            "name": update.name,
            "institution": update.institution,
            "kind": update.kind,
            "currency": update.currency,
            "last_four": update.last_four,
            "external_ref": update.external_ref,
            "meta": update.meta,
        }
    )
    store = store.model_copy(update={"accounts": {**store.accounts, account_id: updated}})
    save_store(store, session, user_id)
    return updated


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> AccountIdResponse:
    """Delete an account, as long as it has no postings yet.

    Returns
    -------
    AccountIdResponse
        The account just deleted.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist; 400 if it already has postings.
    """
    store = load_store(session, user_id)
    if account_id not in store.accounts:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    if _account_has_postings(account_id, state.config, session, user_id):
        raise HTTPException(status_code=400, detail="This account already has transactions and can't be deleted")
    remaining = {aid: account for aid, account in store.accounts.items() if aid != account_id}
    store = store.model_copy(update={"accounts": remaining})
    save_store(store, session, user_id)
    return AccountIdResponse(account_id=account_id)


@router.post("/accounts/{account_id}/close")
def close_account(
    account_id: str,
    request: AccountCloseRequest,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> AccountCloseResponse:
    """Mark an account closed, recording any transfers that moved its remaining balance out first.

    An account no longer open at its institution still keeps its full
    transaction history (see `models.Account.closed`) — closing never
    deletes anything. `request.transfers` (each a `models.ManualTransfer`)
    become real postings on both the closed account and wherever its
    balance went, the one case in this ledger where a posting doesn't
    trace back to an imported statement.

    Returns
    -------
    AccountCloseResponse
        The account and every manual transfer after the update.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist; 400 if a transfer doesn't move
        money out of `account_id`, or names an unknown `to_account_id`.
    """
    store = load_store(session, user_id)
    account = store.accounts.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    for transfer in request.transfers:
        if transfer.from_account_id != account_id:
            raise HTTPException(status_code=400, detail="Each transfer must move money out of the account being closed")
        if transfer.to_account_id not in store.accounts:
            raise HTTPException(status_code=400, detail=f"Account {transfer.to_account_id!r} not found")

    updated_account = account.model_copy(update={"closed": True})
    store = store.model_copy(
        update={
            "accounts": {**store.accounts, account_id: updated_account},
            "manual_transfers": [*store.manual_transfers, *request.transfers],
        }
    )
    save_store(store, session, user_id)
    return AccountCloseResponse(account=updated_account, manual_transfers=store.manual_transfers)


@router.post("/accounts/{account_id}/reopen")
def reopen_account(
    account_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Account:
    """Clear an account's `closed` flag, without touching any transfers recorded when it was closed.

    Returns
    -------
    Account
        The account after the update.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist.
    """
    store = load_store(session, user_id)
    account = store.accounts.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    updated_account = account.model_copy(update={"closed": False})
    store = store.model_copy(update={"accounts": {**store.accounts, account_id: updated_account}})
    save_store(store, session, user_id)
    return updated_account


@router.put("/accounts/{account_id}/opening-balance")
def put_opening_balance(
    account_id: str,
    opening_balance: OpeningBalance,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> OpeningBalance:
    """Set the balance an account already held the day before its first posting.

    Returns
    -------
    OpeningBalance
        The opening balance just persisted.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist; 400 if `opening_balance.account_id` doesn't match the path.
    """
    store = load_store(session, user_id)
    if account_id not in store.accounts:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    if opening_balance.account_id != account_id:
        raise HTTPException(status_code=400, detail="account_id in the body must match the URL")
    store = store.model_copy(update={"opening_balances": {**store.opening_balances, account_id: opening_balance}})
    save_store(store, session, user_id)
    return opening_balance


@router.delete("/accounts/{account_id}/opening-balance")
def delete_opening_balance(
    account_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> AccountIdResponse:
    """Remove an account's opening balance, if it has one.

    Returns
    -------
    AccountIdResponse
        The account whose opening balance was cleared.
    """
    store = load_store(session, user_id)
    remaining = {aid: value for aid, value in store.opening_balances.items() if aid != account_id}
    store = store.model_copy(update={"opening_balances": remaining})
    save_store(store, session, user_id)
    return AccountIdResponse(account_id=account_id)
