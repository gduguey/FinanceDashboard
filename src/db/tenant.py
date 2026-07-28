"""Which tables are tenant-scoped, derived from the schema rather than declared by hand.

Row-Level Security is this app's isolation guarantee: `app_runtime` can
`SELECT` every table, so a single forgotten `.filter_by(user_id=...)`
returns other tenants' rows unless the database itself refuses. That only
holds if *every* tenant table has a policy, and the old arrangement could
not promise it — each migration hand-copied a `_USER_SCOPED_TABLES` list,
and three tables (`transfer_links`, `transfer_linked_transactions`,
`transfer_rule_exclusions`) were simply never added to one. Nothing failed;
the tables just quietly had no policy. That is VISION-AUDIT T3.

The fix is to stop maintaining a list. `tenant_tables` *computes* the set
from `Base.metadata`: a table is tenant-scoped if and only if it has a
`user_id` column. Adding a tenant table therefore adds it to this set
automatically, the baseline migration emits its policy from the same
function, and `tests/db/test_rls_coverage.py` asserts the live database
matches. "Has `user_id`" structurally implies "has a FORCED policy", with
no step anyone can forget.

Two deliberate special cases, both encoded below rather than left implicit:
`public.users` is keyed on its own `id`, and `public.external_identities`
is exempt with its reason recorded in `RLS_EXEMPT`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from sqlalchemy import MetaData

OWNER_COLUMN = "user_id"
"""The column that makes a table tenant-scoped. Its presence is the whole rule."""

POLICY_NAME = "user_isolation"
"""The single policy name every tenant table carries, so coverage is greppable in `pg_policies`."""

_USERS_TABLE = ("public", "users")
"""`users` is tenant-scoped by its own primary key, not by a `user_id` column."""

RLS_EXEMPT: dict[tuple[str, str], str] = {
    ("public", "external_identities"): (
        "Looked up before the acting user is known — sign-in resolves a provider identity "
        "to a user_id, so a policy keyed on that user_id could never match and would lock "
        "every user out. Isolation here comes from the provider-scoped composite primary "
        "key instead."
    ),
}
"""Tables that carry `user_id` but deliberately have no policy, each with the reason.

An entry here is a documented hole in the isolation guarantee, so the
coverage test requires a non-empty reason: adding a table to this dict has
to be a sentence someone wrote, never a silent omission.
"""


class TenantTable(NamedTuple):
    """One tenant-scoped table and the column its policy compares against."""

    schema: str
    table: str
    owner_column: str

    @property
    def qualified_name(self) -> str:
        """Schema-qualified name, e.g. `accounting.postings`.

        Returns
        -------
        str
        """
        return f"{self.schema}.{self.table}"


def tenant_tables(metadata: MetaData) -> list[TenantTable]:
    """Every table that must carry a forced Row-Level Security policy.

    Computed, never configured — see this module's docstring.

    Parameters
    ----------
    metadata
        The declarative metadata to inspect, normally `db.base.Base.metadata`.

    Returns
    -------
    list[TenantTable]
        Sorted by schema then table, so migrations and tests agree on order.
        Excludes anything listed in `RLS_EXEMPT`.
    """
    found: list[TenantTable] = []
    for table in metadata.tables.values():
        schema = table.schema or "public"
        key = (schema, table.name)
        if key in RLS_EXEMPT:
            continue
        if key == _USERS_TABLE:
            found.append(TenantTable(schema, table.name, "id"))
        elif OWNER_COLUMN in table.columns:
            found.append(TenantTable(schema, table.name, OWNER_COLUMN))
    return sorted(found)


def enable_rls_statements(tenant: TenantTable) -> list[str]:
    """Build the SQL that puts one table under forced tenant isolation.

    `FORCE` matters as much as `ENABLE`: migrations run as the table owner,
    and a plain `ENABLE` does not apply to the owner, so without `FORCE` the
    policy would be silently inert for exactly the role that created it.

    The predicate wraps the setting in `NULLIF(..., '')` because
    `app.current_user_id` is an undeclared placeholder GUC — Postgres resets
    it to the empty string, not `NULL`, so a bare cast raises instead of
    returning nothing. Failing closed (zero rows) beats a 500, and beats
    returning everything.

    Parameters
    ----------
    tenant
        The table to protect.

    Returns
    -------
    list[str]
        Statements to execute in order.
    """
    quoted = f'"{tenant.schema}"."{tenant.table}"'
    predicate = f"{tenant.owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    return [
        f"ALTER TABLE {quoted} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {quoted} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {POLICY_NAME} ON {quoted} USING ({predicate}) WITH CHECK ({predicate})",
    ]
