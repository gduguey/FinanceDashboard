"""Status-code and route-ordering contract, asserted against the app itself rather than route by route.

One test per rule, walking every registered path — so a route added later
either follows the rule or fails here, instead of the rule being a convention
nobody re-checks. The exceptions are listed explicitly, with the reason each
one is an exception, so adding to the list is a deliberate act.

Most of these read the app's own OpenAPI schema, which is what a client sees.
The last one reads the routers' registration order instead, because that is
where the fact it checks lives and the schema cannot express it: a literal
segment and an item route can both be perfectly declared and still resolve to
the wrong handler.
"""

from __future__ import annotations

import operator
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.routing import APIRoute

from accounting.api.routers import budgets, goals
from trades.api.api import app

if TYPE_CHECKING:
    from types import ModuleType

# Deletes that answer 200 with a body instead of 204. Each clears or cascades
# rather than removing the one row the client named, so it has a
# representation the caller cannot derive from its own request — see each
# handler's own docstring.
_DELETES_RETURNING_A_BODY = {
    "/api/v1/accounting/categories/{category_id}",  # cascades; returns the surviving tree
    "/api/v1/accounting/settings/llm",  # clears fields on a row that still exists
    "/api/v1/trades/settings/ibkr",  # clears fields on a row that still exists
}

# Every route that brings a resource into existence, and therefore owes a
# `Location`. Listed rather than derived so that adding a create is a
# deliberate act with a status-code decision attached, and so that a route
# quietly *losing* its 201 fails a test. See
# `docs/http-api-contract.md` for the rule that placed each one.
_CREATES = {
    ("post", "/api/v1/accounting/accounts"),
    ("post", "/api/v1/accounting/budgets"),
    ("post", "/api/v1/accounting/categories"),
    ("post", "/api/v1/accounting/categories/{parent_id}/subcategories"),
    ("post", "/api/v1/accounting/category-patterns"),
    ("put", "/api/v1/accounting/dismissed-suggestions/{suggestion_id}"),
    ("post", "/api/v1/accounting/goal-automations/contributions"),
    ("post", "/api/v1/accounting/goal-automations/withdrawals"),
    ("post", "/api/v1/accounting/goal-contributions"),
    ("post", "/api/v1/accounting/goals"),
    ("post", "/api/v1/accounting/other-assets"),
    ("post", "/api/v1/accounting/posting-merges"),
    ("post", "/api/v1/accounting/simulator/scenarios"),
    ("post", "/api/v1/accounting/tags"),
    ("post", "/api/v1/accounting/transfer-links"),
    ("post", "/api/v1/accounting/transfer-rules"),
}

# The subset of `_CREATES` that can also answer 200, because the id comes from
# the request's own content and the write is an upsert (or, for transfer links,
# a no-op re-confirm). Each declares both statuses through
# `http_api.locations.created_or_replaced`; a blanket 201 on these would
# report a replace as a creation and attach a `Location` for a resource the
# request did not create.
_CREATE_OR_REPLACE = {
    ("post", "/api/v1/accounting/budgets"),
    ("post", "/api/v1/accounting/category-patterns"),
    ("put", "/api/v1/accounting/dismissed-suggestions/{suggestion_id}"),
    ("post", "/api/v1/accounting/goal-automations/withdrawals"),
    ("post", "/api/v1/accounting/posting-merges"),
    ("post", "/api/v1/accounting/transfer-links"),
    ("post", "/api/v1/accounting/transfer-rules"),
}


# Every route that starts work it does not finish, and therefore answers
# `202 Accepted` with a `Location` naming the resource that reports on it.
# One today: a broker sync, which the client polls to completion. See
# `trades.api.routers.sync`.
_ACCEPTS = {
    ("post", "/api/v1/trades/sync-runs"),
}


@pytest.fixture(scope="module")
def paths() -> dict[str, dict[str, Any]]:
    """The app's own OpenAPI paths object.

    Returns
    -------
    dict[str, dict[str, Any]]
    """
    return dict(app.openapi()["paths"])


def test_every_delete_answers_204_with_no_body(paths) -> None:
    """A delete's response body used to echo the id the client had just sent in the path.

    Fifteen routes did that, through fifteen one-field `*IdResponse` models.
    None of it told a caller anything it did not already know, and no frontend
    call site read it.
    """
    offenders = []
    for path, operations in paths.items():
        if "delete" not in operations or path in _DELETES_RETURNING_A_BODY:
            continue
        responses = operations["delete"]["responses"]
        if "204" not in responses or responses["204"].get("content") is not None:
            offenders.append((path, sorted(responses)))
    assert offenders == []


