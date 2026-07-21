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
    BudgetIdResponse,
    BudgetToDeletePreview,
    BudgetUpsert,
    CategoryCreate,
    CategoryDeletePreviewResponse,
    CategoryDeleteResponse,
    CategoryPatternCreate,
    CategoryPatternIdResponse,
    CategoryPatternUpdate,
    CategoryRenamePreviewResponse,
    CategoryRenameRequest,
    CategoryRenameResponse,
    GeneralBudgetKeyResponse,
    GeneralBudgetUpsert,
    OtherAssetCreate,
    SimulatorScenarioCreate,
    SubcategoryCreate,
    TagCreate,
    TagRenamePreviewResponse,
    TagRenameRequest,
    TagRenameResponse,
    TransferRuleCreate,
    TransferRuleIdResponse,
    TransferRuleUpdate,
)
from accounting.api.dependencies import _account_has_postings, _resolved_postings_and_store, state
from accounting.importers.common import row_hash
from accounting.importers.ingest import load_ledger, remap_ledger_category_ids, uncategorize_ledger_postings
from accounting.ledger.transfers import reconcile_and_persist_rule_links
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
    delete_category_pattern,
    delete_transfer_rule,
    get_store_version,
    load_overrides,
    load_store,
    normalize_categories,
    plan_category_rename,
    plan_tag_rename,
    remap_category_ids,
    remap_tag_ids,
    save_overrides_for_postings,
    save_store,
    slugify,
    uncategorize_category_ids,
    update_category_pattern,
    update_transfer_rule,
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
        transfer_links=store.transfer_links,
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

    # Only the overrides that actually reference a deleted category/subcategory get touched — every
    # other posting's override is left alone, unlike the old `load_overrides`/`save_overrides(whole
    # dict)` pair this replaced, which rewrote the entire table on every category delete.
    overrides = load_overrides(session, user_id)
    changed_overrides = {
        posting_id: override.model_copy(
            update={"category_id": clear(override.category_id), "subcategory_id": clear(override.subcategory_id)}
        )
        for posting_id, override in overrides.items()
        if override.category_id in ids_to_delete or override.subcategory_id in ids_to_delete
    }
    save_overrides_for_postings(list(changed_overrides.keys()), changed_overrides, session, user_id)

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
    `posting_tags` and `posting_override_tags` join tables (see
    `store.remap_tag_ids`) — before the merged-away tag itself is
    deleted, so a foreign key never briefly points at a row about to
    disappear.

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


def _transfer_rule_id(description_contains: str, account_id: str | None, counterparty_account_id: str | None) -> str:
    """Derive a transfer rule's natural key from its own matching criteria.

    Returns
    -------
    str
    """
    return f"rule:{row_hash(description_contains, account_id or '', counterparty_account_id or '')}"


