"""guard RLS policies against an empty app.current_user_id

Every `user_isolation` policy compares its row's owner column against
`current_setting('app.current_user_id', true)::uuid`. That GUC's reset value
for an undeclared placeholder GUC is the empty string, not NULL (see
`db.session.set_rls_user`), so the moment a transaction ends without the GUC
re-set, the cast becomes `''::uuid` — a hard `DataError` (HTTP 500), not a
clean "no rows".

Wrapping the read in `NULLIF(..., '')` turns the empty string into NULL, so the
comparison is simply never true and the policy denies access (fail-closed)
instead of raising. Normal requests with a real user id are unaffected.

Recreates the policies by introspecting the live set (`pg_policies`) rather
than a hardcoded table list, so it hardens exactly the policies that exist,
regardless of later schema changes. Every such policy keys on `user_id`
except `public.users`, which keys on its own `id`.

Revision ID: fd6052b7ba82
Revises: 1f822ee413fb
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fd6052b7ba82'
down_revision: Union[str, Sequence[str], None] = '1f822ee413fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _isolation_policies(connection: sa.Connection) -> list[tuple[str, str]]:
    """Every (schema, table) that currently has a `user_isolation` RLS policy."""
    rows = connection.execute(
        sa.text("SELECT schemaname, tablename FROM pg_policies WHERE policyname = 'user_isolation' ORDER BY 1, 2")
    ).fetchall()
    return [(row.schemaname, row.tablename) for row in rows]


def _owner_column(schema: str, table: str) -> str:
    """The row-owner column each `user_isolation` policy compares — `id` for users, else `user_id`."""
    return "id" if schema == "public" and table == "users" else "user_id"


def _quote_ident(name: str) -> str:
    """Double-quote a SQL identifier, escaping any embedded double quote."""
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def _recreate_policies(marker_expr: str) -> None:
    """Drop and recreate every `user_isolation` policy so its owner column compares against `marker_expr`."""
    connection = op.get_bind()
    for schema, table in _isolation_policies(connection):
        column = _owner_column(schema, table)
        qualified = f"{_quote_ident(schema)}.{_quote_ident(table)}"
        op.execute(f"DROP POLICY user_isolation ON {qualified}")
        op.execute(
            f"CREATE POLICY user_isolation ON {qualified} "
            f"USING ({column} = {marker_expr}) "
            f"WITH CHECK ({column} = {marker_expr})"
        )


def upgrade() -> None:
    """Upgrade schema."""
    _recreate_policies("NULLIF(current_setting('app.current_user_id', true), '')::uuid")


def downgrade() -> None:
    """Downgrade schema."""
    _recreate_policies("current_setting('app.current_user_id', true)::uuid")
