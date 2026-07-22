"""add version column to transfer_rules

Revision ID: b369b4122538
Revises: 47cd96c87fec
Create Date: 2026-07-20 18:18:57.683418

Backs `PATCH /transfer-rules/{rule_id}`'s per-row optimistic concurrency
(`db.base.check_and_bump_row_version`) — the row-scoped counterpart to the
whole-store `accounting.store_versions` counter, so editing one rule can
never spuriously conflict with, or be silently overwritten by, an
unrelated save elsewhere in the store. `server_default='1'` backfills
every existing row (matching the ORM's own Python-side `default=1`, see
`accounting.db.automation.TransferRule.version`), then gets dropped once
that backfill has run — new rows go through the ORM/an explicit `1` in
`accounting.store._upsert_transfer_rules_and_prune`'s own INSERT from then
on, not a standing DB-level default.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b369b4122538'
down_revision: Union[str, Sequence[str], None] = '47cd96c87fec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'transfer_rules', sa.Column('version', sa.Integer(), nullable=False, server_default='1'), schema='accounting'
    )
    op.alter_column('transfer_rules', 'version', server_default=None, schema='accounting')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('transfer_rules', 'version', schema='accounting')
