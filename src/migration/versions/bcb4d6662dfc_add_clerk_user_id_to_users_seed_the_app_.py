"""add clerk_user_id to users, seed the app owner's row

Revision ID: bcb4d6662dfc
Revises: 36de9420fdbf
Create Date: 2026-07-12 11:46:46.033522

Every table with a `user_id` column has FK-referenced `users.id` since
`36de9420fdbf` — but nothing had ever inserted a row into `users` itself,
so every write to any of those ~28 tables has been failing with a foreign
key violation since that migration landed (independently of Clerk).
Fixed here by seeding exactly one row, for the app's own owner
(`g630du@gmail.com`, Clerk id confirmed via `clerk users list`) — future
users (sent an invite, per the app's restricted Clerk sign-up mode) get
their own row automatically from the Clerk webhook instead (see
`trades.api.webhooks`), computing the same id the same way.

`_CLERK_ID_NAMESPACE`/the id formula below are copied inline rather than
imported from `db.current_user` — a data migration should keep computing
the exact id it always computed, even if that module's implementation
ever changes later.
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bcb4d6662dfc'
down_revision: Union[str, Sequence[str], None] = '36de9420fdbf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CLERK_ID_NAMESPACE = uuid.UUID("bf2457c3-9a9e-4bd4-998e-ab107917b75e")
_OWNER_CLERK_USER_ID = "user_3GOCkamL0sEuBBesmedUlCSwEkT"
_OWNER_EMAIL = "g630du@gmail.com"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('clerk_user_id', sa.String(), nullable=True))
    op.create_unique_constraint(op.f('uq_users_clerk_user_id'), 'users', ['clerk_user_id'])

    owner_id = uuid.uuid5(_CLERK_ID_NAMESPACE, _OWNER_CLERK_USER_ID)
    op.get_bind().execute(
        sa.text(
            "INSERT INTO users (id, email, clerk_user_id, hashed_password, is_active, is_superuser, is_verified) "
            "VALUES (:id, :email, :clerk_user_id, :hashed_password, true, false, true) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": str(owner_id),
            "email": _OWNER_EMAIL,
            "clerk_user_id": _OWNER_CLERK_USER_ID,
            "hashed_password": "unset",  # noqa: S106 — vestigial column, see db.models.User's own docstring
        },
    )


def downgrade() -> None:
    """Downgrade schema."""
    owner_id = uuid.uuid5(_CLERK_ID_NAMESPACE, _OWNER_CLERK_USER_ID)
    op.get_bind().execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": str(owner_id)})
    op.drop_constraint(op.f('uq_users_clerk_user_id'), 'users', type_='unique')
    op.drop_column('users', 'clerk_user_id')
