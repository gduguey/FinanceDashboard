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
`trades.api.webhooks` creates when that person first signs up, or, when that
webhook is late or lost, against the row this file provisions on the spot
(see `_provision_from_clerk`). Restricting who can obtain a session token in
the first place (i.e. who Clerk lets sign up at all) is configured in the
Clerk Dashboard, deliberately outside this app's own code — this file only
ever asks "is this session real" and "which of our own users does it belong
to," never "should this person be allowed to exist."
"""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache
from pathlib import Path

import httpx
from clerk_backend_api import AuthenticateRequestOptions, Clerk, authenticate_request
from clerk_backend_api.models import ClerkBaseError, User
from fastapi import HTTPException, Request
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from db.external_identities import lookup_user_id
from db.provisioning import provision_linked_user
from db.session import session_factory

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]

_NO_ACCOUNT_DETAIL = "No account found for this session yet."
"""The one 401 body every provisioning failure answers with, whatever the cause.

Deliberately identical across "the webhook has not arrived", "Clerk is
unreachable", "Clerk has no such user" and "that Clerk account is banned".
The server log distinguishes them (see `_confirmed_primary_email`); the
client must not, or the 401 becomes an oracle for which Clerk accounts
exist and what state they are in.
"""

PROVISIONING_TIMEOUT_MS = 3_000
"""How long the just-in-time path will wait on Clerk's Backend API before giving up and answering 401.

