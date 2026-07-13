"""add store_versions table

Revision ID: ac57df4e207b
Revises: ce72c5856584
Create Date: 2026-07-13 18:02:41.750377

One row per user, bumped by one on every successful `accounting.store.
save_store` call — the backbone of optimistic concurrency (see
`accounting.store.StoreVersionConflictError`). RLS follows the exact
`_USER_SCOPED_TABLES` pattern migration `8e280c1518e8` established, same
as migration `45a32dc7e644` did for `dashboard_settings`/`llm_usage`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ac57df4e207b'
down_revision: Union[str, Sequence[str], None] = 'ce72c5856584'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USER_SCOPED_TABLES: list[tuple[str, str, str]] = [
    ("accounting", "store_versions", "user_id"),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'store_versions',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_store_versions_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', name=op.f('pk_store_versions')),
        schema='accounting',
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
    op.drop_table('store_versions', schema='accounting')