def test_every_201_declares_the_location_header_it_answers_with(paths) -> None:
    """A `201` owes the caller the address of what it just made.

    FastAPI derives a response body from the return annotation and knows
    nothing about headers, so a `Location` set only in a handler is real on
    the wire but absent from the schema — and therefore absent from
    `web/src/types/schema.ts` and from `/docs`. Every create declares it
    through `http_api.locations.CREATED_WITH_LOCATION`; this fails
    for one that sets the header without declaring it, or declares the
    status without the header.
    """
    missing = [
        (path, method)
        for path, operations in paths.items()
        for method, operation in operations.items()
        if "201" in operation.get("responses", {})
        and "Location" not in operation["responses"]["201"].get("headers", {})
    ]
    assert missing == []


def test_every_202_declares_the_location_header_it_answers_with(paths) -> None:
    """A `202` owes the caller the address of the work it just started.

    The same guarantee as the `201` case above and for the same mechanical
    reason — an undeclared header is invisible to the generated client — but
    it matters more here, because the whole point of a `202` is that the
    client has to come back. A body saying "accepted" with no address to poll
    is a dead end.
    """
    missing = [
        (path, method)
        for path, operations in paths.items()
        for method, operation in operations.items()
        if "202" in operation.get("responses", {})
        and "Location" not in operation["responses"]["202"].get("headers", {})
    ]
    assert missing == []


def test_the_long_running_starts_are_exactly_the_routes_that_answer_202(paths) -> None:
    """A `202` is a promise that the work is *not* done, so gaining or losing one is a contract change.

    Listed rather than derived, like `_CREATES`: a route that starts
    answering 202 has decided its work no longer finishes inside the request,
    and one that stops has decided the opposite. Neither should be able to
    happen without this list moving.
    """
    answering = {
        (method, path)
        for path, operations in paths.items()
        for method, operation in operations.items()
        if "202" in operation.get("responses", {})
    }
    assert answering == _ACCEPTS


def test_the_deletes_that_keep_a_body_are_exactly_the_listed_exceptions(paths) -> None:
    """Guards the exception list from drifting into a place nobody rechecks.

    A delete that starts returning a body without being added above fails the
    test before it, and one that stops needing the exception fails here.
    """
    keeping_a_body = {
        path
        for path, operations in paths.items()
        if "delete" in operations and "200" in operations["delete"]["responses"]
    }
    assert keeping_a_body == _DELETES_RETURNING_A_BODY


def test_the_routes_answering_201_are_exactly_the_listed_creates(paths) -> None:
    """Guards the create inventory in both directions.

    A new route that answers 201 without being listed fails here, which
    forces the `POST`-versus-`PUT` and `201`-versus-`200` decision to be
    made explicitly rather than copied from whichever handler was nearest.
    A create that silently drops back to 200 fails here too — that is the
    regression the whole contract is about.
    """
    answering_201 = {
        (method, path)
        for path, operations in paths.items()
        for method, operation in operations.items()
        if "201" in operation.get("responses", {})
    }
    assert answering_201 == _CREATES


def test_every_content_derived_id_upsert_declares_its_200_as_well(paths) -> None:
    """An upsert's `200` has to be in the schema, not only in the handler.

    FastAPI generates a response entry only for the status on the decorator,
    so the `200` these routes drop to on a replace is a status the generated
    TypeScript client would otherwise believe cannot happen. The inverse
    matters too: a create listed here that stops being able to replace
    should stop declaring the 200.
    """
    declaring_both = {
        (method, path)
        for path, operations in paths.items()
        for method, operation in operations.items()
        if {"200", "201"} <= set(operation.get("responses", {}))
    }
    assert declaring_both == _CREATE_OR_REPLACE


# Every literal-segment read that shares an address space with an item route:
# the module both live in, the literal path, and the item path that would
# shadow it. Starlette matches in registration order, so each of these becomes
# "read the resource whose id is `comparison`" — a 404 or a 422 nobody would
# attribute to route ordering — the moment it is registered second. Both routes
# of each pair are deliberately in one module so the constraint is visible
# where it applies rather than in `api.py`'s include order; see
# `docs/http-api-contract.md`.
_LITERAL_READS_SHADOWED_BY_AN_ITEM_ROUTE = {
    (budgets, "/budgets/comparison", "/budgets/{budget_id}"),
    (budgets, "/budgets/suggested-amount", "/budgets/{budget_id}"),
    (goals, "/goals/summary", "/goals/{goal_id}"),
}


