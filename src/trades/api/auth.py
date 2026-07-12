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

This app is single-user (see `db.current_user`): `require_clerk_session`
only proves a request carries a session token this Clerk application
itself issued — it does not identify *which* Clerk user, since there is
never more than one. Restricting who can obtain that token in the first
place (i.e. who Clerk lets sign up at all) is configured in the Clerk
Dashboard, deliberately outside this app's own code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from clerk_backend_api import AuthenticateRequestOptions, authenticate_request
from fastapi import HTTPException, Request
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

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
