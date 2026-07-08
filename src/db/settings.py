"""Where the app finds its Postgres connection string."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class DatabaseSettings(BaseSettings):
    """The one Postgres connection string the whole app connects through."""

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(validation_alias="DATABASE_URL")


class TestDatabaseSettings(BaseSettings):
    """The test suite's own Postgres connection string — never `DatabaseSettings`'s.

    Kept as a distinct settings class (rather than an `if testing` branch in
    `DatabaseSettings`) so it's structurally impossible for a test run to
    fall back to the dev database just because `DATABASE_URL_TEST` is unset
    — that would fail loudly with a validation error instead.
    """

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(validation_alias="DATABASE_URL_TEST")
