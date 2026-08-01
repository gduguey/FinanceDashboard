"""The one page envelope every bounded read in this app answers with, and the limits it clamps to."""

from __future__ import annotations

from pydantic import BaseModel

PAGE_LIMIT_DEFAULT = 200
"""How many `window_unit`s one page returns when the client asks for no particular size.

Large enough that a first screen needs one request, small enough that it is
nowhere near the DoS cap.
"""

PAGE_LIMIT_MAX = 5_000
"""The hard cap on any single page, whatever the client asks for.

The point is that no request can be made arbitrarily expensive by a query
parameter (API-audit F3). A `limit` above this is **clamped**, not rejected:
a client asking for more than the server will give is asking for "as much as
possible", and answering that with a 422 would make paging through a large
collection fail on the request that is trying hardest to succeed. `total` in
the response is what tells such a client that more remains.

The cap is a bound on one *response*, and says nothing about the work behind
it. `GET /lots` is the endpoint where those differ: its page is clamped here
while its FIFO match still replays the whole trades ledger, which its own
docstring states outright rather than letting this constant imply otherwise.
"""


class Page[ItemT, WindowUnitT: str](BaseModel):
    """One page of a collection, plus what a client needs to ask for the next one.

    `total`, `limit` and `offset` all count **`window_unit`s**, which is not
    always the unit `items` is in: `GET /postings` cuts its window by
    transaction and answers with every leg of every transaction in it, so
    `len(items)` there is normally larger than `limit`. The two paged reads
    used to be two hand-written envelopes with the same four field names
    meaning different things in each, and nothing but a docstring saying so —
    which is exactly the mistake a client makes by reading one endpoint and
    reusing the shape. `window_unit` is a required field, not a default, so
    every page states its own unit on the wire and the schema pins it to one
    `const` per endpoint.

    Paging is therefore always the same arithmetic regardless of endpoint:
    advance `offset` by the `limit` the *server* echoed back (never the one
    you asked for, which is clamped to `PAGE_LIMIT_MAX`) until it reaches
    `total`. `len(items)` is never the stride.

    This lives outside both `accounting` and `trades` because both answer
    with it and neither owns it — see this package's own docstring for why
    that is not a breach of their independence.
    """

    items: list[ItemT]
    window_unit: WindowUnitT
    """What `total`, `limit` and `offset` count — never necessarily an `items` entry."""
    total: int
    """How many `window_unit`s match, ignoring this page's window."""
    limit: int
    """The page size actually applied, after clamping to `PAGE_LIMIT_MAX`."""
    offset: int
    """How many `window_unit`s were skipped."""
