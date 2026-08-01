"""Clerk webhooks — provisions a `users` row the moment someone accepts an invite.

The only route in this app not gated by `require_clerk_session`
(`trades.api.api` mounts this router without it): Clerk's own servers call
this endpoint directly, never a signed-in browser, so there's no session to
check. Authenticity comes from `svix` signature verification instead — the
same guarantee `require_clerk_session` gives every other route, just via a
different mechanism (Clerk's webhook deliveries are signed with Svix, not
issued as session JWTs).

On `user.created`, hands off to `db.provisioning.provision_linked_user`,
which inserts both a `users` row and the `external_identities` link
(`trades.api.auth.resolve_current_user_id` reads that same link on every
later request from them) — so a newly invited person's very first
authenticated request already resolves to a row that exists, rather than
hitting the foreign-key violation this app shipped with before any of
this identity-linking existed (see migration `bcb4d6662dfc`).

That provisioning is **no longer only here**. `resolve_current_user_id`
calls the same function when a valid session arrives before this delivery
does, which is what closed the first-login lockout — so the two can now
genuinely run at once for the same account, and the shared function is what
makes the loser adopt the winner's id instead of stranding one of its own.
This module used to do the insert itself and documented the concurrent-
redelivery case as an acceptable orphaned `users` row; that is fixed rather
than tolerated now, as a side effect of the auth path needing it fixed.

On `user.deleted`, marks the linked `users` row `is_active=False` —
fired by Clerk regardless of whether the person deleted their own
account or an admin removed them, so one handler covers both. Note this
is metadata only today: nothing else in this app currently reads
`is_active` (a soft-delete marker) — a deleted Clerk
account already can't produce a valid session token at all, so access is
already cut off the moment Clerk itself deletes it; this just records
that it happened, for anyone looking at the `users` table directly.

**It deliberately leaves the `external_identities` row in place**, and that
is load-bearing now rather than incidental: because the link survives, a
deactivated account still resolves through the ordinary lookup and the
just-in-time path never fires for it. Removing the link here would silently
turn every deactivated user's next request into a fresh provision — an
undelete nobody asked for. `tests/trades/api/test_webhooks.py` pins it.
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from svix.webhooks import Webhook, WebhookVerificationError

from db.external_identities import lookup_user_id
from db.models import User
from db.provisioning import provision_linked_user
from db.session import session_scope

router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]


class ClerkWebhookSettings(BaseSettings):
    """The signing secret Clerk's Dashboard shows when you create this webhook endpoint.

    A real credential — verifying deliveries with it is what proves a
    request coming into this route genuinely came from Clerk, not anyone
    who finds the URL — treated the same as `CLERK_SECRET_KEY`
    (`trades.api.auth.ClerkAuthSettings`).
    """

    model_config = SettingsConfigDict(env_file=str(_REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    signing_secret: SecretStr = Field(validation_alias="CLERK_WEBHOOK_SIGNING_SECRET")


@lru_cache(maxsize=1)
def _webhook_settings() -> ClerkWebhookSettings:
    """Build (once) the webhook signing settings from `CLERK_WEBHOOK_SIGNING_SECRET`.

    Returns
    -------
    ClerkWebhookSettings
    """
    # signing_secret has no default (see ClerkWebhookSettings) — pydantic-settings fills it from
    # CLERK_WEBHOOK_SIGNING_SECRET at runtime, but mypy has no pydantic plugin configured here to
    # know that, so it sees a required constructor argument never passed.
    return ClerkWebhookSettings()  # type: ignore[call-arg]


def _primary_email(data: dict[str, Any]) -> str:
    """Find the email address matching a `user.created` payload's own `primary_email_address_id`.

    Returns
    -------
    str

    Raises
    ------
    HTTPException
        400 if no email address in the payload matches `primary_email_address_id`.
    """
    primary_id = data["primary_email_address_id"]
    for email_address in data["email_addresses"]:
        if email_address["id"] == primary_id:
            return str(email_address["email_address"])
    message = f"No email address in the payload matches primary_email_address_id {primary_id!r}"
    raise HTTPException(status_code=400, detail=message)


def _deactivate_user(clerk_user_id: str) -> None:
    """Mark the `users` row linked to `clerk_user_id` inactive, whether deleted by the user or an admin.

    A no-op if no `users` row is linked to this Clerk id at all — a
    redelivery of an event already handled, or an id this app never
    provisioned in the first place. Two session scopes, not one: the
    first looks up which internal id this Clerk id maps to (`external_identities`
    carries no Row-Level Security — see its own docstring — so any scope
    works for that lookup); the second is scoped to that *found* id
    specifically, since `users` itself does have RLS, keyed on its own
    `id` column, and a session scoped to the wrong id would silently see
    zero rows to update rather than raising.

    Parameters
    ----------
    clerk_user_id
        The Clerk user id (`data.id`) from the `user.deleted` payload.
    """
    with session_scope(uuid.uuid4()) as session:
        target_user_id = lookup_user_id(session, "clerk", clerk_user_id)
    if target_user_id is None:
        return
    with session_scope(target_user_id) as session:
        user = session.get(User, target_user_id)
        if user is not None:
            user.is_active = False
            session.commit()


# Spelled out here rather than inherited from a router prefix, and deliberately
# left out of the `/api/v1` versioning every other route took: this URL is
# configured in Clerk's own Dashboard, not by this repo. Renaming it here
# silently stops Clerk delivering `user.created`, which breaks sign-ups for
# every new invitee — so a rename needs the Clerk Dashboard updated out of band,
# in the same change. Don't "fix" the inconsistency with the versioned routes.
@router.post("/api/webhooks/clerk")
async def handle_clerk_webhook(request: Request) -> dict[str, str]:
    """Verify and handle one Clerk webhook delivery.

    Parameters
    ----------
    request
        The raw incoming request — both its body and its `svix-*` headers
        are read verbatim, since Svix signs the exact bytes of the body.

    Returns
    -------
    dict[str, str]
        A small acknowledgement body; Clerk only checks the status code.

    Raises
    ------
    HTTPException
        400 if the delivery's signature doesn't verify.
    """
    body = await request.body()
    try:
        payload = Webhook(_webhook_settings().signing_secret.get_secret_value()).verify(body, dict(request.headers))
    except (WebhookVerificationError, ValueError) as error:
        # ValueError also catches a malformed (non-base64) svix-signature header —
        # standardwebhooks' own verify() lets that propagate as a bare
        # binascii.Error/ValueError rather than wrapping it, since it's a
        # library `standardwebhooks` doesn't itself define.
        raise HTTPException(status_code=400, detail="Invalid webhook signature.") from error

    if payload.get("type") == "user.created":
        data = payload["data"]
        provision_linked_user("clerk", data["id"], _primary_email(data))
    elif payload.get("type") == "user.deleted":
        _deactivate_user(payload["data"]["id"])

    return {"status": "ok"}
