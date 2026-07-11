"""One user's own LLM provider API keys — encrypted and persisted in Postgres, never `.env`.

Same shape as `trades.broker_credentials`: `.env` has no notion of "which
user," so a per-user API key can only ever live in the database, keyed by
`(user_id, provider)` — see `db.secrets`, the encrypted key/value store
both are built on. Either provider's key being unset just means that
provider isn't in the fallback chain (see `api._llm_providers`), not an
error — the "AI suggestion" button degrades to unavailable rather than
crashing if only one, or neither, key is set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, SecretStr

from db.secrets import delete_secret, get_secret, set_secret

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

LLM_API_KEY_KIND = "llm_api_key"


def _secret_key(provider: str) -> str:
    """Build the `db.secrets` lookup key one provider's API key for one user is stored under.

    Returns
    -------
    str
    """
    return f"llm:{provider}"


def save_llm_api_key(session: Session, user_id: uuid.UUID, provider: str, api_key: str) -> None:
    """Persist `api_key` as this user's key for `provider`, overwriting whatever was saved before.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose key this is.
    provider
        `"gemini"` or `"mistral"`.
    api_key
        The API key to encrypt and persist.
    """
    set_secret(session, user_id, _secret_key(provider), LLM_API_KEY_KIND, api_key)


def load_llm_api_key(session: Session, user_id: uuid.UUID, provider: str) -> str | None:
    """Return this user's saved API key for `provider`, or `None` if nothing's been saved yet.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose key to look up.
    provider
        `"gemini"` or `"mistral"`.

    Returns
    -------
    str or None
    """
    return get_secret(session, user_id, _secret_key(provider))


def clear_llm_api_key(session: Session, user_id: uuid.UUID, provider: str) -> None:
    """Delete this user's saved API key for `provider`. A no-op if nothing was saved.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose key to clear.
    provider
        `"gemini"` or `"mistral"`.
    """
    delete_secret(session, user_id, _secret_key(provider))


class LLMCredentials(BaseModel):
    """One user's resolved API keys for every LLM provider — either may be unset."""

    model_config = ConfigDict(frozen=True)

    gemini_api_key: SecretStr | None = None
    mistral_api_key: SecretStr | None = None


def resolve_llm_credentials(session: Session, user_id: uuid.UUID) -> LLMCredentials:
    """Build this user's `LLMCredentials` from whatever's saved in Postgres.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose credentials to resolve.

    Returns
    -------
    LLMCredentials
        Either key left unset stays `None` — never an error, since this app
        degrades to whichever provider (if any) has a key configured.
    """
    gemini_api_key = load_llm_api_key(session, user_id, "gemini")
    mistral_api_key = load_llm_api_key(session, user_id, "mistral")
    return LLMCredentials(
        gemini_api_key=SecretStr(gemini_api_key) if gemini_api_key else None,
        mistral_api_key=SecretStr(mistral_api_key) if mistral_api_key else None,
    )
