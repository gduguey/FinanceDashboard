"""Simulator-scenario endpoints — create, replace and delete the saved compound-growth scenarios."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from accounting.api.api_models import SimulatorScenarioCreate
from accounting.models import SimulatorScenario
from accounting.repositories.taxonomy import (
    delete_simulator_scenario,
    insert_simulator_scenario,
    replace_simulator_scenarios,
)
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()


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
    insert_simulator_scenario(session, user_id, scenario)
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
    replace_simulator_scenarios(session, user_id, scenarios)
    session.commit()
    return scenarios


@router.delete("/simulator/scenarios/{scenario_id}", status_code=204)
def delete_simulator_scenario_route(
    scenario_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Delete one saved simulator scenario, without touching any other. Idempotent, no version check.

    Replaces deleting a scenario by re-sending the whole list minus one;
    see `repositories.taxonomy.delete_simulator_scenario`.


    Raises
    ------
    HTTPException
        404 if no scenario with `scenario_id` exists.
    """
    if not delete_simulator_scenario(session, user_id, scenario_id):
        raise HTTPException(status_code=404, detail=f"Simulator scenario {scenario_id!r} not found")
    session.commit()
