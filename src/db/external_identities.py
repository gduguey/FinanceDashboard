"""The only place a (provider, external account id) -> internal user id mapping is read or written.

Deliberately its own table (`db.models.ExternalIdentity`), not a column on
`users` — so `users` never has to know Clerk, or any other identity
provider, exists at all. `external_identities` also deliberately has no
Row-Level Security policy: it's the one place a lookup has to work
*before* the caller already knows which user is asking (a session-scoped
policy like every other table's — see `db.tenant` — would
block exactly the `WHERE external_id = ...` lookup this module exists to
do, since the id it's trying to find is the very thing not known yet). The
table holds no financial data, only an identity mapping, so skipping
row-level isolation here is a narrow, deliberate trade-off, not an
oversight.

Two callers exist today: `trades.api.auth.resolve_current_user_id` reads
this on every request to find out who's asking; `db.provisioning` writes to
it, on behalf of both the Clerk `user.created` webhook and the just-in-time
path the same resolver takes when the webhook has not arrived. Reassigning
someone's account after they're deleted and re-invited in Clerk (their
Clerk id changes, their internal id shouldn't) is a single update to one
row here — see `tests/db/test_external_identities.py`'s own test of exactly
that.
"""

from __future__ import annotations

import uuid  # noqa: TC003 — link_identity's return annotation is evaluated by pydantic-free runtime callers
from typing import TYPE_CHECKING

from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import ExternalIdentity

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class IdentityLinkRaceError(RuntimeError):
    """`link_identity`'s insert conflicted, and the row it conflicted with then could not be read.

    Not reachable by any interleaving this module can construct.
    `INSERT ... ON CONFLICT DO NOTHING` returning no row means Postgres
    found a conflicting tuple and waited on its speculative-insertion lock
    until the inserter finished; had that inserter aborted, the insert would
    have proceeded instead. So by the time the follow-up `SELECT` runs — a
    new statement, and therefore a new READ COMMITTED snapshot — the winning
    row is committed and visible.

    It exists because the alternative to raising is returning the caller's
    own id after failing to link it, which is precisely the silent
    orphaning this function's return value was added to prevent. A loud
    failure on something impossible is cheap; a wrong user id is not.
    """


def lookup_user_id(session: Session, provider: str, external_id: str) -> uuid.UUID | None:
    """Find the internal user id linked to one identity-provider account.

    Parameters
    ----------
    session
        Any open session — this table has no Row-Level Security, so it
        doesn't need to already be scoped to a particular user.
    provider
        e.g. `"clerk"`.
    external_id
        That provider's own id for the account, e.g. a verified session
        token's `sub` claim.

    Returns
    -------
    uuid.UUID | None
        `None` if no user is linked to this `(provider, external_id)` pair.
    """
    identity = session.get(ExternalIdentity, (provider, external_id))
    return identity.user_id if identity is not None else None


def link_identity(session: Session, user_id: uuid.UUID, provider: str, external_id: str) -> uuid.UUID:
    """Create the mapping from one identity-provider account to `user_id`, idempotently.

    Safe to call more than once for the same `(provider, external_id)`
    pair (e.g. a webhook delivered twice) — a repeat call is a no-op, it
    never overwrites an existing link.

    **Returns which user id is linked, which is not always the one passed
    in.** That distinction is the whole point of the return value. Two
    concurrent first sign-ins for the same Clerk account each mint their own
    fresh `uuid4` and each insert their own `users` row — and because
    `users.email` is deliberately not unique (see `db.models.User`), there
    is nothing for those two rows to collide on. Only this table's own
    primary key stops the second link, and `DO NOTHING` stops it *silently*.
    A caller that assumed its own id had been linked would hand the request
    an id nothing will ever resolve to again, and every row that request
    wrote would be stranded under it. Comparing this value against the id
    passed in is how `db.provisioning` detects that it lost.

    Parameters
    ----------
    session
        Any open session — see `lookup_user_id`. Callers commit it
        themselves, same convention as the rest of this repo's repository
        functions.
    user_id
        The internal user id to link.
    provider
        e.g. `"clerk"`.
    external_id
        That provider's own id for the account.

    Returns
    -------
    uuid.UUID
        `user_id` if this call created the link, or the id already linked to
        `(provider, external_id)` if one was there first.

    Raises
    ------
    IdentityLinkRaceError
        If the insert conflicted and the conflicting row could not then be
        read. See that exception for why no interleaving produces it.
    """
    statement = (
        pg_insert(ExternalIdentity)
        .values(user_id=user_id, provider=provider, external_id=external_id)
        .on_conflict_do_nothing(index_elements=["provider", "external_id"])
        .returning(ExternalIdentity.user_id)
    )
    inserted = session.execute(statement).scalar_one_or_none()
    if inserted is not None:
        return inserted
    # Nothing returned means a row was already there. Re-read it rather than
    # assuming `user_id` won — see this function's own docstring, and
    # `IdentityLinkRaceError` for why this lookup cannot come back empty.
    linked = lookup_user_id(session, provider, external_id)
    if linked is None:
        message = (
            f"link_identity conflicted on ({provider!r}, {external_id!r}) but no row is there to read. "
            "Either the conflicting insert is uncommitted (which the speculative-insertion lock rules out) "
            "or the row was deleted in between, which no code path in this repo does."
        )
        raise IdentityLinkRaceError(message)
    return linked
