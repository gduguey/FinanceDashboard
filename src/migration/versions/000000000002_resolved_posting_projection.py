"""Resolved-posting projection: the cached rows, the staleness queue, and the triggers that fill it.

The first revision on top of the baseline. It is additive — two new tables,
their indexes, their Row-Level Security policies, and the trigger set — so
deploying it is `git pull` and a restart, with Alembic running on boot.
Nothing existing is altered and no row is migrated: the projection starts
empty and the first read of each user's transactions fills it, because
`repositories.projection.drain` treats "no projection row and no dirty row"
the same way it treats a cold cache. There is deliberately no backfill step
for an operator to run.

The triggers come from `accounting.db.projection` rather than being
transcribed here, for the reason the baseline gives for
`ZERO_SUM_STATEMENTS`: Alembic's autogenerate does not see triggers at all,
so a copy here would be a second definition that could drift from the one
the test suite installs. Same for the RLS policies, which come from
`db.tenant.tenant_tables` — the two new tables both carry `user_id`, so they
are picked up structurally rather than being named.

Revision ID: 000000000002
Revises: 000000000001
Create Date: 2026-07-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import accounting.db  # noqa: F401 — registers the accounting tables, including the two this revision creates
from accounting.db.projection import DIRTY_TABLE, PROJECTION_TABLE, PROJECTION_TRIGGER_STATEMENTS
from accounting.precedence import RESOLUTION_TRIGGERED_TABLES
from db.base import Base
from db.tenant import POLICY_NAME, enable_rls_statements, tenant_tables

# revision identifiers, used by Alembic.
revision: str = "000000000002"
down_revision: str | Sequence[str] | None = "000000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "accounting"
_NEW_TABLES = (PROJECTION_TABLE, DIRTY_TABLE)


def upgrade() -> None:
    """Create both tables, their policies, and one staleness trigger set per resolution input table."""
    op.create_table(
        PROJECTION_TABLE,
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("posting_id", sa.String(), nullable=False),
        sa.Column("transaction_id", sa.String(), nullable=False),
        sa.Column("transaction_row_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("posted_at", sa.DateTime(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("category_id", sa.String(), nullable=True),
        sa.Column("subcategory_id", sa.String(), nullable=True),
        sa.Column("budget_id", sa.String(), nullable=True),
        sa.Column("tag_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pending_source", sa.String(), nullable=True),
        sa.Column("pending_selected", sa.Boolean(), nullable=False),
        sa.Column("resolved_by_transfer_rule_id", sa.String(), nullable=True),
        sa.Column("manual_transfer_override_posting_id", sa.String(), nullable=True),
        sa.Column("is_linked_transfer", sa.Boolean(), nullable=False),
        sa.Column("linked_transaction_id", sa.String(), nullable=True),
        sa.Column("transfer_link_source", sa.String(), nullable=True),
        sa.Column("is_real_income_expense", sa.Boolean(), nullable=False),
        sa.Column("is_excluded_from_rule", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["currency"], ["currencies.code"], name=op.f("fk_resolved_postings_currency_currencies")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_resolved_postings_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "posting_id", name=op.f("pk_resolved_postings")),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_resolved_postings_user_account", PROJECTION_TABLE, ["user_id", "account_id"], unique=False, schema=_SCHEMA
    )
    op.create_index(
        "ix_resolved_postings_user_category", PROJECTION_TABLE, ["user_id", "category_id"], unique=False, schema=_SCHEMA
    )
    op.create_index(
        "ix_resolved_postings_user_posted_at",
        PROJECTION_TABLE,
        ["user_id", "posted_at", "transaction_id"],
        unique=False,
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_resolved_postings_user_transaction_row",
        PROJECTION_TABLE,
        ["user_id", "transaction_row_id"],
        unique=False,
        schema=_SCHEMA,
    )
    op.create_table(
        DIRTY_TABLE,
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_resolved_postings_dirty_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "transaction_id", name=op.f("pk_resolved_postings_dirty")),
        schema=_SCHEMA,
    )

    # `app_runtime` already holds schema-wide DML from the baseline's
    # `GRANT ... ON ALL TABLES`, which does *not* cover tables created later —
    # so both new tables need their own grant, and the read path genuinely
    # writes to them (see `repositories.projection.drain`).
    for table in _NEW_TABLES:
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{_SCHEMA}"."{table}" TO app_runtime')

    new = {(_SCHEMA, table) for table in _NEW_TABLES}
    for tenant in tenant_tables(Base.metadata):
        if (tenant.schema, tenant.table) in new:
            for statement in enable_rls_statements(tenant):
                op.execute(statement)

    for statement in PROJECTION_TRIGGER_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Drop the trigger set and both tables. The functions go with `DROP ... CASCADE` on their tables."""
    for table in sorted(RESOLUTION_TRIGGERED_TABLES):
        op.execute(f'DROP FUNCTION IF EXISTS "{_SCHEMA}".mark_resolved_dirty_{table}() CASCADE')
    op.execute(f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{_SCHEMA}"."{DIRTY_TABLE}"')
    op.execute(f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{_SCHEMA}"."{PROJECTION_TABLE}"')
    op.drop_table(DIRTY_TABLE, schema=_SCHEMA)
    op.drop_index("ix_resolved_postings_user_transaction_row", table_name=PROJECTION_TABLE, schema=_SCHEMA)
    op.drop_index("ix_resolved_postings_user_posted_at", table_name=PROJECTION_TABLE, schema=_SCHEMA)
    op.drop_index("ix_resolved_postings_user_category", table_name=PROJECTION_TABLE, schema=_SCHEMA)
    op.drop_index("ix_resolved_postings_user_account", table_name=PROJECTION_TABLE, schema=_SCHEMA)
    op.drop_table(PROJECTION_TABLE, schema=_SCHEMA)
