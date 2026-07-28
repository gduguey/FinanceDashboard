"""row level security and restricted app_runtime role

Revision ID: 817ace9deb09
Revises: 8df611ba0b1d
Create Date: 2026-07-11 03:39:12.122701

Adds a `user_isolation` RLS policy to every table scoped by `user_id` (or,
for `users` itself, by its own `id`) across all three schemas — belt and
suspenders on top of the app's own `WHERE user_id = ...` filtering: even a
bug that forgets the filter can't leak another user's rows, because
Postgres itself won't return them.

Critical wrinkle this migration also has to handle: Postgres superusers
(and a table's own owner, unless `FORCE ROW LEVEL SECURITY` is set) always
bypass RLS, full stop, no policy can override that. The role this app
connects as today (`DATABASE_URL`'s user) is both — it ran every prior
migration, so it owns every table, and the `postgres:16-alpine` image's
`POSTGRES_USER` becomes a cluster superuser on top of that. So this
migration also creates `app_runtime`, an ordinary non-superuser,
non-owner role with only `SELECT`/`INSERT`/`UPDATE`/`DELETE` granted — RLS
applies to it unconditionally, no `FORCE` needed.

Its password is *not* generated here — it's read straight out of
`DATABASE_URL_APP` (the operator picks it themselves, in `.env`/`.env.docker`,
the same way `POSTGRES_PASSWORD` already works), and this migration creates
or updates the role to match. Migrations run automatically on every
deploy now (see the Dockerfile entrypoint), so there is no human watching
this migration's output to catch a one-time generated password — reading
a value the operator already committed to `.env.docker` is the only version
of this that survives an unattended deploy. Refuses to run at all
(`RuntimeError`) if `DATABASE_URL_APP` isn't set — RLS existing but quietly
not applying to the running app is exactly the failure mode this whole
migration exists to prevent, so it fails the deploy loudly instead.
"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

_REPO_ROOT = Path(__file__).resolve().parents[3]


class _AppRuntimePasswordSettings(BaseSettings):
    """Reads `DATABASE_URL_APP` the same way `db.settings` does — real env var first, `.env` file second.

    Inside the Docker container, `DATABASE_URL_APP` is already a real
    process environment variable (`env_file: .env.docker` in
    `docker-compose.yml` sets it directly) — a plain `os.environ.get`
    would actually work there. Locally, `.env` is only ever a *file*
    `uv run alembic` never exports into its own process environment, so a
    plain `os.environ.get` silently sees nothing. `BaseSettings` handles
    both the same way pydantic-settings always does: real environment
    variables first, the `.env` file as a fallback — so this migration
    behaves identically in both places without special-casing either one.

    Deliberately has no fallback to `DATABASE_URL` — same reasoning as
    `db.settings.AppRuntimeDatabaseSettings` refusing that fallback too:
    silently reusing the superuser's password for `app_runtime` would be
    worse than this migration just refusing to proceed.
    """

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    database_url_app: str | None = Field(default=None, validation_alias="DATABASE_URL_APP")


# revision identifiers, used by Alembic.
revision: str = '817ace9deb09'
down_revision: Union[str, Sequence[str], None] = '8df611ba0b1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APP_RUNTIME_ROLE = "app_runtime"

# Every (schema, table, ownership_column) scoped to one user. `users` itself
# uses its own `id` rather than a `user_id` column — it *is* the user.
_USER_SCOPED_TABLES: list[tuple[str, str, str]] = [
    ("public", "users", "id"),
    ("public", "user_secrets", "user_id"),
    ("accounting", "accounts", "user_id"),
    ("accounting", "categories", "user_id"),
    ("accounting", "dismissed_suggestions", "user_id"),
    ("accounting", "goals", "user_id"),
    ("accounting", "other_assets", "user_id"),
    ("accounting", "simulator_scenarios", "user_id"),
    ("accounting", "tags", "user_id"),
    ("accounting", "transactions", "user_id"),
    ("accounting", "budgets", "user_id"),
    ("accounting", "category_patterns", "user_id"),
    ("accounting", "general_budgets", "user_id"),
    ("accounting", "manual_transfers", "user_id"),
    ("accounting", "opening_balances", "user_id"),
    ("accounting", "posting_merges", "user_id"),
    ("accounting", "postings", "user_id"),
    ("accounting", "recurring_additions", "user_id"),
    ("accounting", "transfer_rules", "user_id"),
    ("accounting", "withdrawal_priority_entries", "user_id"),
    ("accounting", "goal_contributions", "user_id"),
    ("accounting", "manual_overrides", "user_id"),
    ("accounting", "posting_merge_duplicates", "user_id"),
    ("accounting", "posting_splits", "user_id"),
    ("accounting", "posting_tags", "user_id"),
    ("accounting", "posting_split_legs", "user_id"),
    ("trades", "broker_connections", "user_id"),
    ("trades", "ledger_events", "user_id"),
]


def _app_runtime_password() -> str:
    """Read `app_runtime`'s intended password straight out of `DATABASE_URL_APP`.

    Returns
    -------
    str

    Raises
    ------
    RuntimeError
        If `DATABASE_URL_APP` isn't set, or has no password component.
    """
    raw_url = _AppRuntimePasswordSettings().database_url_app
    if not raw_url:
        message = (
            "DATABASE_URL_APP is not set. Pick a password for the 'app_runtime' role "
            "(same as POSTGRES_PASSWORD/DATABASE_URL) and set DATABASE_URL_APP to the full "
            "connection string using it, in .env (dev) or .env.docker (deploy), before running "
            "this migration — RLS cannot actually apply to the running app without it."
        )
        raise RuntimeError(message)
    password = make_url(raw_url).password
    if not password:
        message = "DATABASE_URL_APP has no password component — expected postgresql://app_runtime:<password>@..."
        raise RuntimeError(message)
    return password


def upgrade() -> None:
    """Upgrade schema."""
    connection = op.get_bind()

    for schema, table, column in _USER_SCOPED_TABLES:
        op.execute(f'ALTER TABLE "{schema}"."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{schema}"."{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY user_isolation ON "{schema}"."{table}" '
            f"USING ({column} = current_setting('app.current_user_id', true)::uuid) "
            f"WITH CHECK ({column} = current_setting('app.current_user_id', true)::uuid)"
        )

    # The password itself is a literal, not a bound parameter — CREATE/ALTER ROLE
    # ... PASSWORD doesn't accept one, only a string literal — so it's quoted by
    # doubling any single quotes, the standard SQL escape (Postgres has no other
    # placeholder syntax for this position).
    password = _app_runtime_password().replace("'", "''")
    role_exists = connection.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": _APP_RUNTIME_ROLE}
    ).first()
    if role_exists is None:
        op.execute(f"CREATE ROLE \"{_APP_RUNTIME_ROLE}\" LOGIN PASSWORD '{password}'")
    else:
        # Idempotent sync: if the operator changes DATABASE_URL_APP's password and
        # redeploys, the role's actual password follows it rather than drifting out
        # of sync with what the app is now trying to connect with.
        op.execute(f"ALTER ROLE \"{_APP_RUNTIME_ROLE}\" WITH PASSWORD '{password}'")

    for schema in ("public", "accounting", "trades"):
        op.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{_APP_RUNTIME_ROLE}"')
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{_APP_RUNTIME_ROLE}"')
        op.execute(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{_APP_RUNTIME_ROLE}"')
        op.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{_APP_RUNTIME_ROLE}"'
        )
        op.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" GRANT USAGE ON SEQUENCES TO "{_APP_RUNTIME_ROLE}"'
        )


def downgrade() -> None:
    """Downgrade schema.

    Checks each table actually exists before touching it — a later
    migration (`8e280c1518e8`) drops and recreates the `accounting`/`trades`
    schemas wholesale, which already removes the RLS this migration set up
    on the tables that existed at the time. Downgrading *that* migration
    first (as any normal `alembic downgrade` to before this revision does)
    leaves nothing here for those tables — only `public.users`/
    `public.user_secrets` (never touched by that later migration) still
    need cleaning up by the time this runs.
    """
    connection = op.get_bind()
    for schema, table, _column in _USER_SCOPED_TABLES:
        exists = connection.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = :schema AND table_name = :table"
            ),
            {"schema": schema, "table": table},
        ).first()
        if exists is None:
            continue
        op.execute(f'DROP POLICY IF EXISTS user_isolation ON "{schema}"."{table}"')
        op.execute(f'ALTER TABLE "{schema}"."{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{schema}"."{table}" DISABLE ROW LEVEL SECURITY')

    for schema in ("public", "accounting", "trades"):
        # ALTER DEFAULT PRIVILEGES grants are a separate kind of object from the
        # privileges they hand out on existing tables — revoking "on all tables"
        # above doesn't touch these, and DROP ROLE fails until they're gone too.
        op.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            f'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM "{_APP_RUNTIME_ROLE}"'
        )
        op.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" REVOKE USAGE ON SEQUENCES FROM "{_APP_RUNTIME_ROLE}"')
        op.execute(f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA "{schema}" FROM "{_APP_RUNTIME_ROLE}"')
        op.execute(f'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "{schema}" FROM "{_APP_RUNTIME_ROLE}"')
        op.execute(f'REVOKE USAGE ON SCHEMA "{schema}" FROM "{_APP_RUNTIME_ROLE}"')
    op.execute(f'DROP ROLE IF EXISTS "{_APP_RUNTIME_ROLE}"')
