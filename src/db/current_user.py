"""Which user is making this request.

`get_current_user_id` is a name, not really a function meant to run — its
own body always raises. `trades.api.api` overrides it, once, at startup
(`app.dependency_overrides[get_current_user_id] = resolve_current_user_id`)
with the function that actually resolves a live Clerk session to a real
row in `users` (via `db.external_identities.lookup_user_id` — see that
module for why identity resolution is a database lookup, not a formula).
See `src/db/README.md`'s "Which user is making this request" section for
the full step-by-step trace of how that swap actually reaches a request.

There is no `DEFAULT_USER_ID` here on purpose: every real request resolves
its acting user fresh, from that user's own verified session, via the
mechanism above; background/cron jobs that need a real user look one up
explicitly rather than assuming a single fixed identity (see e.g.
`trades.market_data.price_sync`). A stable, arbitrary id for tests to seed
a `User` row under is a test-only fixture, not a production concept — see
`tests.conftest.DEFAULT_USER_ID`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid


def get_current_user_id() -> uuid.UUID:
    """Return the acting user for the current request — always overridden in the real running app.

    Raises
    ------
    RuntimeError
        Always, unless overridden. See this module's own docstring.
    """
    message = "get_current_user_id() was never overridden — see this function's own docstring."
    raise RuntimeError(message)