@router.post("/transfer-rules")
def post_transfer_rule(
    request: TransferRuleCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TransferRule:
    """Create one new transfer rule, without touching any other rule already saved.

    Posting this again for the same
    `(description_contains, account_id, counterparty_account_id)` replaces
    that rule (its `priority`/`description` update in place) rather than
    creating a duplicate — see `PATCH /transfer-rules/{rule_id}` instead
    for editing an existing rule by id, which never risks that ambiguity.

    Returns
    -------
    TransferRule
        The rule just persisted.
    """
    rule = TransferRule(
        rule_id=_transfer_rule_id(request.description_contains, request.account_id, request.counterparty_account_id),
        description_contains=request.description_contains,
        account_id=request.account_id,
        counterparty_account_id=request.counterparty_account_id,
        priority=request.priority,
        description=request.description,
    )
    store = load_store(session, user_id)
    remaining = [r for r in store.rules if r.rule_id != rule.rule_id]
    store = store.model_copy(update={"rules": [*remaining, rule]})
    raw_ledger = load_ledger(session, user_id)
    save_store(store, session, user_id)
    reconcile_and_persist_rule_links(raw_ledger, session, user_id)
    return rule


@router.patch("/transfer-rules/{rule_id}")
def patch_transfer_rule(
    rule_id: str,
    request: TransferRuleUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TransferRule:
    """Update one existing transfer rule in place, without touching any other rule already saved.

    A true per-resource write — unlike `POST /transfer-rules`, this
    never round-trips through `load_store`/`save_store` (which deletes and
    reinserts every persisted entity for the user); see
    `accounting.store.update_transfer_rule`. Guarded by
    `request.expected_version` instead of the whole-store
    `X-Expected-Store-Version` header, so an edit to this one rule can
    never spuriously conflict with — or be silently overwritten by — an
    unrelated save elsewhere in the store.

    Returns
    -------
    TransferRule
        The rule as persisted after the update.

    Raises
    ------
    HTTPException
        404 if no rule with `rule_id` exists.
    """
    raw_ledger = load_ledger(session, user_id)
    rule = TransferRule(
        rule_id=rule_id,
        description_contains=request.description_contains,
        account_id=request.account_id,
        counterparty_account_id=request.counterparty_account_id,
        priority=request.priority,
        description=request.description,
        active=request.active,
        excluded_transaction_ids=request.excluded_transaction_ids,
    )
    updated = update_transfer_rule(session, user_id, rule, request.expected_version)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Transfer rule {rule_id!r} not found")
    session.commit()
    reconcile_and_persist_rule_links(raw_ledger, session, user_id)
    return updated


@router.delete("/transfer-rules/{rule_id}")
def delete_transfer_rule_route(
    rule_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TransferRuleIdResponse:
    """Delete one transfer rule, without touching any other rule already saved.

    No version check — see `accounting.store.delete_transfer_rule`'s own
    docstring for why deleting an already-gone rule is a plain 404, not a
    409: there's nothing left to conflict with.

    Returns
    -------
    RuleIdResponse
        The rule id just deleted.

    Raises
    ------
    HTTPException
        404 if no rule with `rule_id` exists.
    """
    raw_ledger = load_ledger(session, user_id)
    deleted = delete_transfer_rule(session, user_id, rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Transfer rule {rule_id!r} not found")
    session.commit()
    reconcile_and_persist_rule_links(raw_ledger, session, user_id)
    return TransferRuleIdResponse(rule_id=rule_id)


def _category_pattern_id(description_contains: str, category_id: str, subcategory_id: str | None) -> str:
    """Derive a category pattern's natural key from its own matching criteria.

    Returns
    -------
    str
    """
    return f"pattern:{row_hash(description_contains, category_id, subcategory_id or '')}"


@router.post("/category-patterns")
def post_category_pattern(
    request: CategoryPatternCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategoryPattern:
    """Create one new category pattern, without touching any other pattern already saved.

    Posting this again for the same `(description_contains, category_id,
    subcategory_id)` replaces that pattern rather than creating a duplicate.

    Returns
    -------
    CategoryPattern
        The pattern just persisted.
    """
    pattern = CategoryPattern(
        pattern_id=_category_pattern_id(request.description_contains, request.category_id, request.subcategory_id),
        description_contains=request.description_contains,
        category_id=request.category_id,
        subcategory_id=request.subcategory_id,
        priority=request.priority,
    )
    store = load_store(session, user_id)
    store = store.model_copy(update={"category_patterns": {**store.category_patterns, pattern.pattern_id: pattern}})
    save_store(store, session, user_id)
    return pattern


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


@router.patch("/category-patterns/{pattern_id}")
def patch_category_pattern(
    pattern_id: str,
    request: CategoryPatternUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategoryPattern:
    """Update one existing category pattern in place, without touching any other pattern already saved.

    A true per-resource write — see `accounting.store.update_category_pattern`. Guarded by
    `request.expected_version` instead of the whole-store `X-Expected-Store-Version` header.

    Returns
    -------
    CategoryPattern
        The pattern as persisted after the update.

    Raises
    ------
    HTTPException
        404 if no pattern with `pattern_id` exists.
    """
    pattern = CategoryPattern(
        pattern_id=pattern_id,
        description_contains=request.description_contains,
        category_id=request.category_id,
        subcategory_id=request.subcategory_id,
        priority=request.priority,
        active=request.active,
    )
    updated = update_category_pattern(session, user_id, pattern, request.expected_version)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Category pattern {pattern_id!r} not found")
    session.commit()
    return updated


@router.delete("/category-patterns/{pattern_id}")
def delete_category_pattern_route(
    pattern_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategoryPatternIdResponse:
    """Delete one category pattern, without touching any other pattern already saved.

    No version check — see `accounting.store.delete_category_pattern`.

    Returns
    -------
    CategoryPatternIdResponse
        The pattern id just deleted.

    Raises
    ------
    HTTPException
        404 if no pattern with `pattern_id` exists.
    """
    deleted = delete_category_pattern(session, user_id, pattern_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Category pattern {pattern_id!r} not found")
    session.commit()
    return CategoryPatternIdResponse(pattern_id=pattern_id)


@router.post("/other-assets")
def post_other_asset(
    request: OtherAssetCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> OtherAsset:
    """Create one new manually-entered asset, without touching any other asset already saved.

    `asset_id` is server-minted — two assets can validly share every
    other field (e.g. two rental properties both named "Rental"), so
    there's no natural key two "the same" asset would collide on.

    Returns
    -------
    OtherAsset
        The asset just persisted.
    """
    asset = OtherAsset(
        asset_id=f"asset:{uuid.uuid4().hex}",
        name=request.name,
        value=request.value,
        currency=request.currency,
        note=request.note,
    )
    store = load_store(session, user_id)
    store = store.model_copy(update={"other_assets": [*store.other_assets, asset]})
    save_store(store, session, user_id)
    return asset


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


def _budget_id(month: str, category_id: str, subcategory_id: str | None) -> str:
    """Derive the natural key one `(month, category_id, subcategory_id)` tuple always maps to.

    Returns
    -------
    str
    """
    return f"{month}:{category_id}:{subcategory_id}" if subcategory_id is not None else f"{month}:{category_id}"


@router.post("/budgets")
def post_budget(
    request: BudgetUpsert,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Budget:
    """Set one month's spending target for one category (or subcategory), replacing any prior target for it.

    Unlike `PUT /budgets`, only the one budget in the request body is
    sent or touched — every other month/category's target is left alone,
    so editing one cell in the budget grid no longer means re-sending
    every budget the user has ever set.

    Returns
    -------
    Budget
        The budget just persisted.
    """
    budget = Budget(
        budget_id=_budget_id(request.month, request.category_id, request.subcategory_id),
        month=request.month,
        category_id=request.category_id,
        subcategory_id=request.subcategory_id,
        amount=request.amount,
        currency=request.currency,
    )
    store = load_store(session, user_id)
    remaining = [b for b in store.budgets if b.budget_id != budget.budget_id]
    store = store.model_copy(update={"budgets": [*remaining, budget]})
    save_store(store, session, user_id)
    return budget


@router.delete("/budgets/{budget_id}")
def delete_budget(
    budget_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> BudgetIdResponse:
    """Remove one month's target for one category.

    Returns
    -------
    BudgetIdResponse
        The id just removed.

    Raises
    ------
    HTTPException
        404 if no budget has this id.
    """
    store = load_store(session, user_id)
    if not any(b.budget_id == budget_id for b in store.budgets):
        raise HTTPException(status_code=404, detail=f"Budget {budget_id!r} not found")
    remaining = [b for b in store.budgets if b.budget_id != budget_id]
    store = store.model_copy(update={"budgets": remaining})
    save_store(store, session, user_id)
    return BudgetIdResponse(budget_id=budget_id)


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


def _general_budget_key(category_id: str, subcategory_id: str | None) -> str:
    """Which of `category_id`/`subcategory_id` a general budget is keyed by — whichever is more specific.

    Returns
    -------
    str
    """
    return subcategory_id if subcategory_id is not None else category_id


@router.post("/general-budgets")
def post_general_budget(
    request: GeneralBudgetUpsert,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GeneralBudget:
    """Set one category's (or subcategory's) standing target, replacing any prior one for it.

    Unlike `PUT /general-budgets`, only the one entry in the request body
    is sent or touched.

    Returns
    -------
    GeneralBudget
        The general budget just persisted.
    """
    key = _general_budget_key(request.category_id, request.subcategory_id)
    general_budget = GeneralBudget(
        category_id=request.category_id,
        subcategory_id=request.subcategory_id,
        amount=request.amount,
        currency=request.currency,
    )
    store = load_store(session, user_id)
    store = store.model_copy(update={"general_budgets": {**store.general_budgets, key: general_budget}})
    save_store(store, session, user_id)
    return general_budget


@router.delete("/general-budgets/{key}")
def delete_general_budget(
    key: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> GeneralBudgetKeyResponse:
    """Remove one category's (or subcategory's) standing target.

    Returns
    -------
    GeneralBudgetKeyResponse
        The key just removed.

    Raises
    ------
    HTTPException
        404 if no general budget has this key.
    """
    store = load_store(session, user_id)
    if key not in store.general_budgets:
        raise HTTPException(status_code=404, detail=f"General budget {key!r} not found")
    remaining = {k: v for k, v in store.general_budgets.items() if k != key}
    store = store.model_copy(update={"general_budgets": remaining})
    save_store(store, session, user_id)
    return GeneralBudgetKeyResponse(key=key)


@router.post("/simulator/scenarios")
def post_simulator_scenario(
    request: SimulatorScenarioCreate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> SimulatorScenario:
    """Create one new saved scenario, without touching any other scenario already saved.

    `scenario_id` is server-minted — two scenarios can validly share
    every input field (comparing "what if I ran this exact case twice"),
    so there's no natural key two "the same" scenario would collide on.

    Returns
    -------
    SimulatorScenario
        The scenario just persisted.
    """
    scenario = SimulatorScenario(
        scenario_id=f"scenario:{uuid.uuid4().hex}",
        name=request.name,
        initial_capital=request.initial_capital,
        monthly_contribution=request.monthly_contribution,
        horizon_years=request.horizon_years,
        annual_rate_pct=request.annual_rate_pct,
        compounding_frequency=request.compounding_frequency,
        currency=request.currency,
    )
    store = load_store(session, user_id)
    store = store.model_copy(update={"simulator_scenarios": [*store.simulator_scenarios, scenario]})
    save_store(store, session, user_id)
    return scenario


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
