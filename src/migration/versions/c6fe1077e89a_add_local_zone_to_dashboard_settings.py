"""add local_zone to dashboard_settings

Revision ID: c6fe1077e89a
Revises: d8ff30cf3dd1
Create Date: 2026-07-12 23:43:30.534490

Browser-detected (`Intl.DateTimeFormat().resolvedOptions().timeZone`), not
user-picked from a list — same free-form IANA zone name
`trades.config.TimezoneConfig.local_zone` already validates against, so no
`CheckConstraint` here the way `tax_regime` has one (that's a closed
two-value enum; this isn't). `NULL` means "never reported one yet" (a
user who hasn't loaded the frontend since this shipped), resolved to
`TimezoneConfig.local_zone`'s own default the same way `tax_regime` unset
resolves to `RESIDENT` — see `trades.dashboard.settings.resolved_local_zone`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c6fe1077e89a'
down_revision: Union[str, Sequence[str], None] = 'd8ff30cf3dd1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('dashboard_settings', sa.Column('local_zone', sa.String(), nullable=True), schema='trades')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('dashboard_settings', 'local_zone', schema='trades')
