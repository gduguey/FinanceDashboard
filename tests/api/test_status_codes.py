"""Status-code contract, asserted against the app's own OpenAPI schema rather than route by route.

One test per rule, walking every registered path — so a route added later
either follows the rule or fails here, instead of the rule being a convention
nobody re-checks. The exceptions are listed explicitly, with the reason each
one is an exception, so adding to the list is a deliberate act.
"""

from __future__ import annotations

from typing import Any

import pytest

from trades.api.api import app

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
# `accounting.api.locations.created_or_replaced`; a blanket 201 on these would
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
    through `accounting.api.locations.CREATED_WITH_LOCATION`; this fails
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
