"""Reads `GEMINI_API_KEY`/`MISTRAL_API_KEY` from the repo's `.env`, or from a Settings-page override.

Same convention as `trades.credentials`. Both keys are optional: a
missing one just means that provider isn't in the fallback chain (see
`api._llm_providers`), not an error — the "AI suggestion" button degrades
to unavailable rather than crashing if only one, or neither, key is set.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from accounting.utils.io_utils import write_json_atomic

if TYPE_CHECKING:
    from accounting.config import AccountingConfig

_REPO_ROOT = Path(__file__).resolve().parents[3]


class LLMCredentials(BaseSettings):
    """API keys for the LLM providers, read from `.env` or the environment."""

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    mistral_api_key: SecretStr | None = Field(default=None, validation_alias="MISTRAL_API_KEY")


class LLMCredentialOverride(BaseModel):
    """LLM API keys entered via the Settings page, taking precedence over `.env`.

    Never sent back to a client once saved — an endpoint reporting on
    this only ever reports whether a key is set, never its value.
    """

    model_config = ConfigDict(frozen=True)

    gemini_api_key: str | None = None
    mistral_api_key: str | None = None


def load_llm_credential_override(config: AccountingConfig) -> LLMCredentialOverride:
    """Read the persisted LLM credential override, or an empty one if nothing's been saved yet.

    Parameters
    ----------
    config
        Application configuration; `config.llm_credentials_path` is read.

    Returns
    -------
    LLMCredentialOverride
        The persisted override, or `LLMCredentialOverride()` if the file doesn't exist yet.
    """
    path = config.llm_credentials_path
    if not path.exists():
        return LLMCredentialOverride()
    try:
        return LLMCredentialOverride.model_validate_json(path.read_text())
    except ValueError:
        return LLMCredentialOverride()

def save_llm_credential_override(override: LLMCredentialOverride, config: AccountingConfig) -> None:
    """Persist an LLM credential override, overwriting whatever was saved before.

    Parameters
    ----------
    override
        The credentials to persist.
    config
        Application configuration; `config.llm_credentials_path` is written to.
    """
    write_json_atomic(override.model_dump(mode="json"), config.llm_credentials_path)


def resolve_llm_credentials(config: AccountingConfig) -> LLMCredentials:
    """Build the LLM credentials to actually use: the Settings-page override, falling back to `.env`.

    Parameters
    ----------
    config
        Application configuration; `config.llm_credentials_path` is read.

    Returns
    -------
    LLMCredentials
        Either key left unset (by both the override and `.env`/the environment) stays `None`.
    """
    override = load_llm_credential_override(config)
    kwargs: dict[str, Any] = {}
    if override.gemini_api_key:
        kwargs["gemini_api_key"] = override.gemini_api_key
    if override.mistral_api_key:
        kwargs["mistral_api_key"] = override.mistral_api_key
    return LLMCredentials(**kwargs)
