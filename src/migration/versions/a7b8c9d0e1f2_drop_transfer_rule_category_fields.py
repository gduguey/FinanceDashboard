"""drop transfer_rules.category_id/subcategory_id

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-07-13 00:00:00.000001

A `TransferRule` resolves a posting's counterparty, never its category:
repointing a counterparty at a real account you hold makes the
transaction an internal transfer, which is never categorizable in the
first place (see `dashboard.income_statement.real_income_expense_legs`
and `accounting.models.TransferRule`'s own docstring) — so these two
columns had nothing downstream that could ever surface their value.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        op.f('fk_transfer_rules_category_id_categories'), 'transfer_rules', schema='accounting', type_='foreignkey'
    )
    op.drop_constraint(
        op.f('fk_transfer_rules_subcategory_id_categories'), 'transfer_rules', schema='accounting', type_='foreignkey'
    )
    op.drop_column('transfer_rules', 'category_id', schema='accounting')
    op.drop_column('transfer_rules', 'subcategory_id', schema='accounting')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('transfer_rules', sa.Column('category_id', sa.UUID(), nullable=True), schema='accounting')
    op.add_column('transfer_rules', sa.Column('subcategory_id', sa.UUID(), nullable=True), schema='accounting')
    op.create_foreign_key(
        op.f('fk_transfer_rules_category_id_categories'),
        'transfer_rules',
        'categories',
        ['category_id'],
        ['id'],
        source_schema='accounting',
        referent_schema='accounting',
    )
    op.create_foreign_key(
        op.f('fk_transfer_rules_subcategory_id_categories'),
        'transfer_rules',
        'categories',
        ['subcategory_id'],
        ['id'],
        source_schema='accounting',
        referent_schema='accounting',
    )