A bound rather than a preference, and small on purpose: this call sits in
the request path, so a Clerk outage has to degrade to a prompt 401 rather
than to requests that hang until something upstream times out. It only ever
runs on a lookup miss (see `resolve_current_user_id`), so no
already-provisioned user pays it.
"""


class ClerkAuthSettings(BaseSettings):
    """The Clerk secret key — a real credential, never logged or exposed to the frontend.

    Distinct from `CLERK_PUBLISHABLE_KEY`: the publishable key isn't
    actually secret — it's meant to ship inside browser JS, used only at
    frontend build time — while this one lets its holder act as the Clerk
    account itself, so it's treated the same as any other secret in this
    repo (`db.encryption`'s keys, IBKR credentials, etc.) — real values
    only ever live in `.env`/`.env.docker`/`.env.staging`, never in git.
    """

    model_config = SettingsConfigDict(env_file=str(_REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    secret_key: SecretStr = Field(validation_alias="CLERK_SECRET_KEY")
    public_domain: str | None = Field(default=None, validation_alias="PUBLIC_DOMAIN")
    """The same space-separated hostname(s) Caddy serves (see `deploy/Caddyfile`'s `{$PUBLIC_DOMAIN}`).

    `None` in local dev, where no fixed domain exists yet — see `_options`
    for what that means for `authorized_parties`.
    """
    public_port: int | None = Field(default=None, validation_alias="PUBLIC_PORT")
    """The external port the browser actually connects on, only if it isn't the standard 443.

    Staging needs this (`8443`) since it can't share port 443 with
    production on the same VM; production and local dev leave it unset.
    Clerk encodes the exact origin a session token was issued for in its
    `azp` claim, port included whenever it's non-standard — and
    `clerk_backend_api`'s own verification does a plain `azp not in
    authorized_parties` string check, no port-normalization at all (see
    `security/verifytoken.py`) — so `authorized_parties` below has to
    reproduce that port suffix exactly, or every session gets rejected as
    an unauthorized party on every single request, regardless of endpoint.
    """


@lru_cache(maxsize=1)
def _clerk_settings() -> ClerkAuthSettings:
    """Read the Clerk credentials once per process — both the verifier and the provisioning call need them.

    Returns
    -------
    ClerkAuthSettings
    """
    # secret_key has no default (see ClerkAuthSettings) — pydantic-settings fills it from
    # CLERK_SECRET_KEY at runtime, but mypy has no pydantic plugin configured here to know
    # that, so it sees a required constructor argument never passed.
    return ClerkAuthSettings()  # type: ignore[call-arg]


@lru_cache(maxsize=1)
def _options() -> AuthenticateRequestOptions:
    """Build (once) Clerk's request-authentication options from `CLERK_SECRET_KEY`/`PUBLIC_DOMAIN`.

    Returns
    -------
    AuthenticateRequestOptions
    """
    settings = _clerk_settings()
    # `authorized_parties` restricts accepted sessions to tokens issued for one of these
    # origins (Clerk's `azp` claim) — left unset (None) in local dev, where PUBLIC_DOMAIN
    # isn't configured, so nothing beyond signature/expiry is enforced there.
    port_suffix = f":{settings.public_port}" if settings.public_port else ""
    authorized_parties = (
        [f"https://{host}{port_suffix}" for host in settings.public_domain.split()] if settings.public_domain else None
    )
    return AuthenticateRequestOptions(
        secret_key=settings.secret_key.get_secret_value(), authorized_parties=authorized_parties
    )


def validate_clerk_settings() -> None:
    """Fail fast if `CLERK_SECRET_KEY` is missing/invalid, instead of only on the first authenticated request.

    Called from `trades.api.dependencies`'s app startup — see its own
    lifespan handler.
    """
    _options()


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


def _primary_email(user: User) -> str | None:
    """Find the address matching a Clerk user's own `primary_email_address_id`.

    Returns
    -------
    str or None
        `None` if the account has no primary email address — possible for a
        phone-only or SSO-only Clerk account, and the one confirmable-user
        case this app cannot provision.
    """
    for email_address in user.email_addresses or []:
        if email_address.id == user.primary_email_address_id:
            return email_address.email_address
    return None


def _confirmed_primary_email(clerk_user_id: str) -> str:
    """Ask Clerk's Backend API to confirm this user exists and is usable, and return their email.

    **Fails closed, always.** Every path out of here that is not a confirmed,
    usable account raises the same 401 with the same body a missing `users`
    row already produced, because a provisioning path that failed *open*
    would turn today's inconvenience into a real hole: anyone holding a
    session token for a deleted or banned Clerk account would get an
    internal account minted for them. The distinction between the causes
    lives in the log and nowhere else — in particular a permanent failure
    ("this Clerk account has no email address") logs differently from a
    transient one, because the two need completely different responses from
    whoever reads it and the client is told neither.

    Bounded twice over. `timeout_ms` caps the request itself, and `retries`
    is pinned to `None` rather than left unset: the SDK's own default is a
    backoff whose `max_elapsed_time` is **one hour** on any 5XX
    (`clerk_backend_api.users.Users.get`), which inside a request handler is
    indistinguishable from a hang.

    Parameters
    ----------
    clerk_user_id
        The verified session token's `sub` claim.

    Returns
    -------
    str
        The account's primary email address.

    Raises
    ------
    HTTPException
        401, with the same detail as an unprovisioned session, if Clerk
        cannot be reached, does not know this user, reports the account
        unusable, or reports it without a primary email address.
    """
    settings = _clerk_settings()
    try:
        with Clerk(bearer_auth=settings.secret_key.get_secret_value()) as clerk:
            user = clerk.users.get(user_id=clerk_user_id, timeout_ms=PROVISIONING_TIMEOUT_MS, retries=None)
    except ClerkBaseError, httpx.HTTPError:
        # Both, and the second is not redundant. `ClerkBaseError` is the SDK's
        # own hierarchy — a 4XX/5XX response, an unparseable body — but a
        # request that never got a response does not go through it: with
        # `retries=None`, `BaseSDK.do_request` re-raises whatever `httpx`
        # raised, untouched. So a Clerk outage or a timeout is an
        # `httpx.HTTPError` and nothing else, and catching only the SDK's
        # errors would turn exactly the outage this timeout exists for into a
        # 500 instead of the 401 the caller is owed.
        #
        # Logged without the exception body, which for an auth call can carry
        # request detail we have no reason to persist.
        logger.warning(
            "Just-in-time provisioning: Clerk would not confirm %r (unreachable, timed out, or no such user) — "
            "answering 401. Transient: the next request retries.",
            clerk_user_id,
        )
        raise HTTPException(status_code=401, detail=_NO_ACCOUNT_DETAIL) from None

    if user.banned or user.locked or user.deprovisioned:
        logger.warning(
            "Just-in-time provisioning: refusing Clerk user %r — banned=%s locked=%s deprovisioned=%s",
            clerk_user_id,
            user.banned,
            user.locked,
            user.deprovisioned,
        )
        raise HTTPException(status_code=401, detail=_NO_ACCOUNT_DETAIL)

    email = _primary_email(user)
    if email is None:
        # Distinguished from every other failure above on purpose. This one
        # is *permanent* — retrying will never fix it, and the same account
        # would be refused by `webhooks._primary_email` with a 400 — so it
        # is the one case where a silent 401 would otherwise read as "the
        # webhook is still coming" forever.
        logger.error(
            "Just-in-time provisioning: Clerk user %r has no primary email address, so no account can be created "
            "for it. This is permanent, not a late webhook — the account needs an email address in Clerk.",
            clerk_user_id,
        )
        raise HTTPException(status_code=401, detail=_NO_ACCOUNT_DETAIL)
    return email


def _provision_from_clerk(clerk_user_id: str) -> uuid.UUID:
    """Create this app's own account row for a valid Clerk session that has none yet.

    Two steps in this order, and the order is the fail-closed guarantee:
    Clerk confirms the account first (`_confirmed_primary_email`, which
    raises 401 rather than returning on any doubt), and only a confirmed one
    reaches the write.

    Returns
    -------
    uuid.UUID
        The internal user id now linked to this Clerk account — this call's
        own, or the one a concurrent provision linked first (see
        `db.provisioning`).
    """
    email = _confirmed_primary_email(clerk_user_id)
    return provision_linked_user("clerk", clerk_user_id, email)


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

    A miss on that lookup used to be the end of it, and that was the
    first-login lockout (known gap 3): the `users` row is created by Clerk's
    `user.created` webhook, so a delivery that is slow or lost left a
    perfectly valid session permanently rejected with nothing actually
    wrong. It now provisions instead — see `_provision_from_clerk`. Three
    properties of that, each one tested:

    - **Only on a miss.** The lookup below is the fast path and stays a
      single indexed read; an already-provisioned user never touches Clerk's
      Backend API from here.
    - **Fails closed.** Every way provisioning can fail answers the same 401
      this function already answered. See `_confirmed_primary_email`.
    - **Cannot resurrect a deleted account.** `webhooks._deactivate_user`
      clears `users.is_active` and deliberately leaves the
      `external_identities` row alone, so a deactivated account still *hits*
      the lookup and provisioning never runs for it. Re-inviting that person
      in Clerk mints a new Clerk id, and a new internal user with it, which
      is what `db.models.User` documents should happen.

    Returns
    -------
    uuid.UUID
        This app's internal id for the Clerk user this session belongs to.

    Raises
    ------
    HTTPException
        401 under the same conditions as `require_clerk_session`, or if this
        Clerk session is genuinely valid and Clerk will not confirm an
        account to provision for it.
    """
    state = authenticate_request(request, _options())
    if not state.is_signed_in or state.payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    clerk_user_id = state.payload["sub"]
    with session_factory()() as session:
        user_id = lookup_user_id(session, "clerk", clerk_user_id)
    if user_id is None:
        return _provision_from_clerk(clerk_user_id)
    return user_id
