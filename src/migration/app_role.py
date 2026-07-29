"""Creating and revoking the restricted `app_runtime` role migrations grant to.

Row-Level Security only does anything if the application connects as a role
it applies to. The migration-owning role from `DATABASE_URL` owns every
table and would bypass every policy, so the app connects instead as
`app_runtime` (`DATABASE_URL_APP`) — an ordinary, non-superuser,
non-owning role with nothing but DML on the three schemas.

This lives beside the migrations rather than inside one because the
baseline needs it and any future revision that adds a schema will too.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]

APP_RUNTIME_ROLE = "app_runtime"
"""The role the running application connects as, and the only one RLS applies to."""


class _AppRuntimePasswordSettings(BaseSettings):
    """Reads `DATABASE_URL_APP` the way `db.settings` does: real env var first, `.env` second.

    Inside the deployment container `DATABASE_URL_APP` is already a real
    process environment variable, so `os.environ` would be enough. Locally
    `.env` is only ever a file that `uv run alembic` never exports, so it
    would not be. `BaseSettings` covers both without special-casing either.

    No fallback to `DATABASE_URL`, for the same reason
    `db.settings.AppRuntimeDatabaseSettings` refuses one: silently giving
    `app_runtime` the superuser's password is worse than refusing to run.
    """

    model_config = SettingsConfigDict(env_file=str(_REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    database_url_app: str | None = Field(default=None, validation_alias="DATABASE_URL_APP")


def _app_runtime_password() -> str:
    """Read the password the app will connect with.

    Returns
    -------
    str

    Raises
    ------
    RuntimeError
        If `DATABASE_URL_APP` is unset or carries no password — the app
        could not connect afterwards, so failing here is the honest
        outcome.
    """
    url = _AppRuntimePasswordSettings().database_url_app
    if not url:
        message = (
            "DATABASE_URL_APP must be set before migrating: it is the connection string for the restricted "
            "app_runtime role this migration creates, and the application refuses to start without it."
        )
        raise RuntimeError(message)
    password = make_url(url).password
    if not password:
        message = "DATABASE_URL_APP must include a password for the app_runtime role."
        raise RuntimeError(message)
    return password


def grant_app_runtime(op: ModuleType, *, schemas: Sequence[str]) -> None:
    """Create (or re-sync) `app_runtime` and grant it DML on `schemas`.

    Idempotent on the password: re-running after `DATABASE_URL_APP`
    changes brings the role's password back in line rather than leaving
    the app unable to connect.

    Parameters
    ----------
    op
        Alembic's `op` module.
    schemas
        Every schema the role needs to reach.
    """
    # CREATE/ALTER ROLE ... PASSWORD takes a string literal, never a bind
    # parameter, so the password is escaped by doubling single quotes.
    password = _app_runtime_password().replace("'", "''")
    role_exists = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_RUNTIME_ROLE})
        .first()
    )
    if role_exists is None:
        op.execute(f"CREATE ROLE \"{APP_RUNTIME_ROLE}\" LOGIN PASSWORD '{password}'")
    else:
        op.execute(f"ALTER ROLE \"{APP_RUNTIME_ROLE}\" WITH PASSWORD '{password}'")

    for schema in schemas:
        op.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{APP_RUNTIME_ROLE}"')
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{APP_RUNTIME_ROLE}"')
        op.execute(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{APP_RUNTIME_ROLE}"')
        op.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{APP_RUNTIME_ROLE}"'
        )
        op.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" GRANT USAGE ON SEQUENCES TO "{APP_RUNTIME_ROLE}"')


def revoke_app_runtime(op: ModuleType, *, schemas: Sequence[str]) -> None:
    """Revoke every grant this database gave `app_runtime`.

    Deliberately does **not** `DROP ROLE`. A Postgres role is cluster-wide,
    not per-database, so the same `app_runtime` is typically also granted
    objects in a sibling database (dev alongside test, staging alongside
    its own scratch databases) — `DROP ROLE` then fails outright with
    "role cannot be dropped because some objects depend on it", which
    would make this downgrade unrunnable for reasons that have nothing to
    do with this database. Revoking what this database granted is the
    correct scope; the role itself is shared infrastructure.

    Default privileges are separate objects from table grants and must be
    revoked in their own right.

    Parameters
    ----------
    op
        Alembic's `op` module.
    schemas
        The same schemas `grant_app_runtime` was given.
    """
    for schema in schemas:
        op.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            f'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM "{APP_RUNTIME_ROLE}"'
        )
        op.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" REVOKE USAGE ON SEQUENCES FROM "{APP_RUNTIME_ROLE}"')
        op.execute(f'REVOKE ALL ON ALL TABLES IN SCHEMA "{schema}" FROM "{APP_RUNTIME_ROLE}"')
        op.execute(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA "{schema}" FROM "{APP_RUNTIME_ROLE}"')
        op.execute(f'REVOKE USAGE ON SCHEMA "{schema}" FROM "{APP_RUNTIME_ROLE}"')
