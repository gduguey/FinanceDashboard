"""Budget endpoints — read, set and remove the per-month and general spending targets for a category."""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from accounting.api.api_models import BudgetComparisonRow, BudgetUpsert, SuggestedBudgetAmount
from accounting.api.dependencies import (
    _currencies_in_use,
    _display_currency,
    _resolved_postings_for_aggregation,
)
from accounting.api.entities import Budget
from accounting.api.locations import created_or_replaced, location_of
from accounting.dashboard import budgets
from accounting.models import Budget as DomainBudget
from accounting.models import CurrencyCode
from accounting.repositories.planning import (
    budget_row_key,
    load_budgets,
    remove_budget,
    upsert_budget,
)
from accounting.repositories.taxonomy import load_categories
from accounting.taxonomy import seed_new_user_defaults, seeded_accounts
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()


@router.post("/budgets", status_code=201, responses=created_or_replaced(Budget))
def post_budget(
    request: BudgetUpsert,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Budget:
    """Set one spending target for one category (or subcategory), replacing any prior target for it.

    `request.month` picks which target: a `"YYYY-MM"` sets that month's,
    and omitting it sets the general, every-month-alike one. The two are
    separate rows, so setting one never overwrites the other.

    Only the one budget in the request body is sent or touched — every
    other month/category's target is left alone, so editing one cell in
    the budget grid no longer means re-sending every budget the user has
    ever set, the way the retired whole-list `PUT /budgets` did.

    A genuine upsert, so the status distinguishes its two outcomes: `201`
    with a `Location` when this call brought the cell into existence,
    `200` when it replaced a target already set. `upsert_budget` reports
    which happened out of the `INSERT ... ON CONFLICT` itself.

    Stays a `POST` on the collection rather than becoming
    `PUT /budgets/{budget_id}`, even though the id *is* derived from the
    body's `(month, category_id, subcategory_id)`: that derivation is
    `repositories.planning.budget_row_key`, server code. Addressing the
    cell directly would mean every client reimplementing that key format
    and breaking silently the day it changes, which is a worse contract
    than a `POST` that reports honestly which of the two things it did.

    Returns
    -------
    Budget
        The budget just persisted.
    """
    # The default category tree this budget's `category_id` foreign-keys into has to exist first;
    # a no-op read for everyone but a brand-new user.
    seed_new_user_defaults(session, user_id)
    budget = DomainBudget(
        budget_id=budget_row_key(request.month, request.category_id, request.subcategory_id),
        month=request.month,
        category_id=request.category_id,
        subcategory_id=request.subcategory_id,
        amount=request.amount,
        currency=request.currency,
    )
    if upsert_budget(budget, session, user_id):
        location_of(http_request, response, "get_budget", budget_id=budget.budget_id)
    else:
        response.status_code = 200
    return Budget.from_domain(budget)


@router.delete("/budgets/{budget_id}", status_code=204)
def delete_budget(
    budget_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Remove one target — a month's, or the general one — for one category.

    Raises
    ------
    HTTPException
        404 if no budget has this id.
    """
    if not remove_budget(session, user_id, budget_id):
        raise HTTPException(status_code=404, detail=f"Budget {budget_id!r} not found")
    session.commit()


@router.get("/budgets/comparison")
def get_budget_comparison(
    month: str,
    *,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[BudgetComparisonRow]:
    """Every category budgeted for one month, actual spend next to the target.

    Returns
    -------
    list[BudgetComparisonRow]

    Raises
    ------
    HTTPException
        400 if `month` isn't `"YYYY-MM"`.
    """
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM form")
    since, until = budgets.month_bounds(month)
    postings = _resolved_postings_for_aggregation(session, user_id, since=since, until=until)
    rows = budgets.budget_comparison(
        postings,
        seeded_accounts(session, user_id),
        load_categories(session, user_id),
        load_budgets(session, user_id),
        month,
        _display_currency(display_currency, _currencies_in_use(session, user_id)),
    )
    return [BudgetComparisonRow(**vars(row)) for row in rows]


@router.get("/budgets/suggested-amount")
def get_suggested_budget_amount(
    category_id: str,
    month: str,
    *,
    lookback_months: Annotated[int, Query(ge=1)] = 3,
    subcategory_id: str | None = None,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> SuggestedBudgetAmount:
    """Suggest a budget for a category (or one subcategory of it) from its trailing months' actual spend.

    Returns
    -------
    SuggestedBudgetAmount

    Raises
    ------
    HTTPException
        400 if `month` isn't `"YYYY-MM"`.
    """
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM form")
    window = budgets.suggested_budget_amount_window(month, lookback_months)
    since, until = window if window is not None else (None, None)
    postings = _resolved_postings_for_aggregation(session, user_id, since=since, until=until)
    amount = budgets.suggested_budget_amount(
        postings,
        seeded_accounts(session, user_id),
        category_id,
        month,
        lookback_months,
        subcategory_id,
        _display_currency(display_currency, _currencies_in_use(session, user_id)),
    )
    return SuggestedBudgetAmount(suggested_amount=amount)


# Last in this module on purpose, and the two routes above are why: Starlette
# matches in registration order, and `/budgets/comparison` and
# `/budgets/suggested-amount` both match `/budgets/{budget_id}` on a GET.
# Declared before them, this route would answer 404 for both. Any future
# `GET /budgets/<literal>` has to go above here too.
@router.get("/budgets/{budget_id}")
def get_budget(
    budget_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Budget:
    """Return one spending target by id — the address `post_budget` advertises when it creates one.

    Returns
    -------
    Budget

    Raises
    ------
    HTTPException
        404 if no budget has this id.
    """
    budget = next((b for b in load_budgets(session, user_id) if b.budget_id == budget_id), None)
    if budget is None:
        raise HTTPException(status_code=404, detail=f"Budget {budget_id!r} not found")
    return Budget.from_domain(budget)
