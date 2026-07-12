"""Reads `GEMINI_API_KEY`/`MISTRAL_API_KEY` from the repo's `.env`.

Same convention as `trades.config.IbkrFlexCredentials`. Both are
optional: a missing key just means that provider isn't in the
fallback chain (see `api._llm_providers`), not an error — the "AI
suggestion" button degrades to unavailable rather than crashing if only
one, or neither, key is set.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

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
