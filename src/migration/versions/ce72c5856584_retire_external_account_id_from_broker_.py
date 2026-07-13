"""retire external_account_id from broker_connections

Revision ID: ce72c5856584
Revises: 5c8da4f9a76b
Create Date: 2026-07-13 17:14:33.218439

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ce72c5856584'
down_revision: Union[str, Sequence[str], None] = '5c8da4f9a76b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('broker_connections', 'external_account_id', schema='trades')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('broker_connections', sa.Column('external_account_id', sa.String(), nullable=True), schema='trades')
