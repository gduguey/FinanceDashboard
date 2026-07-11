"""One user's own credentials for one broker — encrypted and persisted in Postgres, never `.env`.

Generic over `broker` on purpose: IBKR is the only broker connected today,
but nothing here mentions it by name — a second broker later reuses this
module exactly as is, with its own `broker` string and its own field names
in `fields`. Broker-specific translation (which fields a given broker
actually needs, and building that broker's own typed credentials model
from them) belongs to that broker's own module — see
`trades.brokers.ibkr.credentials.resolve_ibkr_credentials`, the only piece
of this that knows IBKR needs a `token` and a `query_id`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from db.secrets import delete_secret, get_secret, set_secret

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

BROKER_CREDENTIALS_KIND = "broker_credentials"


def _secret_key(broker: str) -> str:
    """Build the `db.secrets` lookup key one broker's credentials for one user are stored under.

    Returns
    -------
    str
    """
    return f"broker:{broker}"


def save_broker_credentials(session: Session, user_id: uuid.UUID, broker: str, fields: dict[str, str]) -> None:
    """Persist `fields` as one user's full set of credentials for `broker`, overwriting whatever was saved before.

    A caller wanting to change just one field (e.g. only a query id, not
    the token) reads the existing fields via `load_broker_credentials`
    first and merges its own update into them — this always writes the
    complete set, never a partial one.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose credentials these are.
    broker
        The broker these credentials are for, e.g. `"ibkr"`.
    fields
        Every credential field this broker needs, e.g. `{"token": ..., "query_id": ...}`.
    """
    set_secret(session, user_id, _secret_key(broker), BROKER_CREDENTIALS_KIND, json.dumps(fields))


def load_broker_credentials(session: Session, user_id: uuid.UUID, broker: str) -> dict[str, str]:
    """Return the saved credential fields for `(user_id, broker)`.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose credentials these are.
    broker
        The broker to look up, e.g. `"ibkr"`.

    Returns
    -------
    dict[str, str]
        Empty if nothing's been saved yet for this `(user_id, broker)` pair.
    """
    plaintext = get_secret(session, user_id, _secret_key(broker))
    return json.loads(plaintext) if plaintext is not None else {}


def clear_broker_credentials(session: Session, user_id: uuid.UUID, broker: str) -> None:
    """Delete whatever's saved for `(user_id, broker)`. A no-op if nothing was.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose credentials these are.
    broker
        The broker to clear, e.g. `"ibkr"`.
    """
    delete_secret(session, user_id, _secret_key(broker))


def broker_is_configured(session: Session, user_id: uuid.UUID, broker: str, required_fields: Sequence[str]) -> bool:
    """Whether every one of `required_fields` has a non-empty value saved for `(user_id, broker)`.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose credentials to check.
    broker
        The broker to check, e.g. `"ibkr"`.
    required_fields
        Field names that must all be non-empty for this broker to be usable, e.g. `("token", "query_id")`.

    Returns
    -------
    bool
    """
    fields = load_broker_credentials(session, user_id, broker)
    return all(fields.get(name) for name in required_fields)
