"""add transfer_rule_exclusions table

Revision ID: 274f8b1a5322
Revises: c3d4e5f6a7b8
Create Date: 2026-07-18 15:53:19.429454

Lets one specific transaction opt out of matching one otherwise-applicable
`TransferRule`, without disabling the rule for anything else it correctly
resolves. Shaped like `posting_merge_duplicates` (a join table with a real
foreign key into `transactions`), not a JSON array of ids, for the same
reason that one is: `transaction_id` values are deterministic
(`db.base.derive_id`), so a lasting reference into `transactions` survives
a ledger rebuild.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '274f8b1a5322'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'transfer_rule_exclusions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('rule_id', sa.UUID(), nullable=False),
        sa.Column('transaction_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ['rule_id'],
            ['accounting.transfer_rules.id'],
            name=op.f('fk_transfer_rule_exclusions_rule_id_transfer_rules'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['transaction_id'],
            ['accounting.transactions.id'],
            name=op.f('fk_transfer_rule_exclusions_transaction_id_transactions'),
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], name=op.f('fk_transfer_rule_exclusions_user_id_users'), ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_transfer_rule_exclusions')),
        sa.UniqueConstraint('user_id', 'rule_id', 'transaction_id', name='uq_transfer_rule_exclusions_user_rule_txn'),
        schema='accounting',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('transfer_rule_exclusions', schema='accounting')
