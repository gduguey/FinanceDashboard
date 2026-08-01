"""Simulator-scenario endpoints — read, create and delete the saved compound-growth scenarios."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from accounting.api.api_models import SimulatorScenarioCreate
from accounting.api.entities import SimulatorScenario
from accounting.models import SimulatorScenario as DomainSimulatorScenario
from accounting.repositories.taxonomy import (
    delete_simulator_scenario,
    insert_simulator_scenario,
    load_simulator_scenarios,
)
from db.current_user import get_current_user_id
from db.session import get_db
from http_api.locations import CREATED_WITH_LOCATION, location_of

router = APIRouter()


@router.get("/simulator/scenarios/{scenario_id}")
def get_simulator_scenario(
    scenario_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> SimulatorScenario:
    """Return one saved scenario by id — the address `post_simulator_scenario` advertises.

    Returns
    -------
    SimulatorScenario

    Raises
    ------
    HTTPException
        404 if no scenario has this id.
    """
    scenario = next((s for s in load_simulator_scenarios(session, user_id) if s.scenario_id == scenario_id), None)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Simulator scenario {scenario_id!r} not found")
    return SimulatorScenario.from_domain(scenario)


@router.post("/simulator/scenarios", status_code=201, responses=CREATED_WITH_LOCATION)
def post_simulator_scenario(
    request: SimulatorScenarioCreate,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> SimulatorScenario:
    """Create one new saved scenario, without touching any other scenario already saved.

    `scenario_id` is server-minted — two scenarios can validly share
    every input field (comparing "what if I ran this exact case twice"),
    so there's no natural key two "the same" scenario would collide on,
    and nothing this route could replace instead of creating.

    Returns
    -------
    SimulatorScenario
        The scenario just persisted.
    """
    scenario = DomainSimulatorScenario(
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
    location_of(http_request, response, "get_simulator_scenario", scenario_id=scenario.scenario_id)
    return SimulatorScenario.from_domain(scenario)


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
