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


@pytest.mark.parametrize("route", _routes(), ids=_route_id)
def test_every_response_model_is_declared_in_an_api_module(route: APIRoute) -> None:
    """The positive half of the rule: a response *model* is one this layer owns.

    Catches a model declared anywhere the test above does not name — a router
    file, a `dashboard` module, a third-party base — which would be just as
    undeclared a contract as a domain model is. A route answering with a plain
    scalar or a mapping of them (`dict[str, Rate]`) declares no shape at all
    and has nothing to leak.
    """
    model = route.response_model
    while True:
        if hasattr(model, "__metadata__"):  # `Annotated[Decimal, WithJsonSchema(...)]`, i.e. `db.money.Rate`
            model = typing.get_args(model)[0]
            continue
        contained = [argument for argument in typing.get_args(model) if argument is not type(None)]
        if not contained:  # `list[...]`, `dict[str, ...]`, `X | None`
            break
        model = contained[-1]
    is_model = isinstance(model, type) and issubclass(model, BaseModel)
    module = getattr(model, "__module__", None)
    assert not (is_model and module not in _WIRE_MODULES), (
        f"{_route_id(route)} answers with a model declared in {module}, which is not one of {sorted(_WIRE_MODULES)}."
    )
