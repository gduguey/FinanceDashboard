"""make audit timestamps timezone-aware

Converts the metadata/audit instant columns (created_at / updated_at /
dismissed_at) from naive ``TIMESTAMP`` to ``TIMESTAMPTZ``. The app has always
written these as UTC (``datetime.now(UTC)`` / ``func.now()`` on a UTC-configured
database), so every existing value is reinterpreted with ``AT TIME ZONE 'UTC'``,
which preserves the exact instant.

Deliberately excluded:
- Calendar-date columns stored as datetimes (``goals.target_date``,
  ``manual_transfers.date``, ``goal_contributions.date``,
  ``market_data.as_of_date``, ``postings.posted_at``, ``llm_usage.period_start``)
  — these mean a day, not an instant; making them tz-aware would shift the
  displayed date across timezones. They want to become ``DATE`` in a separate
  change, not ``TIMESTAMPTZ``.
- ``trades.ledger_events.event_datetime`` — load-bearing trade data that flows
  into the Polars dashboard calculations; converting it needs its own careful
  pass so those comparisons don't silently break.

Revision ID: 1f822ee413fb
Revises: 4aec4f22f9fc
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f822ee413fb'
down_revision: Union[str, Sequence[str], None] = '4aec4f22f9fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (schema, table, column) — schema=None means the public schema.
_AUDIT_COLUMNS: list[tuple[Union[str, None], str, str]] = [
    ('accounting', 'transactions', 'created_at'),
    ('accounting', 'goals', 'created_at'),
    ('accounting', 'dismissed_suggestions', 'dismissed_at'),
    ('trades', 'broker_connections', 'created_at'),
    (None, 'users', 'created_at'),
    (None, 'external_identities', 'created_at'),
    (None, 'user_secrets', 'created_at'),
    (None, 'user_secrets', 'updated_at'),
]


def upgrade() -> None:
    """Upgrade schema."""
    for schema, table, column in _AUDIT_COLUMNS:
        op.alter_column(
            table,
            column,
            schema=schema,
            existing_type=sa.DateTime(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
            # The stored naive value is already UTC — anchor it as such rather
            # than reinterpreting it in the server's local timezone.
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    """Downgrade schema."""
    for schema, table, column in _AUDIT_COLUMNS:
        op.alter_column(
            table,
            column,
            schema=schema,
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(),
            existing_nullable=False,
            # Drop the offset by expressing the instant as its UTC wall-clock,
            # matching what these columns held before the upgrade.
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )
