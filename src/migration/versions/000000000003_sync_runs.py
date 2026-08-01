"""Sync runs: the durable record a background broker sync reports into.

Additive, like `000000000002` — one new table, its indexes, its grant and
its Row-Level Security policy. Deploying it is `git pull` and a restart,
with Alembic running on boot; nothing existing is altered and no row is
migrated. The table starts empty and the first `POST /sync-runs` fills it,
so there is deliberately no backfill step for an operator to run.

The interesting object here is `uq_sync_runs_active_user`, a **partial**
unique index over `user_id` where the run is still `queued` or `running`. It
is what makes "one sync in flight per user" a fact about the database rather
than about one Python process — it replaces a per-process
`dict[uuid.UUID, threading.Lock]` that had the same defect as the
in-process progress dict this whole change removes. `ix_sync_runs_user_id`
sits beside it and is not redundant: a partial index cannot serve the
unqualified `user_id` lookup that the RLS policy and the cascade from
`users` both make.

Revision ID: 000000000003
Revises: 000000000002
Create Date: 2026-08-01

"""

from collections.abc import Sequence
from typing import get_args

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import trades.db  # noqa: F401 — registers the trades tables, including the one this revision creates
from db.base import Base, check_in_sql
from db.tenant import POLICY_NAME, enable_rls_statements, tenant_tables
from trades.db.models import ACTIVE_SYNC_RUN_STATES, SyncRunState

# revision identifiers, used by Alembic.
revision: str = "000000000003"
down_revision: str | Sequence[str] | None = "000000000002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "trades"
_TABLE = "sync_runs"
_ACTIVE_STATES_SQL = ", ".join(repr(state) for state in ACTIVE_SYNC_RUN_STATES)


def upgrade() -> None:
    """Create the table, both its indexes, its grant and its isolation policy."""
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("public.uuid7()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("step", sa.String(), nullable=False),
        sa.Column("percent", sa.Float(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("synced_at", sa.String(), nullable=True),
        sa.Column("new_event_count", sa.Integer(), nullable=False),
        sa.Column("total_event_count", sa.Integer(), nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # Both the CHECK and the partial index's predicate come from the
        # model's own declaration rather than being transcribed. Alembic's
        # autogenerate compares neither, so a copy here is a second definition
        # that `alembic check` could not tell had drifted — the same reasoning
        # `000000000002` gives for its triggers and policies.
        sa.CheckConstraint(check_in_sql("state", get_args(SyncRunState)), name=op.f("ck_sync_runs_state")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_sync_runs_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_runs")),
        schema=_SCHEMA,
    )
    op.create_index("ix_sync_runs_user_id", _TABLE, ["user_id"], unique=False, schema=_SCHEMA)
    op.create_index(
        "uq_sync_runs_active_user",
        _TABLE,
        ["user_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text(f"state IN ({_ACTIVE_STATES_SQL})"),
    )

    # The baseline's `GRANT ... ON ALL TABLES` does not reach a table created
    # by a later revision, so this one needs its own — same as `000000000002`.
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{_SCHEMA}"."{_TABLE}" TO app_runtime')

    for tenant in tenant_tables(Base.metadata):
        if (tenant.schema, tenant.table) == (_SCHEMA, _TABLE):
            for statement in enable_rls_statements(tenant):
                op.execute(statement)


def downgrade() -> None:
    """Drop the policy, both indexes and the table."""
    op.execute(f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{_SCHEMA}"."{_TABLE}"')
    op.drop_index("uq_sync_runs_active_user", table_name=_TABLE, schema=_SCHEMA)
    op.drop_index("ix_sync_runs_user_id", table_name=_TABLE, schema=_SCHEMA)
    op.drop_table(_TABLE, schema=_SCHEMA)
