"""Budget endpoints — set, replace and remove the per-month and general spending targets for a category."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from accounting.api.api_models import BudgetIdResponse, BudgetUpsert
from accounting.models import Budget
from accounting.repositories.planning import budget_row_key, remove_budget, replace_budgets, upsert_budget
from accounting.taxonomy import seed_new_user_defaults
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


@router.delete("/budgets/{budget_id}")
def delete_budget(
    budget_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> BudgetIdResponse:
    """Remove one target — a month's, or the general one — for one category.

    Returns
    -------
    BudgetIdResponse
        The id just removed.

    Raises
    ------
    HTTPException
        404 if no budget has this id.
    """
    if not remove_budget(session, user_id, budget_id):
        raise HTTPException(status_code=404, detail=f"Budget {budget_id!r} not found")
    session.commit()
    return BudgetIdResponse(budget_id=budget_id)
