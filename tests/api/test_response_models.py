"""Every route answers with an API-layer model, never with a domain model.

The return annotation on a handler is its whole response contract — neither
package uses `response_model=` anywhere — so an annotation naming a type from
`accounting.models`/`trades.models` couples the wire format to the domain
model, and a field added there for internal reasons ships to every client and
into `web/src/types/schema.ts` with nobody deciding it should. Asserted here
by walking every router module in both packages rather than reviewed route by
route, so a handler added later either follows the rule or fails.
"""

from __future__ import annotations

import importlib
import pkgutil
import typing
from decimal import Decimal

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute
from pydantic import BaseModel

import accounting.api.routers
import trades.api.routers

_DOMAIN_MODULES = frozenset({"accounting.models", "trades.models"})
"""The two modules a response type may never come from."""

_WIRE_MODULES = frozenset({
    "accounting.api.api_models",
    "accounting.api.entities",
    "trades.api.api_models",
    "trades.api.entities",
})
"""Where a response model may be declared.

`db.money`, `db.currency` and the standard library are reached *through* those
models' own fields — a route never names one directly.
"""


def _routes() -> list[APIRoute]:
    """Every route in both packages' router modules, with the response model it answers with.

    Discovered through `pkgutil` rather than listed, so a new router module
    is covered the moment it exists. Routes with no response model at all
    (every `204` delete) carry no contract to check.

    Returns
    -------
    list[APIRoute]
        One entry per (module, route) pair, ordered by module name.
    """
    found: list[APIRoute] = []
    for package in (accounting.api.routers, trades.api.routers):
        for module_info in sorted(pkgutil.iter_modules(package.__path__), key=lambda info: info.name):
            module = importlib.import_module(f"{package.__name__}.{module_info.name}")
            router = getattr(module, "router", None)
            if not isinstance(router, APIRouter):
                continue
            found.extend(
                route for route in router.routes if isinstance(route, APIRoute) and route.response_model is not None
            )
    return found


def _modules_reachable_from(annotation: object) -> set[str]:
    """Collect the defining module of `annotation` and of everything reachable from it.

    Walks two ways, because a leak hides equally well in either: into the
    type arguments (`list[Category]`) and into a model's own fields
    (`AccountingStoreResponse.categories`), which is how the four transitive
    leaks this rule was written for got onto the wire.

    Parameters
    ----------
    annotation
        A response-model annotation.

    Returns
    -------
    set[str]
        One entry per distinct `__module__` reachable from it.
    """
    found: set[str] = set()
    seen: set[int] = set()

    def walk(node: object) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        module = getattr(node, "__module__", None)
        if module is not None:
            found.add(module)
        for argument in typing.get_args(node):
            walk(argument)
        if isinstance(node, type) and issubclass(node, BaseModel):
            for field in node.model_fields.values():
                walk(field.annotation)

    walk(annotation)
    return found


def _route_id(route: APIRoute) -> str:
    """Name one route for pytest's own output.

    Parameters
    ----------
    route
        The route to name.

    Returns
    -------
    str
        Its method and module-relative path.
    """
    return f"{min(route.methods)} {route.path}"


def test_there_are_routes_to_check() -> None:
    """Guard the two tests below against a discovery change that silently empties them."""
    assert len(_routes()) > 100


@pytest.mark.parametrize("route", _routes(), ids=_route_id)
def test_no_route_answers_with_a_domain_model(route: APIRoute) -> None:
    """A route may compose its response out of anything except `accounting.models`/`trades.models`.

    Nesting counts: `list[Category]`, and a wrapper model with one
    domain-typed field, leak exactly as much as a bare annotation does — see
    `_modules_reachable_from`.
    """
    leaked = _modules_reachable_from(route.response_model) & _DOMAIN_MODULES
    assert not leaked, (
        f"{_route_id(route)} answers with a type from {sorted(leaked)}. "
        f"Mirror it in `api.entities` instead — see that module's docstring."
    )


