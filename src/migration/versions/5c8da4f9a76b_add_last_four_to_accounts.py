"""add last_four to accounts

Revision ID: 5c8da4f9a76b
Revises: c6fe1077e89a
Create Date: 2026-07-13 11:01:41.857765

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c8da4f9a76b'
down_revision: Union[str, Sequence[str], None] = 'c6fe1077e89a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('accounts', sa.Column('last_four', sa.String(), nullable=True), schema='accounting')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('accounts', 'last_four', schema='accounting')
