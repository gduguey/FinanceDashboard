"""One way to answer `Location` on a create, so the header can never name a route that does not exist.

Every `201 Created` in `accounting.api.routers` owes a `Location`, and every
one of them sets it through `location_of` below rather than by formatting a
path itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import Request, Response
    from pydantic import BaseModel

# What a `201` promises, spelled out in the schema rather than left as an
# undocumented convention. FastAPI derives a response's body from the return
# annotation but knows nothing about headers, so a `Location` that isn't
# declared here is invisible to `web/src/types/schema.ts` and to anyone reading
# `/docs` — and invisible is indistinguishable from absent for a client
# deciding whether it can follow the header. `tests/api/test_status_codes.py`
# asserts every 201 in the app carries this declaration.
CREATED_WITH_LOCATION: dict[int | str, dict[str, Any]] = {
    201: {
        "headers": {
            "Location": {
                "description": "URL of the resource this request created.",
                "schema": {"type": "string", "format": "uri-reference"},
            }
        }
    }
}


def created_or_replaced(model: type[BaseModel]) -> dict[int | str, dict[str, Any]]:
    """Declare both statuses a create-or-replace route can answer with.

    An upsert whose id comes from the request's own content has two honest
    outcomes, and a blanket `201` would report a replace as a creation.
    These routes declare `status_code=201` as their default and drop to
    `200` in the handler when the row was already there, so the schema has
    to carry both — FastAPI only generates a response entry for the status
    on the decorator, and an undeclared `200` is a status the generated
    client believes cannot happen.

    The `200` body is the same model as the `201` body: the caller gets the
    persisted row either way, and only the status and the absence of a
    `Location` distinguish them.

    Parameters
    ----------
    model
        The route's response model, restated for the `200` entry.

    Returns
    -------
    dict[int | str, dict[str, Any]]
        A `responses=` value covering `201` (with `Location`) and `200`.
    """
    return {
        **CREATED_WITH_LOCATION,
        200: {"model": model, "description": "The request replaced a resource that already existed."},
    }


def location_of(request: Request, response: Response, route: str, **path_params: str) -> None:
    """Point `response`'s `Location` header at the URL of the named route.

    Resolved through `request.url_for`, against the application's own route
    table, rather than by formatting the path in the handler. Two things
    follow, and both are the reason this exists:

    - A `Location` naming a route that isn't registered raises
      `starlette.routing.NoMatchFound` here, on the create itself, instead
      of returning a header that 404s when a client follows it. A create
      cannot advertise an address the API does not serve.
    - No handler has to know its own prefix. These routes are mounted under
      `/api/v1/accounting` by `accounting.api.api`, one level up, and a
      hand-built `Location` would have to repeat that constant at every
      create and drift from it silently.

    Parameters
    ----------
    request
        The create's request — read for the route table and the base URL.
    response
        The create's response, whose `Location` header this sets.
    route
        The name of the route to point at, which is the item-GET handler's
        own function name.
    path_params
        The path parameters that route takes.
    """
    response.headers["Location"] = str(request.url_for(route, **path_params))