@pytest.mark.parametrize(
    ("module", "literal_path", "item_path"),
    sorted(_LITERAL_READS_SHADOWED_BY_AN_ITEM_ROUTE, key=operator.itemgetter(1)),
    ids=lambda value: value if isinstance(value, str) else value.__name__.rsplit(".", 1)[-1],
)
def test_a_literal_read_is_registered_before_the_item_route_that_would_shadow_it(
    module: ModuleType, literal_path: str, item_path: str
) -> None:
    """Asserted against the router's own list, so moving either route re-runs the check.

    A route absent from this module fails here too, which is the other half of
    the constraint: `/budgets/comparison` and `/budgets/suggested-amount` were
    moved out of `routers.dashboard` precisely so that "registered first" is a
    fact about one file.
    """
    order = [route.path for route in module.router.routes if isinstance(route, APIRoute) and "GET" in route.methods]
    assert literal_path in order, f"GET {literal_path} is not registered in {module.__name__}"
    assert item_path in order, f"GET {item_path} is not registered in {module.__name__}"
    assert order.index(literal_path) < order.index(item_path)


# Every read that answers with the shared `http_api.pagination.Page` envelope.
# Listed rather than derived for the same reason `_CREATES` is: a read that
# quietly *loses* its pagination should fail here, which a rule computed from
# whatever the schema currently says could never catch. `GET /trades/ledger/export`
# was unbounded until C3 and is the reason these assertions exist — the
# contract document claimed this file enforced them, and it asserted nothing
# about pagination at all.
_PAGED_READS = {
    "/api/v1/accounting/ledger/export": "posting",
    "/api/v1/accounting/postings": "transaction",
    "/api/v1/trades/ledger/export": "event",
}


def _response_schema_name(operation: dict[str, Any]) -> str:
    """Return the component name a 200 response is a reference to.

    Returns
    -------
    str
        The bare component name, or `""` if the response is not a `$ref`.
    """
    content = operation["responses"]["200"]["content"]["application/json"]["schema"]
    return str(content.get("$ref", "")).rsplit("/", 1)[-1]


@pytest.mark.parametrize(("path", "window_unit"), sorted(_PAGED_READS.items()))
def test_every_paged_read_answers_with_the_shared_envelope(paths, path: str, window_unit: str) -> None:
    """One envelope, four fields, and a `window_unit` that says what the other three count.

    The fields are asserted by name because that is the whole contract a
    client codes against: `docs/http-api-contract.md` tells it to advance
    `offset` by the echoed `limit` until it reaches `total`, and that
    arithmetic is wrong the moment one of them is missing or renamed.
    """
    schemas = app.openapi()["components"]["schemas"]
    operation = paths[path]["get"]
    envelope = schemas[_response_schema_name(operation)]

    assert set(envelope["required"]) >= {"items", "window_unit", "total", "limit", "offset"}, (
        f"GET {path} does not answer with the shared page envelope"
    )
    assert envelope["properties"]["window_unit"]["const"] == window_unit, (
        f"GET {path} must pin its window unit to {window_unit!r} rather than leave it open"
    )


@pytest.mark.parametrize("path", sorted(_PAGED_READS))
def test_every_paged_read_takes_an_optional_limit_and_offset(paths, path: str) -> None:
    """Both are optional and both are bounded, so an unparameterised call is a valid first page.

    `limit` carries `minimum: 1` and `offset` `minimum: 0` from their
    `Query(ge=...)` declarations. Neither declares a `maximum`: the cap is
    applied by clamping in the handler rather than by rejecting, so a schema
    maximum here would turn "as much as possible" into a 422 — see
    `PAGE_LIMIT_MAX`.
    """
    parameters = {parameter["name"]: parameter for parameter in paths[path]["get"].get("parameters", [])}

    for name, minimum in (("limit", 1), ("offset", 0)):
        assert name in parameters, f"GET {path} takes no {name}"
        assert parameters[name]["required"] is False, f"GET {path}'s {name} must be optional"
        assert parameters[name]["schema"]["minimum"] == minimum
        assert "maximum" not in parameters[name]["schema"], (
            f"GET {path}'s {name} declares a maximum; the cap is clamped in the handler, not rejected"
        )
