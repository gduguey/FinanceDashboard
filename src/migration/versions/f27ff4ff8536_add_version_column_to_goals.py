"""add version column to goals

Revision ID: f27ff4ff8536
Revises: b369b4122538
Create Date: 2026-07-20 22:26:51.484102

Backs `PATCH /goals/{goal_id}`'s per-row optimistic concurrency
(`db.base.check_and_bump_row_version`), the same shape
`b369b4122538_add_version_column_to_transfer_rules.py` added for
`transfer_rules`. `server_default='1'` backfills every existing row, then
gets dropped once that backfill has run — see that earlier migration's
own docstring for the full reasoning.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f27ff4ff8536'
down_revision: Union[str, Sequence[str], None] = 'b369b4122538'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('goals', sa.Column('version', sa.Integer(), nullable=False, server_default='1'), schema='accounting')
    op.alter_column('goals', 'version', server_default=None, schema='accounting')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('goals', 'version', schema='accounting')
