"""The one place that knows IBKR credentials are a `token` + `query_id` pair.

Everything generic about "one user's credentials for one broker" lives in
`trades.broker_credentials`; this module is the thin, IBKR-specific layer
on top of it — the only code in this package allowed to know what fields
IBKR's Flex Web Service actually needs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

from trades.broker_credentials import (
    broker_is_configured,
    clear_broker_credentials,
    load_broker_credentials,
    save_broker_credentials,
)
from trades.config import IbkrFlexCredentials

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

BROKER_NAME = "ibkr"
_REQUIRED_FIELDS = ("token", "query_id")


class IbkrCredentialsNotConfiguredError(Exception):
    """Raised when no IBKR credentials (or an incomplete pair) are saved for a user."""


def save_ibkr_credentials(
    session: Session, user_id: uuid.UUID, *, token: str | None = None, query_id: str | None = None
) -> None:
    """Merge `token`/`query_id` into whatever's already saved for this user's IBKR connection.

    Either argument left `None` leaves that field exactly as it was — the
    same partial-merge convention `PUT /api/settings/ibkr` already exposes
    to the frontend.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose IBKR credentials these are.
    token
        The Flex Web Service token, or `None` to leave the saved one unchanged.
    query_id
        The Flex Web Service query id, or `None` to leave the saved one unchanged.
    """
    existing = load_broker_credentials(session, user_id, BROKER_NAME)
    updated = dict(existing)
    if token is not None:
        updated["token"] = token
    if query_id is not None:
        updated["query_id"] = query_id
    save_broker_credentials(session, user_id, BROKER_NAME, updated)


def clear_ibkr_credentials(session: Session, user_id: uuid.UUID) -> None:
    """Delete this user's saved IBKR credentials entirely.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose IBKR credentials to clear.
    """
    clear_broker_credentials(session, user_id, BROKER_NAME)


def ibkr_credential_fields(session: Session, user_id: uuid.UUID) -> dict[str, str]:
    """Return whichever of `token`/`query_id` are currently saved, for reporting only — never the values.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose IBKR credentials to look up.

    Returns
    -------
    dict[str, str]
        Missing keys mean that field was never saved.
    """
    return load_broker_credentials(session, user_id, BROKER_NAME)


def ibkr_is_configured(session: Session, user_id: uuid.UUID) -> bool:
    """Whether this user has both a token and a query id saved.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose IBKR credentials to check.

    Returns
    -------
    bool
    """
    return broker_is_configured(session, user_id, BROKER_NAME, _REQUIRED_FIELDS)


def resolve_ibkr_credentials(session: Session, user_id: uuid.UUID) -> IbkrFlexCredentials:
    """Build this user's `IbkrFlexCredentials` from what's saved in Postgres.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose IBKR credentials to resolve.

    Returns
    -------
    IbkrFlexCredentials

    Raises
    ------
    IbkrCredentialsNotConfiguredError
        If no token, no query id, or neither is saved for this user.
    """
    fields = load_broker_credentials(session, user_id, BROKER_NAME)
    token = fields.get("token")
    query_id = fields.get("query_id")
    if not token or not query_id:
        message = "No IBKR credentials configured for this user"
        raise IbkrCredentialsNotConfiguredError(message)
    return IbkrFlexCredentials(token=SecretStr(token), query_id=query_id)
