"""add rule_id to transfer_links

Revision ID: 530fc702b41e
Revises: c0a01fde67fc
Create Date: 2026-07-19 20:36:15.334041

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '530fc702b41e'
down_revision: Union[str, Sequence[str], None] = 'c0a01fde67fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('transfer_links', sa.Column('rule_id', sa.String(), nullable=True), schema='accounting')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('transfer_links', 'rule_id', schema='accounting')
