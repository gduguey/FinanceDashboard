"""posting_override_tags join table, replacing tag_ids_override array

Revision ID: f1a2b3c4d5e6
Revises: 49df0125a9c2
Create Date: 2026-07-13 00:00:00.000000

`posting_overrides.tag_ids_override` was a raw array of tag natural keys
with no foreign key enforcement — nothing stopped an entry from pointing
at a tag that no longer exists. Replaces it with `posting_override_tags`,
a real FK-enforced join table mirroring `posting_tags`, plus a
`tags_overridden` boolean on `posting_overrides` itself to keep the
NULL-vs-empty distinction ("no override" vs "overridden to no tags") that
the array's own NULL-vs-`[]` used to carry, since a join table's row count
alone can't tell those two cases apart.

Existing `tag_ids_override` entries are migrated by joining each natural
key against `accounting.tags.natural_key` for the same user — any entry
that no longer matches a real tag (exactly the kind of stale reference
this migration exists to make impossible going forward) is silently
dropped rather than migrated, since there is no real tag left for it to
reference.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = '49df0125a9c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USER_SCOPED_TABLES: list[tuple[str, str, str]] = [
    ("accounting", "posting_override_tags", "user_id"),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'posting_overrides',
        sa.Column('tags_overridden', sa.Boolean(), nullable=False, server_default=sa.false()),
        schema='accounting',
    )
    op.execute(
        'UPDATE accounting.posting_overrides SET tags_overridden = (tag_ids_override IS NOT NULL)'
    )
    op.alter_column('posting_overrides', 'tags_overridden', server_default=None, schema='accounting')

    op.create_table(
        'posting_override_tags',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('override_id', sa.UUID(), nullable=False),
        sa.Column('tag_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ['override_id'],
            ['accounting.posting_overrides.id'],
            name=op.f('fk_posting_override_tags_override_id_posting_overrides'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['tag_id'], ['accounting.tags.id'], name=op.f('fk_posting_override_tags_tag_id_tags'), ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], name=op.f('fk_posting_override_tags_user_id_users'), ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_posting_override_tags')),
        sa.UniqueConstraint('user_id', 'override_id', 'tag_id', name='uq_posting_override_tags_user_override_tag'),
        schema='accounting',
    )

    op.execute("""
        INSERT INTO accounting.posting_override_tags (id, user_id, override_id, tag_id)
        SELECT gen_random_uuid(), po.user_id, po.id, t.id
        FROM accounting.posting_overrides po
        CROSS JOIN LATERAL unnest(po.tag_ids_override) AS tag_natural_key
        JOIN accounting.tags t ON t.user_id = po.user_id AND t.natural_key = tag_natural_key
        WHERE po.tag_ids_override IS NOT NULL
    """)

    op.drop_column('posting_overrides', 'tag_ids_override', schema='accounting')

    for schema, table, column in _USER_SCOPED_TABLES:
        op.execute(f'ALTER TABLE "{schema}"."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{schema}"."{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY user_isolation ON "{schema}"."{table}" '
            f"USING ({column} = current_setting('app.current_user_id', true)::uuid) "
            f"WITH CHECK ({column} = current_setting('app.current_user_id', true)::uuid)"
        )


def downgrade() -> None:
    """Downgrade schema.

    Rebuilds `tag_ids_override` by aggregating `posting_override_tags`
    back into an array of natural keys, then drops the join table (its
    `user_isolation` policy drops automatically along with it).
    """
    op.add_column(
        'posting_overrides', sa.Column('tag_ids_override', sa.ARRAY(sa.String()), nullable=True), schema='accounting'
    )
    op.execute("""
        UPDATE accounting.posting_overrides po
        SET tag_ids_override = agg.natural_keys
        FROM (
            SELECT pot.override_id, array_agg(t.natural_key) AS natural_keys
            FROM accounting.posting_override_tags pot
            JOIN accounting.tags t ON t.id = pot.tag_id
            GROUP BY pot.override_id
        ) AS agg
        WHERE po.id = agg.override_id
    """)
    op.execute(
        "UPDATE accounting.posting_overrides SET tag_ids_override = '{}' WHERE tags_overridden AND tag_ids_override IS NULL"
    )

    op.drop_table('posting_override_tags', schema='accounting')
    op.drop_column('posting_overrides', 'tags_overridden', schema='accounting')
