"""add real foreign keys for account, goal, and budget references

Revision ID: 36de9420fdbf
Revises: 45a32dc7e644
Create Date: 2026-07-11 15:48:26.821373

Converts six columns that used to hold a bare natural-key *string* pointing
at another table — `TransferRule.account_id`/`counterparty_account_id`,
`Posting.budget_id`, `GoalContribution.goal_id`, `RecurringAddition.goal_id`,
`WithdrawalPriorityEntry.goal_id` — into real `uuid` foreign keys, enforced
by Postgres. This removes the "create a rule/contribution referencing
something that doesn't exist yet" capability entirely, matching how
`category_id`/`subcategory_id` already worked everywhere else in this
schema — see the two migrations this one reverses the reasoning of,
`79850202e325` ("drop transfer_rules account fks for forward references")
and `1651bbdb25cc` ("drop unenforced goal_id fks matching list-replace
endpoints").

`db.base.derive_id` (the function that turns a `(user_id, table,
natural_key)` triple into a deterministic uuid) is a plain Python function,
not SQL, so every new column here is populated in Python: read every row
with a non-null old string value, compute its target id, and write it back
with a parameterized `UPDATE` — see `_populate_new_column` below.

Before any column is actually dropped or constrained, every one of the six
is checked for orphans — a natural-key reference that doesn't resolve to
any real row in the referenced table, for that same user. If any are
found, `RuntimeError` is raised and nothing is changed, so a bad reference
already sitting in the database is never silently dropped or nulled by
this migration.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

from db.base import derive_id

# revision identifiers, used by Alembic.
revision: str = '36de9420fdbf'
down_revision: Union[str, Sequence[str], None] = '45a32dc7e644'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEMA = "accounting"

# (table, column, referenced table — which is also the name `derive_id` was
# originally called with, e.g. `derive_id(user_id, "accounts", ...)` — and
# whether the column is NOT NULL).
_CONVERSIONS: list[tuple[str, str, str, bool]] = [
    ("transfer_rules", "account_id", "accounts", False),
    ("transfer_rules", "counterparty_account_id", "accounts", False),
    ("postings", "budget_id", "budgets", False),
    ("goal_contributions", "goal_id", "goals", True),
    ("recurring_additions", "goal_id", "goals", True),
    ("withdrawal_priority_entries", "goal_id", "goals", True),
]

_NEW_COLUMN_SUFFIX = "_new"


def _populate_new_column(bind: Connection, table: str, old_column: str, new_column: str, referenced_table: str) -> None:
    """Compute every row's new uuid from its old natural-key string, in Python, and write it back.

    Parameters
    ----------
    bind
        The migration's live connection.
    table
        The table being converted, e.g. `"transfer_rules"`.
    old_column
        The existing string column, e.g. `"account_id"`.
    new_column
        The new nullable uuid column already added alongside it.
    referenced_table
        The table `old_column` points at, e.g. `"accounts"` — also the
        table name `derive_id` was originally called with when this value
        was first written.
    """
    rows = bind.execute(
        text(f'SELECT id, user_id, "{old_column}" FROM {_SCHEMA}.{table} WHERE "{old_column}" IS NOT NULL')  # noqa: S608
    ).fetchall()
    for row in rows:
        new_id = derive_id(row.user_id, referenced_table, getattr(row, old_column))
        bind.execute(
            text(f'UPDATE {_SCHEMA}.{table} SET "{new_column}" = :new_id WHERE id = :row_id'),  # noqa: S608
            {"new_id": new_id, "row_id": row.id},
        )


def _count_orphans(bind: Connection, table: str, new_column: str, referenced_table: str) -> int:
    """Count rows whose new uuid column is set but doesn't match any real row in `referenced_table`, per user."""
    result = bind.execute(
        text(  # noqa: S608
            f'SELECT count(*) FROM {_SCHEMA}.{table} t '
            f'WHERE t."{new_column}" IS NOT NULL '
            f"AND NOT EXISTS ("
            f'  SELECT 1 FROM {_SCHEMA}.{referenced_table} r '
            f'  WHERE r.id = t."{new_column}" AND r.user_id = t.user_id'
            f")"
        )
    ).scalar_one()
    return int(result)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    for table, column, referenced_table, _not_null in _CONVERSIONS:
        new_column = f"{column}{_NEW_COLUMN_SUFFIX}"
        op.add_column(table, sa.Column(new_column, sa.UUID(), nullable=True), schema=_SCHEMA)
        _populate_new_column(bind, table, column, new_column, referenced_table)

    orphans = [
        (table, column, referenced_table, count)
        for table, column, referenced_table, _not_null in _CONVERSIONS
        if (count := _count_orphans(bind, table, f"{column}{_NEW_COLUMN_SUFFIX}", referenced_table)) > 0
    ]
    if orphans:
        details = "\n".join(
            f"  {_SCHEMA}.{table}.{column}: {count} row(s) reference a {referenced_table} row that doesn't exist"
            for table, column, referenced_table, count in orphans
        )
        message = f"Aborting migration {revision} — found orphaned natural-key references:\n{details}"
        raise RuntimeError(message)

    for table, column, referenced_table, not_null in _CONVERSIONS:
        new_column = f"{column}{_NEW_COLUMN_SUFFIX}"
        op.drop_column(table, column, schema=_SCHEMA)
        op.alter_column(table, new_column, new_column_name=column, schema=_SCHEMA)
        if not_null:
            op.alter_column(table, column, nullable=False, schema=_SCHEMA)
        op.create_foreign_key(
            op.f(f"fk_{table}_{column}_{referenced_table}"),
            table,
            referenced_table,
            [column],
            ["id"],
            source_schema=_SCHEMA,
            referent_schema=_SCHEMA,
        )


def _populate_string_column(
    bind: Connection, table: str, uuid_column: str, string_column: str, referenced_table: str
) -> None:
    """Reverse of `_populate_new_column`: fill a fresh string column from the referenced table's own `natural_key`."""
    bind.execute(
        text(  # noqa: S608
            f'UPDATE {_SCHEMA}.{table} t SET "{string_column}" = r.natural_key '
            f'FROM {_SCHEMA}.{referenced_table} r '
            f'WHERE r.id = t."{uuid_column}"'
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()

    for table, column, referenced_table, _not_null in _CONVERSIONS:
        op.drop_constraint(op.f(f"fk_{table}_{column}_{referenced_table}"), table, schema=_SCHEMA, type_="foreignkey")

    for table, column, referenced_table, not_null in _CONVERSIONS:
        old_column = f"{column}{_NEW_COLUMN_SUFFIX}"
        op.add_column(table, sa.Column(old_column, sa.String(), nullable=True), schema=_SCHEMA)
        _populate_string_column(bind, table, column, old_column, referenced_table)
        op.drop_column(table, column, schema=_SCHEMA)
        op.alter_column(table, old_column, new_column_name=column, schema=_SCHEMA)
        if not_null:
            op.alter_column(table, column, nullable=False, schema=_SCHEMA)
