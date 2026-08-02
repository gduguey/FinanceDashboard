"""Narrow the `categories` staleness trigger to the postings filed under the changed category.

One `CREATE OR REPLACE FUNCTION`. No table, no column, no row: this replaces
the body of `accounting.mark_resolved_dirty_categories`, which the four
`resolved_dirty_categories_*` triggers already installed by `000000000002`
call. A trigger names its function and nothing about its body, so the
triggers themselves are untouched and there is nothing to drop and recreate.

**Why this is a revision at all**, given that the definition lives in
`accounting.db.projection` and both the migration and the test suite's
`after_create` hook read it from there. Because a database that has already
run `000000000002` will never run it again — `git pull` and a restart would
leave the old, whole-ledger function in place, and nothing would notice:
`tests/accounting/test_resolution_sources.py` checks that every declared
table *carries* its four triggers and that each has a function, and neither
it nor `alembic check` compares a function *body*. The failure would be
invisible and permanent. Editing `000000000002` in place would have been
correct only for a database built from scratch.

Deploying is `git pull` and a restart, with Alembic running on boot. Nothing
is migrated and no operator step exists. Over-invalidation is the safe
direction, so even a database that somehow missed this is slow rather than
wrong.

Revision ID: 000000000004
Revises: 000000000003
Create Date: 2026-08-02

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from accounting.db.core import SCHEMA
from accounting.db.projection import AFFECTED_TRANSACTIONS, trigger_function_statement

# revision identifiers, used by Alembic.
revision: str = "000000000004"
down_revision: str | Sequence[str] | None = "000000000003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "categories"

_WHOLE_LEDGER_BEFORE_THIS_REVISION = f"""
    SELECT t.user_id, t.id
    FROM {SCHEMA}.transactions AS t
    WHERE t.user_id IN (SELECT DISTINCT user_id FROM changed)
"""  # noqa: S608 — `SCHEMA` is the application's own constant, never caller input
"""What `AFFECTED_TRANSACTIONS["categories"]` mapped to before this revision.

Frozen here rather than imported, because `downgrade` has to install the
fragment this revision *replaced* and that fragment no longer describes
anything the application believes. A revision that read its own downgrade
out of live code would stop being reversible the moment the live code moved
again.
"""


def upgrade() -> None:
    """Replace the function body with the narrow mapping."""
    op.execute(text(trigger_function_statement(_TABLE, AFFECTED_TRANSACTIONS[_TABLE])))


def downgrade() -> None:
    """Put the whole-ledger mapping back."""
    op.execute(text(trigger_function_statement(_TABLE, _WHOLE_LEDGER_BEFORE_THIS_REVISION)))
