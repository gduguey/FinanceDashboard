"""add clerk_user_id to users

Revision ID: bcb4d6662dfc
Revises: 36de9420fdbf
Create Date: 2026-07-12 11:46:46.033522

Every table with a `user_id` column has FK-referenced `users.id` since
`36de9420fdbf` — so a `users` row has to exist before anything else can be
written. Nothing in this migration creates one: provisioning a `users` row
is exclusively `trades.api.webhooks`' job, the moment Clerk delivers a
`user.created` event for a real invite. A fresh database has an empty
`users` table until the first person actually signs up — see
`deploy/seed_owner_user.py` (gitignored, not this migration) if you need
to seed your own account manually, e.g. to bypass the invite flow once.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bcb4d6662dfc'
down_revision: Union[str, Sequence[str], None] = '36de9420fdbf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('clerk_user_id', sa.String(), nullable=True))
    op.create_unique_constraint(op.f('uq_users_clerk_user_id'), 'users', ['clerk_user_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('uq_users_clerk_user_id'), 'users', type_='unique')
    op.drop_column('users', 'clerk_user_id')
