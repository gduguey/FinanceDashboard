"""Which user is making this request — a placeholder until real authentication exists.

`DEFAULT_USER_ID` is the one `User` row the initial migration seeds.
`get_current_user_id` is the single FastAPI dependency every endpoint asks
for the acting user through; every repository function in `accounting.db`
and `trades.db` takes `user_id` as an explicit parameter rather than
reaching for a global, specifically so that swapping this one function's
body for real session/JWT-based auth later never requires touching a
single call site.
"""

from __future__ import annotations

import uuid

DEFAULT_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def get_current_user_id() -> uuid.UUID:
    """Return the acting user's id for the current request.

    Returns
    -------
    uuid.UUID
        Always `DEFAULT_USER_ID` today — there is exactly one user, and no
        login flow to identify anyone else.
    """
    return DEFAULT_USER_ID
