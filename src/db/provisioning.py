"""Create the `users` row and identity link a new account needs — exactly once, under concurrency.

Two callers reach the same code here, and the reason they must is that they
can run at the same instant. `trades.api.webhooks` provisions when Clerk
delivers `user.created`; `trades.api.auth.resolve_current_user_id`
provisions just-in-time when a valid session arrives and that delivery has
not (known gap 3 — the first-login lockout). A user who signs up and lands
on the app immediately triggers both, and whichever loses must end up using
the winner's id rather than one of its own.

Provider-agnostic on purpose, and in `db/` rather than under `trades/api/`
for the same reason `db.external_identities` is: nothing here knows Clerk
exists. `provider` and `external_id` are parameters, `email` is display
information the caller already resolved, and `trades.api.auth` remains the
one module in this app that talks to Clerk. It also keeps the import
pointing the right way — the auth path would otherwise have to import the
webhook module to reuse its provisioning.

## Why a savepoint

The failure this exists to prevent is not a crash, it is a silent one.
`users.email` is deliberately not unique (`db.models.User`) and each caller
mints its own `uuid4`, so two concurrent first provisions of the same
external account insert two perfectly legal `users` rows. The only
constraint either can trip is `external_identities`' own primary key, and
`db.external_identities.link_identity` resolves that with `ON CONFLICT DO
NOTHING`. Before that function returned the linked id, the loser walked away
believing its own id was live: an orphaned `users` row nobody links to, and
— for the auth path, which hands that id straight to the request — a whole
request's worth of writes stranded under an id no later sign-in resolves to.

So the `users` insert and the link go inside one `session.begin_nested()`.
If the link comes back naming somebody else, rolling back to that savepoint
takes the orphan with it and the winner's id is returned instead. That is
`db.base.merge_by_natural_key`'s shape, deliberately — same problem, same
primitive. It needs no retry *loop*, though, and that difference is worth
stating: `merge_by_natural_key` budgets several attempts because a batch can
lose to a different writer on each pass, each a genuinely new conflict. Here
there is one fixed key, so one re-read settles it forever.
"""

from __future__ import annotations

import uuid

from db.external_identities import link_identity, lookup_user_id
from db.models import User
from db.session import session_scope


def provision_linked_user(provider: str, external_id: str, email: str) -> uuid.UUID:
    """Return the internal user id for one identity-provider account, creating it if it has none.

    Idempotent and safe to call concurrently with itself: the id returned is
    always the one `db.external_identities.lookup_user_id` will return from
    then on, whether this call created it or lost the race to create it.

    Opens its own session rather than taking one, because the session has to
    be Row-Level-Security-scoped to the user being created and no caller
    knows that id yet — it is minted here. `external_identities` is exempt
    from RLS (see `db.tenant.RLS_EXEMPT`), so the lookups work under any
    scope; `users` is not, and its policy compares the row's own `id`, which
    is exactly what this scope satisfies.

    Parameters
    ----------
    provider
        The identity provider, e.g. `"clerk"`.
    external_id
        That provider's own id for the account.
    email
        Display information for the `users` row, used only when this call is
        the one that creates it. Never a lookup key — see `db.models.User`
        on why `email` carries no unique constraint.

    Returns
    -------
    uuid.UUID
        The internal user id linked to `(provider, external_id)`.
    """
    new_user_id = uuid.uuid4()
    with session_scope(new_user_id) as session:
        already_linked = lookup_user_id(session, provider, external_id)
        if already_linked is not None:
            # The ordinary case for a webhook redelivery, and for a
            # just-in-time attempt that another request finished first.
            # Nothing is written, so nothing needs rolling back.
            return already_linked

        savepoint = session.begin_nested()
        session.add(User(id=new_user_id, email=email, is_active=True))
        session.flush()
        linked = link_identity(session, new_user_id, provider, external_id)
        if linked != new_user_id:
            # Lost the race. Undo this call's own `users` row — leaving it
            # would be the orphan this module exists to prevent — and adopt
            # the id that actually got linked.
            savepoint.rollback()
            return linked

        savepoint.commit()
        session.commit()
        return new_user_id
