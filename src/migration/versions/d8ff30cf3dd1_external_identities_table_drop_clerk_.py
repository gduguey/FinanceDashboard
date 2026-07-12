"""external_identities table, drop clerk_user_id and email uniqueness from users

Revision ID: d8ff30cf3dd1
Revises: bcb4d6662dfc
Create Date: 2026-07-12 13:25:52.228328

Replaces the formula-derived id / `users.clerk_user_id` column approach
from `bcb4d6662dfc` with a real, explicit mapping table — see
`db.external_identities`'s own module docstring for the full reasoning
(keeping `users` provider-agnostic; making a future account reassignment,
e.g. after a delete-and-re-invite in Clerk, a one-row update instead of a
31-table id migration). `email`'s uniqueness is dropped in the same
migration for a related reason: `db.models.User`'s own docstring explains
why a delete-and-re-invite of the same address must not collide with the
first signup's now-orphaned row.

Data migration, not just a schema change: any row that already has a
`clerk_user_id` (today, only the app owner's seeded row from
`bcb4d6662dfc`) gets its link copied into `external_identities` before
that column is dropped — nothing here is losing information.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8ff30cf3dd1'
down_revision: Union[str, Sequence[str], None] = 'bcb4d6662dfc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'external_identities',
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('external_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_external_identities_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('provider', 'external_id', name=op.f('pk_external_identities')),
    )

    # Carry over any existing clerk_user_id link before the column that held it is dropped below.
    op.execute(
        "INSERT INTO external_identities (provider, external_id, user_id) "
        "SELECT 'clerk', clerk_user_id, id FROM users WHERE clerk_user_id IS NOT NULL"
    )

    op.drop_constraint(op.f('uq_users_clerk_user_id'), 'users', type_='unique')
    op.drop_constraint(op.f('uq_users_email'), 'users', type_='unique')
    op.drop_column('users', 'clerk_user_id')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('users', sa.Column('clerk_user_id', sa.VARCHAR(), autoincrement=False, nullable=True))
    op.create_unique_constraint(op.f('uq_users_email'), 'users', ['email'], postgresql_nulls_not_distinct=False)
    op.create_unique_constraint(op.f('uq_users_clerk_user_id'), 'users', ['clerk_user_id'], postgresql_nulls_not_distinct=False)

    op.execute(
        "UPDATE users SET clerk_user_id = external_identities.external_id "
        "FROM external_identities "
        "WHERE external_identities.provider = 'clerk' AND external_identities.user_id = users.id"
    )

    op.drop_table('external_identities')
