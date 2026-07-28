"""Where the app finds its Postgres connection string(s).

Two distinct roles connect to the same database, deliberately kept as two
settings classes: `DatabaseSettings` (`DATABASE_URL`) is the
migration-owning superuser role — Alembic always uses exactly this one,
since DDL needs its privileges. `AppRuntimeDatabaseSettings`
(`DATABASE_URL_APP`) is what the running API process actually connects as
day to day — a separate, ordinary (non-superuser, non-owner) role, so
Postgres Row-Level Security policies (see the `817ace9deb09` migration)
actually apply to its queries.

`DATABASE_URL_APP` is required, with no fallback to `DATABASE_URL` — the
app refuses to start rather than silently connecting as the superuser
(which would make RLS exist but do nothing). The migration that creates
the `app_runtime` role already refuses to run under the same condition
(see `817ace9deb09`), so this mirrors that: either both are configured, or
neither runs, never a half-configured state where migrations succeeded
but the app quietly isn't protected by RLS.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class DatabaseSettings(BaseSettings):
    """The migration-owning Postgres connection string — Alembic's, always, never the app's own request traffic."""

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(validation_alias="DATABASE_URL")


class AppRuntimeDatabaseSettings(BaseSettings):
    """The connection string the running API process actually uses to serve requests.

    Always `DATABASE_URL_APP` — the restricted `app_runtime` role RLS
    policies actually apply to. No fallback to `DATABASE_URL`: see this
    module's own docstring for why silently running as the superuser
    instead is worse than refusing to start.
    """

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(validation_alias="DATABASE_URL_APP")

    pool_size: int = Field(
        default=5,
        ge=1,
        validation_alias="DB_POOL_SIZE",
        description=(
            "Connections held open per process. Sized for the 1-CPU deployment container: uvicorn serves "
            "requests from one event loop, so concurrency is bounded by how many requests are simultaneously "
            "inside a `session_scope`, not by CPU count. Raise this with the container, not ahead of it."
        ),
    )
    max_overflow: int = Field(
        default=5,
        ge=0,
        validation_alias="DB_MAX_OVERFLOW",
        description=(
            "Extra connections opened past `pool_size` under burst, then discarded. Deliberately lower than "
            "SQLAlchemy's default of 10: Postgres' own `max_connections` is the scarce resource, and a burst "
            "that needs 10 spare connections on a 1-CPU box is a queueing problem, not a pool-sizing one."
        ),
    )
    pool_recycle_seconds: int = Field(
        default=1800,
        ge=-1,
        validation_alias="DB_POOL_RECYCLE_SECONDS",
        description=(
            "Discard a pooled connection older than this. Guards against an idle connection being reaped "
            "server-side or by a NAT/proxy timeout between requests; `pool_pre_ping` catches the rest."
        ),
    )
    pool_timeout_seconds: int = Field(
        default=30,
        ge=1,
        validation_alias="DB_POOL_TIMEOUT_SECONDS",
        description="How long a request waits for a free connection before failing rather than hanging.",
    )


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
