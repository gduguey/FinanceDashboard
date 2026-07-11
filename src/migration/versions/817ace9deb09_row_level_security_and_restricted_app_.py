"""row level security and restricted app_runtime role

Revision ID: 817ace9deb09
Revises: 8df611ba0b1d
Create Date: 2026-07-11 03:39:12.122701

Adds a `user_isolation` RLS policy to every table scoped by `user_id` (or,
for `users` itself, by its own `id`) across all three schemas — belt and
suspenders on top of the app's own `WHERE user_id = ...` filtering, per
`docs/app-stack/authentication-and-authorization.md`'s own reasoning: even
a bug that forgets the filter can't leak another user's rows, because
Postgres itself won't return them.

Critical wrinkle this migration also has to handle: Postgres superusers
(and a table's own owner, unless `FORCE ROW LEVEL SECURITY` is set) always
bypass RLS, full stop, no policy can override that. The role this app
connects as today (`DATABASE_URL`'s user) is both — it ran every prior
migration, so it owns every table, and the `postgres:16-alpine` image's
`POSTGRES_USER` becomes a cluster superuser on top of that. So this
migration also creates `app_runtime`, an ordinary non-superuser,
non-owner role with only `SELECT`/`INSERT`/`UPDATE`/`DELETE` granted — RLS
applies to it unconditionally, no `FORCE` needed. Its generated password
is printed once, to this migration's own output, for the operator to copy
into `.env`'s `DATABASE_URL_APP` (see `db.settings.DatabaseSettings`) —
until that's done, the app keeps connecting as the superuser role exactly
as before, and these policies exist but have no practical effect yet.
"""
import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


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

    role_exists = connection.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": _APP_RUNTIME_ROLE}
    ).first()
    if role_exists is None:
        # token_urlsafe's alphabet ([A-Za-z0-9_-]) has no quote/escape characters,
        # so it's safe to inline directly — CREATE ROLE ... PASSWORD doesn't accept
        # a bound query parameter here, only a literal.
        password = secrets.token_urlsafe(32)
        op.execute(f"CREATE ROLE \"{_APP_RUNTIME_ROLE}\" LOGIN PASSWORD '{password}'")
        print(  # noqa: T201 — this is the one, intentional place this password is ever surfaced
            f"\nCreated Postgres role '{_APP_RUNTIME_ROLE}' with a generated password.\n"
            "Copy it into DATABASE_URL_APP in .env (see db.settings.DatabaseSettings) to make RLS "
            "actually take effect for the running app — it is not printed anywhere else and cannot "
            "be recovered later, only reset via ALTER ROLE.\n"
            f"Generated password: {password}\n"
        )

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
    """Downgrade schema."""
    for schema, table, _column in _USER_SCOPED_TABLES:
        op.execute(f'DROP POLICY IF EXISTS user_isolation ON "{schema}"."{table}"')
        op.execute(f'ALTER TABLE "{schema}"."{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{schema}"."{table}" DISABLE ROW LEVEL SECURITY')

    for schema in ("public", "accounting", "trades"):
        op.execute(f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA "{schema}" FROM "{_APP_RUNTIME_ROLE}"')
        op.execute(f'REVOKE USAGE ON SCHEMA "{schema}" FROM "{_APP_RUNTIME_ROLE}"')
    op.execute(f'DROP ROLE IF EXISTS "{_APP_RUNTIME_ROLE}"')
