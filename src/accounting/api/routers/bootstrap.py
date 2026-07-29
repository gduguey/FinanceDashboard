"""Store-bootstrap endpoints — the whole persisted store in one read, plus the currencies it can hold."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from accounting.api.api_models import AccountingStoreResponse
from accounting.models import SUPPORTED_CURRENCIES, Currency
from accounting.repositories.interpretation import (
    load_category_patterns,
    load_transfer_links,
    load_transfer_rules,
)
from accounting.repositories.planning import (
    load_budgets,
    load_goal_automations,
    load_goal_contributions,
    load_goals,
)
from accounting.repositories.taxonomy import load_other_assets, load_simulator_scenarios, load_tags
from accounting.taxonomy import seeded_accounts, seeded_categories
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
        `accounts`, `categories`, `tags`, `category_patterns`, `goals`
        and `goal_contributions` (each a dict keyed by id), plus
        `transfer_rules`, `other_assets`, `budgets`,
        `simulator_scenarios`, `transfer_links` and `goal_automations`
        (each a list). No store-wide version: optimistic concurrency is
        per-row (`goals`, `transfer_rules`, `category_patterns` each
        carry their own `version`), so there is nothing store-wide to
        echo back. Opening balances, manual transfers, posting splits
        and posting merges are deliberately absent — see
        `AccountingStoreResponse` for why.
    """
    return AccountingStoreResponse(
        accounts=seeded_accounts(session, user_id),
        categories=seeded_categories(session, user_id),
        tags=load_tags(session, user_id),
        transfer_rules=load_transfer_rules(session, user_id),
        other_assets=load_other_assets(session, user_id),
        budgets=load_budgets(session, user_id),
        simulator_scenarios=load_simulator_scenarios(session, user_id),
        transfer_links=load_transfer_links(session, user_id),
        category_patterns=load_category_patterns(session, user_id),
        goals=load_goals(session, user_id),
        goal_contributions=load_goal_contributions(session, user_id),
        goal_automations=load_goal_automations(session, user_id),
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
