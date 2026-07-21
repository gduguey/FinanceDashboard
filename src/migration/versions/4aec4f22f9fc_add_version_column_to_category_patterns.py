"""add version column to category_patterns

Revision ID: 4aec4f22f9fc
Revises: f27ff4ff8536
Create Date: 2026-07-20 23:51:36.911292

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4aec4f22f9fc'
down_revision: Union[str, Sequence[str], None] = 'f27ff4ff8536'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'category_patterns', sa.Column('version', sa.Integer(), nullable=False, server_default='1'), schema='accounting'
    )
    op.alter_column('category_patterns', 'version', server_default=None, schema='accounting')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('category_patterns', 'version', schema='accounting')
