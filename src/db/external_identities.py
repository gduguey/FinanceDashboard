"""The only place a (provider, external account id) -> internal user id mapping is read or written.

Deliberately its own table (`db.models.ExternalIdentity`), not a column on
`users` — so `users` never has to know Clerk, or any other identity
provider, exists at all. `external_identities` also deliberately has no
Row-Level Security policy: it's the one place a lookup has to work
*before* the caller already knows which user is asking (a session-scoped
policy like every other table's — see migration `817ace9deb09` — would
block exactly the `WHERE external_id = ...` lookup this module exists to
do, since the id it's trying to find is the very thing not known yet). The
table holds no financial data, only an identity mapping, so skipping
row-level isolation here is a narrow, deliberate trade-off, not an
oversight.

Two callers exist today: `trades.api.auth.resolve_current_user_id` reads
this on every request to find out who's asking; `trades.api.webhooks`
writes to it once, when a new user is provisioned. Reassigning someone's
account after they're deleted and re-invited in Clerk (their Clerk id
changes, their internal id shouldn't) is a single update to one row here
— see `tests/db/test_external_identities.py`'s own test of exactly that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import ExternalIdentity

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


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


def link_identity(session: Session, user_id: uuid.UUID, provider: str, external_id: str) -> None:
    """Create the mapping from one identity-provider account to `user_id`, idempotently.

    Safe to call more than once for the same `(provider, external_id)`
    pair (e.g. a webhook delivered twice) — a repeat call is a no-op, it
    never overwrites an existing link.

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
    """
    statement = (
        pg_insert(ExternalIdentity)
        .values(user_id=user_id, provider=provider, external_id=external_id)
        .on_conflict_do_nothing(index_elements=["provider", "external_id"])
    )
    session.execute(statement)
