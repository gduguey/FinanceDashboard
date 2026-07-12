"""Persisted-entity CRUD — mirrors `accounting.store`: accounts, categories, tags, rules, budgets, other assets."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    AccountCloseRequest,
    AccountCloseResponse,
    AccountIdResponse,
    AccountingStoreResponse,
    AccountUpdate,
    CategoryRenameRequest,
    CategoryRenameResponse,
)
from accounting.api.dependencies import _account_has_postings, _resolved_postings_and_store, state
from accounting.importers.ingest import remap_ledger_category_ids
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
    load_overrides,
    load_store,
    normalize_categories,
    plan_category_rename,
    remap_category_ids,
    save_overrides,
    save_store,
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
        keyed by id), `transfer_rules`, `other_assets`, `budgets` (each a list).
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
    same parent.

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
    try:
        store = remap_category_ids(store.model_copy(update={"categories": categories}), id_remap)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    # Every reference to a merged-away category must be repointed *before* `save_store`
    # deletes that category row below — postings and manual overrides both foreign-key
    # into `categories`, so the delete would otherwise fail with a constraint violation.
    remap_ledger_category_ids(id_remap, session, user_id)
    if id_remap:

        def remap(category_id: str | None) -> str | None:
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
    account: Account,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Account:
    """Register a new account.

    Returns
    -------
    Account
        The account just persisted.

    Raises
    ------
    HTTPException
        409 if an account with this id already exists.
    """
    store = load_store(session, user_id)
    if account.account_id in store.accounts:
        raise HTTPException(status_code=409, detail=f"Account {account.account_id!r} already exists")
    store = store.model_copy(update={"accounts": {**store.accounts, account.account_id: account}})
    save_store(store, session, user_id)
    return account


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
