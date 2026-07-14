"""index postings(user_id, posted_at)

Revision ID: c3d4e5f6a7b8
Revises: a7b8c9d0e1f2
Create Date: 2026-07-14 00:00:00.000000

Every dashboard/report query filters `postings` by `user_id` (via RLS)
and usually by a date range too, but nothing indexed either column —
confirmed by checking every prior migration, `postings` had no index
beyond its own primary key and the unrelated budget uniqueness
constraints. This is a pure win: nothing reads `postings` without
filtering by `user_id` at minimum, so there's no query this could make
slower.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        'ix_postings_user_posted_at', 'postings', ['user_id', 'posted_at'], unique=False, schema='accounting'
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_postings_user_posted_at', table_name='postings', schema='accounting')
