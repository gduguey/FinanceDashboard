"""IBKR credentials entered via the Settings page, persisted outside `.env` and preferred over it.

`.env` remains the zero-setup path for an operator comfortable editing a
config file directly; this module exists so someone without shell access
to the server can connect their own IBKR account from the Settings page
instead. An override only replaces whichever field is actually set — a
query id saved here with no token yet still lets a token already in
`.env` resolve normally, field by field, the same way `ManualOverride`
merges a partial edit elsewhere in this app rather than requiring the
whole record every time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError

from trades.config import IbkrFlexCredentials
from trades.utils.io_utils import write_json_atomic

if TYPE_CHECKING:
    from trades.config import AppConfig


class IbkrCredentialOverride(BaseModel):
    """IBKR Flex Web Service credentials entered via the Settings page.

    Never sent back to a client once saved — an endpoint reporting on
    this only ever reports whether a field is set, never its value.
    """

    model_config = ConfigDict(frozen=True)

    token: str | None = None
    query_id: str | None = None


def load_ibkr_credential_override(config: AppConfig) -> IbkrCredentialOverride:
    """Read the persisted IBKR credential override, or an empty one if nothing's been saved yet.

    Parameters
    ----------
    config
        Application configuration; `config.credentials.ibkr_credentials_path` is read.

    Returns
    -------
    IbkrCredentialOverride
        The persisted override, or `IbkrCredentialOverride()` if the file doesn't exist yet.
    """
    path = config.credentials.ibkr_credentials_path
    if not path.exists():
        return IbkrCredentialOverride()
    return IbkrCredentialOverride.model_validate_json(path.read_text())


def save_ibkr_credential_override(override: IbkrCredentialOverride, config: AppConfig) -> None:
    """Persist an IBKR credential override, overwriting whatever was saved before.

    Parameters
    ----------
    override
        The credentials to persist.
    config
        Application configuration; `config.credentials.ibkr_credentials_path` is written to.
    """
    write_json_atomic(override.model_dump(mode="json"), config.credentials.ibkr_credentials_path)


def resolve_ibkr_credentials(config: AppConfig) -> IbkrFlexCredentials:
    """Build the IBKR credentials to actually use: the Settings-page override, falling back to `.env`.

    Parameters
    ----------
    config
        Application configuration; `config.credentials.ibkr_credentials_path` is read.

    Returns
    -------
    IbkrFlexCredentials
        Raises `pydantic.ValidationError` (from the underlying construction) if neither the
        override nor `.env`/the environment supplies a token and a query id.
    """
    override = load_ibkr_credential_override(config)
    kwargs: dict[str, Any] = {}
    if override.token:
        kwargs["token"] = override.token
    if override.query_id:
        kwargs["query_id"] = override.query_id
    return IbkrFlexCredentials(**kwargs)  # unset fields fall back to the environment


def ibkr_is_configured(config: AppConfig) -> bool:
    """Whether IBKR credentials are available right now, from the Settings-page override or `.env`.

    Returns
    -------
    bool
    """
    try:
        resolve_ibkr_credentials(config)
    except ValidationError:
        return False
    return True
