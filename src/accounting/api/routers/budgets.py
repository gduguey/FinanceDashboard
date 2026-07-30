"""Budget endpoints — read, set, replace and remove the per-month and general spending targets for a category."""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from accounting.api.api_models import BudgetComparisonRow, BudgetUpsert, SuggestedBudgetAmount
from accounting.api.dependencies import (
    _currencies_in_use,
    _display_currency,
    _resolved_postings_for_aggregation,
)
from accounting.dashboard import budgets
from accounting.models import Budget, CurrencyCode
from accounting.repositories.planning import (
    budget_row_key,
    load_budgets,
    remove_budget,
    replace_budgets,
    upsert_budget,
)
from accounting.repositories.taxonomy import load_categories
from accounting.taxonomy import seed_new_user_defaults, seeded_accounts
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()


@router.put("/budgets")
def put_budgets(
    budgets: list[Budget],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[Budget]:
    """Replace the whole budget list — every month's targets plus the general, every-month-alike ones.

    Returns
    -------
    list[Budget]
        The budgets just persisted.
    """
    seed_new_user_defaults(session, user_id)
    replace_budgets(session, user_id, budgets)
    session.commit()
    return budgets


@router.post("/budgets")
def post_budget(
    request: BudgetUpsert,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Budget:
    """Set one spending target for one category (or subcategory), replacing any prior target for it.

    `request.month` picks which target: a `"YYYY-MM"` sets that month's,
    and omitting it sets the general, every-month-alike one. The two are
    separate rows, so setting one never overwrites the other.

    Unlike `PUT /budgets`, only the one budget in the request body is
    sent or touched — every other month/category's target is left alone,
    so editing one cell in the budget grid no longer means re-sending
    every budget the user has ever set.

    Returns
    -------
    Budget
        The budget just persisted.
    """
    # The default category tree this budget's `category_id` foreign-keys into has to exist first;
    # a no-op read for everyone but a brand-new user.
    seed_new_user_defaults(session, user_id)
    budget = Budget(
        budget_id=budget_row_key(request.month, request.category_id, request.subcategory_id),
        month=request.month,
        category_id=request.category_id,
        subcategory_id=request.subcategory_id,
        amount=request.amount,
        currency=request.currency,
    )
    upsert_budget(budget, session, user_id)
    return budget


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
