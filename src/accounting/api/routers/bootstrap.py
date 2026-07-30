"""Store-bootstrap endpoints — the whole persisted store in one read, plus the currencies it can hold.

`GET /store` is a **composite** on purpose: one round trip that recomposes
twelve `load_*` calls so the SPA has everything it needs to render before the
user's first interaction. The API-design audit's finding against it (F4) is
answered rather than accepted, on two counts:

- Its stated defect — that the route "returns the entire persisted
  `AccountingStore`" — is factually dead. There is no such class; the whole-store
  load/mutate/save cycle went when `accounting.repositories` split into
  aggregates, and this route is a router-level recomposition of independent
  reads with no type behind it (see `AccountingStoreResponse`'s own docstring).
- Splitting it into twelve collection GETs would turn one boot request into
  twelve, which is a performance regression dressed as a design fix. Whether the
  SPA should fetch per-resource instead belongs with the work that measures the
  boot path, not before it.

The per-resource collection GETs are the right thing to *add* — a client that
wants one collection should not have to read all twelve — and they are what an
item-level `Location` header needs to point at. Adding them does not require
deleting this route.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from accounting.api.api_models import AccountingStoreResponse
from accounting.api.entities import (
    Account,
    Category,
    CategoryPattern,
    Currency,
    Tag,
    TransferLink,
    TransferRule,
)
from accounting.models import SUPPORTED_CURRENCIES
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
        accounts={
            account_id: Account.from_domain(account)
            for account_id, account in seeded_accounts(session, user_id).items()
        },
        categories={
            category_id: Category.from_domain(category)
            for category_id, category in seeded_categories(session, user_id).items()
        },
        tags={tag_id: Tag.from_domain(tag) for tag_id, tag in load_tags(session, user_id).items()},
        transfer_rules=[TransferRule.from_domain(rule) for rule in load_transfer_rules(session, user_id)],
        other_assets=load_other_assets(session, user_id),
        budgets=load_budgets(session, user_id),
        simulator_scenarios=load_simulator_scenarios(session, user_id),
        transfer_links=[TransferLink.from_domain(link) for link in load_transfer_links(session, user_id)],
        category_patterns={
            pattern_id: CategoryPattern.from_domain(pattern)
            for pattern_id, pattern in load_category_patterns(session, user_id).items()
        },
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
    return [Currency.from_domain(currency) for currency in SUPPORTED_CURRENCIES.values()]
