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