def _models_reachable_from(annotation: object) -> set[type[BaseModel]]:
    """Collect every Pydantic model reachable from `annotation`, at any depth.

    Parameters
    ----------
    annotation
        A response-model annotation.

    Returns
    -------
    set[type[BaseModel]]
        Every model the wire format is made of, the outermost one included.
    """
    found: set[type[BaseModel]] = set()

    def walk(node: object) -> None:
        if isinstance(node, type) and issubclass(node, BaseModel):
            if node in found:
                return
            found.add(node)
            for field in node.model_fields.values():
                walk(field.annotation)
        for argument in typing.get_args(node):
            walk(argument)

    walk(annotation)
    return found


@pytest.mark.parametrize("route", _routes(), ids=_route_id)
def test_every_response_model_is_declared_in_an_api_module(route: APIRoute) -> None:
    """The positive half of the rule: every model the response is made of is one this layer owns.

    Checked at every depth, not only on the outermost type — a wire model
    holding a field typed from a router file or a `dashboard` module would be
    just as undeclared a contract as a domain model is, and the test above
    would not name it because it only rejects the two domain modules.

    A route answering with a plain scalar or a mapping of them
    (`dict[str, Rate]`) reaches no model at all and has nothing to declare.
    """
    misplaced = {
        model for model in _models_reachable_from(route.response_model) if model.__module__ not in _WIRE_MODULES
    }
    assert not misplaced, (
        f"{_route_id(route)} is made of {sorted(f'{model.__module__}.{model.__name__}' for model in misplaced)}, "
        f"declared outside {sorted(_WIRE_MODULES)}."
    )


_ANALYTICS_RESPONSES = frozenset({
    "BudgetComparisonRow",
    "CategoryTotalRow",
    "GoalsSummary",
    "InterestAccountRow",
    "MonthlyIncomeExpenseRow",
    "NetWorthAccountRow",
    "NetWorthHistoryByAccountPoint",
    "NetWorthHistoryPoint",
    "NetWorthOtherAssetRow",
    "NetWorthSummary",
    "ProjectionPoint",
    "SpendCurvePoint",
    "SuggestedBudgetAmount",
})
"""The response models whose every money figure is an aggregate, not a stored value.

Each one reports a sum, a balance, or a projection, computed over the resolved
ledger in floats through the boundary `ledger.frame` declares (T1) and usually
converted at a display currency's rate on top. None of those figures is exact
and none of them can be, so all of them are `float` — and an exact `Money`
appearing anywhere inside one is the defect this list exists to catch, not an
improvement.

Two of these used to carry one. `NetWorthSummary` embedded the stored
`OtherAsset` — exact `value` — as a summand of its own float total, and
`BudgetComparisonRow` put an exact `budgeted` next to an approximate `actual`
and invited the subtraction. See `docs/http-api-contract.md` for where the
exact value of each of those lives instead.
"""


def _exact_money_fields(model: type[BaseModel]) -> list[str]:
    """Name every field of `model` that is, or contains, an exact `Decimal`.

    Parameters
    ----------
    model
        A wire model.

    Returns
    -------
    list[str]
        The field names, empty when the model carries no exact money.
    """
    found = []
    for name, field in model.model_fields.items():
        annotations = [field.annotation, *typing.get_args(field.annotation)]
        if any(annotation is Decimal for annotation in annotations):
            found.append(name)
    return found


@pytest.mark.parametrize("route", _routes(), ids=_route_id)
def test_an_analytics_response_carries_no_exact_money(route: APIRoute) -> None:
    """A response that reports aggregates reports only aggregates, at every depth.

    Checked transitively, because the way this goes wrong is by embedding: the
    outermost model's own fields were already floats in both cases the rule was
    written for, and the exact value arrived inside a nested row.
    """
    reachable = _models_reachable_from(route.response_model)
    if not any(model.__name__ in _ANALYTICS_RESPONSES for model in reachable):
        return
    offenders = {f"{model.__name__}.{field}" for model in reachable for field in _exact_money_fields(model)}
    assert not offenders, (
        f"{_route_id(route)} reports aggregates but embeds exact money in {sorted(offenders)}. "
        f"Convert it at the boundary (`db.money.to_analytics_float`) or return the entity from its own route."
    )


def test_every_named_analytics_response_exists() -> None:
    """Guard `_ANALYTICS_RESPONSES` against a rename that silently empties an entry."""
    declared = {model.__name__ for route in _routes() for model in _models_reachable_from(route.response_model)}

    assert declared >= _ANALYTICS_RESPONSES
