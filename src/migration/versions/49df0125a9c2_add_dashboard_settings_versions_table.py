"""add dashboard_settings_versions table

Revision ID: 49df0125a9c2
Revises: ac57df4e207b
Create Date: 2026-07-13 18:41:50.209030

One row per user, bumped by one on every successful `trades.dashboard.
settings.save_settings` call — same purpose as `accounting.store_versions`
(migration `ac57df4e207b`), now generalized via `db.base.check_and_bump_
version`/`get_version` so `trades` can reuse the identical mechanism
against its own single-row `dashboard_settings` table. RLS follows the
same `_USER_SCOPED_TABLES` pattern as every migration since `8e280c1518e8`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '49df0125a9c2'
down_revision: Union[str, Sequence[str], None] = 'ac57df4e207b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USER_SCOPED_TABLES: list[tuple[str, str, str]] = [
    ("trades", "dashboard_settings_versions", "user_id"),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'dashboard_settings_versions',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], name=op.f('fk_dashboard_settings_versions_user_id_users'), ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('user_id', name=op.f('pk_dashboard_settings_versions')),
        schema='trades',
    )

    for schema, table, column in _USER_SCOPED_TABLES:
        op.execute(f'ALTER TABLE "{schema}"."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{schema}"."{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY user_isolation ON "{schema}"."{table}" '
            f"USING ({column} = current_setting('app.current_user_id', true)::uuid) "
            f"WITH CHECK ({column} = current_setting('app.current_user_id', true)::uuid)"
        )


def downgrade() -> None:
    """Downgrade schema.

    Just drops the table — its `user_isolation` policy drops
    automatically along with it, no separate `DROP POLICY` needed.
    """
    op.drop_table('dashboard_settings_versions', schema='trades')
