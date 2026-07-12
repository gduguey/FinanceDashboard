"""Clerk session verification — the one place this app knows Clerk exists.

`trades.api.api` applies `require_clerk_session` (via `Depends`) to every
router it mounts onto `app`; no router, and nothing in `db`/`accounting`/
`trades` beyond this file, ever imports Clerk-specific code. Swapping Clerk
for another identity provider later means rewriting this file alone — see
`docs/architecture.md`'s "one FastAPI app" note for where the routers this
gates actually live.

Verification itself is delegated to Clerk's own `clerk-backend-api`
package rather than hand-rolled — it fetches and caches Clerk's signing
key from Clerk's Backend API using `CLERK_SECRET_KEY`, so a normal request
never makes a network call of its own, and a signing-key rotation on
Clerk's side is picked up automatically rather than requiring a manual
update here.

`require_clerk_session` only proves a request carries a session token this
Clerk application itself issued. `resolve_current_user_id` goes one step
further and identifies *which* Clerk user, via a real lookup —
`db.external_identities.lookup_user_id` — against the row
`trades.api.webhooks` creates when that person first signs up. Restricting
who can obtain a session token in the first place (i.e. who Clerk lets
sign up at all) is configured in the Clerk Dashboard, deliberately outside
this app's own code — this file only ever asks "is this session real" and
"which of our own users does it belong to," never "should this person be
allowed to exist."
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from pathlib import Path

from clerk_backend_api import AuthenticateRequestOptions, authenticate_request
from fastapi import HTTPException, Request
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from db.external_identities import lookup_user_id
from db.session import session_factory

_REPO_ROOT = Path(__file__).resolve().parents[3]


class ClerkAuthSettings(BaseSettings):
    """The Clerk secret key — a real credential, never logged or exposed to the frontend.

    Distinct from `CLERK_PUBLISHABLE_KEY` (used only by the frontend, and
    by this app's own docs — see `docs/server-setup/clerk-authentication.md`
    for the difference): this key lets its holder act as the Clerk
    account itself, so it's treated the same as any other secret in this
    repo (`db.encryption`'s keys, IBKR credentials, etc.) — real values
    only ever live in `.env`/`.env.docker`/`.env.staging`, never in git.
    """

    model_config = SettingsConfigDict(env_file=str(_REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    secret_key: SecretStr = Field(validation_alias="CLERK_SECRET_KEY")


@lru_cache(maxsize=1)
def _options() -> AuthenticateRequestOptions:
    """Build (once) Clerk's request-authentication options from `CLERK_SECRET_KEY`.

    Returns
    -------
    AuthenticateRequestOptions
    """
    # secret_key has no default (see ClerkAuthSettings) — pydantic-settings fills it from
    # CLERK_SECRET_KEY at runtime, but mypy has no pydantic plugin configured here to know
    # that, so it sees a required constructor argument never passed.
    settings = ClerkAuthSettings()  # type: ignore[call-arg]
    return AuthenticateRequestOptions(secret_key=settings.secret_key.get_secret_value())


def require_clerk_session(request: Request) -> None:
    """FastAPI dependency: reject any request without a valid Clerk session token.

    Verifies the token's signature against Clerk's own signing key, plus
    its expiry, via `clerk_backend_api.authenticate_request` — never the
    raw token itself, which is never logged or included in any exception
    raised here (see this module's own docstring on why *which* Clerk user
    isn't checked).

    Parameters
    ----------
    request
        The incoming request; only its headers are read (`Authorization:
        Bearer <token>`, or a Clerk `__session` cookie).

    Raises
    ------
    HTTPException
        401 if no session token is present, or it fails signature or
        expiry verification.
    """
    if not authenticate_request(request, _options()).is_signed_in:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")


def resolve_current_user_id(request: Request) -> uuid.UUID:
    """FastAPI dependency: the internal user id for whoever's Clerk session this request carries.

    Verifies the session independently of `require_clerk_session` (both
    are cheap — Clerk's own signing key is cached in memory, see
    `_options` — rather than threading a result between them, which would
    reintroduce the sibling-dependency ordering problem `db.session.get_db`'s
    own docstring documents empirically confirming unreliable). Installed
    as `db.current_user.get_current_user_id`'s override in `trades.api.api`,
    so `get_db`'s own `Depends(get_current_user_id)` resolves through here
    in the real app.

    Parameters
    ----------
    request
        The incoming request; only its headers are read.

    Returns
    -------
    uuid.UUID
        This app's internal id for the Clerk user this session belongs to.

    Raises
    ------
    HTTPException
        401 under the same conditions as `require_clerk_session`, or if
        this Clerk session is genuinely valid but no `users` row is linked
        to it yet — normally only a brief race right after sign-up, before
        `trades.api.webhooks` has processed the `user.created` event.
    """
    state = authenticate_request(request, _options())
    if not state.is_signed_in or state.payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    clerk_user_id = state.payload["sub"]
    with session_factory()() as session:
        user_id = lookup_user_id(session, "clerk", clerk_user_id)
    if user_id is None:
        raise HTTPException(status_code=401, detail="No account found for this session yet.")
    return user_id
