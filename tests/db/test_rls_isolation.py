"""Row-Level Security isolation: the policies keep two tenants apart, in both directions.

`test_rls_coverage.py` proves every tenant table *has* a forced policy. That
is a structural claim, read out of `pg_policies`, and it would still pass if
every policy compared a column to itself. This module proves the policies
*work*, by running real statements as the real restricted role:

- through the migrations, on the same scratch database, so what is tested is
  the schema that actually ships;
- as `app_runtime`, the login the API uses — not the owning role, which
  `FORCE ROW LEVEL SECURITY` also constrains but which holds every grant and
  so cannot demonstrate that the *grants* are right too;
- for reads and writes both. A policy that filters `SELECT` correctly and
  lets tenant A `UPDATE` tenant B's row is the same leak facing the other
  way, and `WITH CHECK` is the half PR 3's review found unasserted.

Three tables, chosen for their shapes rather than for coverage:
`public.users` (whose policy compares `id`, since the table has no
`user_id`), `accounting.accounts` and `trades.broker_connections` (one per
non-public schema). Every policy in the database is emitted by the same
`db.tenant.enable_rls_statements`, and coverage that they all exist is the
other module's job — so proving the generated predicate behaves correctly
once per shape is the guarantee, not proving it 40 times.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

from sqlalchemy.orm import Session

from db.session import create_one_shot_engine, set_rls_user
from tests.db.conftest import app_runtime_database_url

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Connection, Engine
    from sqlalchemy.engine import URL

TENANT_A = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
TENANT_B = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")

# One row per tenant per table, keyed by the statement that inserts it. Each
# carries the tenant's own id, so "tenant A reading tenant B's row" is a real
# row that exists and is deliberately invisible, not an empty table.
_SEED = (
    (
        "public.users",
        "INSERT INTO public.users (id, email, is_active) VALUES (:tenant, :email, true)",
    ),
    (
        "accounting.accounts",
        (
            "INSERT INTO accounting.accounts "
            "(user_id, natural_key, name, kind, institution, currency, meta, closed) "
            "VALUES (:tenant, :key, :key, 'checking', :institution, 'USD', '{}'::jsonb, false)"
        ),
    ),
    (
        "trades.broker_connections",
        "INSERT INTO trades.broker_connections (user_id, natural_key, broker) VALUES (:tenant, :key, 'ibkr')",
    ),
)

_TABLES = [table for table, _ in _SEED]

_INSTITUTION = "rls-isolation-test-bank"

# `users` is scoped by its own primary key; every other tenant table by
# `user_id`. The isolation question is the same, only the column differs.
_TENANT_COLUMN = {"public.users": "id"}


def _tenant_column(table: str) -> str:
    """The column a table's isolation policy compares against.

    Parameters
    ----------
    table
        Qualified table name.

    Returns
    -------
    str
    """
    return _TENANT_COLUMN.get(table, "user_id")


def _params(tenant: uuid.UUID, *, key: str) -> dict[str, object]:
    """Every bind any seed statement needs, so one dict serves all three.

    Parameters
    ----------
    tenant
        The tenant the row belongs to.
    key
        A natural key unique to this row.

    Returns
    -------
    dict[str, object]
    """
    return {"tenant": tenant, "key": key, "email": f"{key}@example.test", "institution": _INSTITUTION}


def _become(connection: Connection, tenant: uuid.UUID | None) -> None:
    """Set (or clear) the tenant the policies will compare against.

    Parameters
    ----------
    connection
        A live connection.
    tenant
        The tenant to act as, or `None` to leave the setting empty — which is
        what an un-armed request looks like, and must see nothing.
    """
    connection.execute(text("SELECT set_config('app.current_user_id', :value, false)"), {"value": str(tenant or "")})


@pytest.fixture(scope="module")
def app_runtime_engine(migrated_url: URL) -> Iterator[Engine]:
    """The migrated scratch database, reached as the restricted `app_runtime` role.

    Takes the username and password from `DATABASE_URL_APP` — the same pair
    the migration used to create the role — and everything else from the
    scratch database, which is where the role was granted its DML.

    Yields
    ------
    Engine
    """
    configured = app_runtime_database_url()
    if configured is None:
        pytest.skip("DATABASE_URL_APP is not configured")

    app_url = make_url(configured)
    engine = create_one_shot_engine(migrated_url.set(username=app_url.username, password=app_url.password))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def seeded(app_runtime_engine: Engine) -> Engine:
    """One row per tenant in each table under test, written as `app_runtime` itself.

    Seeding through the restricted role rather than the owner is deliberate:
    if `WITH CHECK` rejected a tenant writing its *own* row, these inserts
    would fail and the isolation tests below would be passing against an
    empty database.

    Parameters
    ----------
    app_runtime_engine
        The restricted-role engine.

    Returns
    -------
    Engine
        The same engine, once both tenants' rows exist.
    """
    # `accounts.institution` is a foreign key into the shared `institutions`
    # reference table, which carries no `user_id` and so has no policy — one
    # row, before either tenant's.
    with app_runtime_engine.begin() as connection:
        connection.execute(text("INSERT INTO accounting.institutions (code) VALUES (:code)"), {"code": _INSTITUTION})

    for tenant in (TENANT_A, TENANT_B):
        with app_runtime_engine.begin() as connection:
            _become(connection, tenant)
            for table, statement in _SEED:
                connection.execute(
                    text(statement),
                    _params(tenant, key=f"{table}-{tenant}"),
                )
    return app_runtime_engine


@pytest.mark.parametrize("table", _TABLES)
def test_a_tenant_reads_only_its_own_rows(seeded: Engine, table: str) -> None:
    """The read half: tenant A's `SELECT` returns A's row and never B's."""
    with seeded.connect() as connection:
        _become(connection, TENANT_A)
        query = f"SELECT {_tenant_column(table)} FROM {table}"  # noqa: S608 — module-level constants, never input
        visible = connection.execute(text(query)).scalars().all()

    assert visible == [TENANT_A], f"{table} leaked rows across tenants: {visible}"


@pytest.mark.parametrize("table", _TABLES)
def test_naming_another_tenants_rows_explicitly_still_returns_nothing(seeded: Engine, table: str) -> None:
    """A predicate that asks for B's rows by id is filtered too, not merely defaulted away."""
    column = _tenant_column(table)
    with seeded.connect() as connection:
        _become(connection, TENANT_A)
        query = f"SELECT 1 FROM {table} WHERE {column} = :other"  # noqa: S608 — `table` and `column` are this module's own constants, never input
        rows = connection.execute(text(query), {"other": TENANT_B}).all()

    assert rows == [], f"{table} returned another tenant's rows when asked for them by id"


@pytest.mark.parametrize("table", _TABLES)
def test_a_tenant_cannot_insert_a_row_owned_by_another(seeded: Engine, table: str) -> None:
    """The `WITH CHECK` half: writing a row stamped with someone else's id is refused."""
    statement = next(sql for name, sql in _SEED if name == table)
    with seeded.connect() as connection:
        _become(connection, TENANT_A)
        with pytest.raises(ProgrammingError, match="row-level security"):
            connection.execute(
                text(statement),
                _params(TENANT_B, key=f"{table}-smuggled"),
            )
        connection.rollback()


@pytest.mark.parametrize("table", _TABLES)
def test_a_tenant_cannot_update_another_tenants_rows(seeded: Engine, table: str) -> None:
    """An `UPDATE` aimed at B's row matches nothing rather than silently rewriting it."""
    column = _tenant_column(table)
    with seeded.connect() as connection:
        _become(connection, TENANT_A)
        query = f"UPDATE {table} SET updated_at = now() WHERE {column} = :other"  # noqa: S608 — module-level constants, never input
        result = connection.execute(text(query), {"other": TENANT_B})
        connection.rollback()

    assert result.rowcount == 0, f"{table} let one tenant update another's rows"


@pytest.mark.parametrize("table", _TABLES)
def test_a_tenant_cannot_delete_another_tenants_rows(seeded: Engine, table: str) -> None:
    """A `DELETE` aimed at B's row matches nothing — the destructive version of the same leak."""
    column = _tenant_column(table)
    with seeded.connect() as connection:
        _become(connection, TENANT_A)
        query = f"DELETE FROM {table} WHERE {column} = :other"  # noqa: S608 — module-level constants, never input
        result = connection.execute(text(query), {"other": TENANT_B})
        connection.rollback()

    assert result.rowcount == 0, f"{table} let one tenant delete another's rows"


@pytest.mark.parametrize("table", _TABLES)
def test_an_unset_tenant_sees_nothing(seeded: Engine, table: str) -> None:
    """Fails closed: a connection that never armed `app.current_user_id` reads zero rows.

    `test_rls_coverage.py` asserts the predicate contains `NULLIF`. This
    asserts what that spelling is for — and that an empty setting returns
    nothing rather than raising, which would turn a missing `set_rls_user`
    into a 500 instead of an empty page.
    """
    with seeded.connect() as connection:
        _become(connection, None)
        query = f"SELECT 1 FROM {table}"  # noqa: S608 — module-level constants, never input
        rows = connection.execute(text(query)).all()

    assert rows == [], f"{table} was readable with no tenant set"


@pytest.mark.parametrize("table", _TABLES)
def test_an_unset_tenant_cannot_write(seeded: Engine, table: str) -> None:
    """Failing closed applies to writes too, not only to reads."""
    statement = next(sql for name, sql in _SEED if name == table)
    with seeded.connect() as connection:
        _become(connection, None)
        with pytest.raises(ProgrammingError, match="row-level security"):
            connection.execute(
                text(statement),
                _params(TENANT_A, key=f"{table}-unarmed"),
            )
        connection.rollback()


def test_the_restricted_role_cannot_turn_the_policies_off(seeded: Engine) -> None:
    """`app_runtime` holds DML and nothing more — it cannot disable what constrains it.

    Isolation enforced by a policy the constrained role may itself drop is not
    enforced at all. `FORCE ROW LEVEL SECURITY` binds the owner; this is the
    other half — that the login the API actually uses is not the owner.
    """
    with seeded.connect() as connection:
        _become(connection, TENANT_A)
        with pytest.raises(ProgrammingError, match=r"must be owner|permission denied"):
            connection.execute(text("ALTER TABLE accounting.accounts DISABLE ROW LEVEL SECURITY"))
        connection.rollback()


def test_the_restricted_role_is_not_a_superuser_and_cannot_bypass_rls(seeded: Engine) -> None:
    """A `BYPASSRLS` or superuser login would make every policy above vacuous."""
    with seeded.connect() as connection:
        role = connection.execute(
            text("SELECT rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname = current_user")
        ).one()

    assert not role.rolsuper, "app_runtime is a superuser, so RLS does not apply to it"
    assert not role.rolbypassrls, "app_runtime holds BYPASSRLS, so every isolation policy is decorative"
    assert not role.rolcreaterole, "app_runtime can create roles, and so can grant itself a way around RLS"


def test_a_read_that_commits_mid_request_stays_scoped_to_its_tenant(seeded: Engine) -> None:
    """`set_rls_user` is transaction-local, so anything that commits and then reads has to re-arm it.

    Almost every write path commits at the end of the request and reads
    nothing afterwards, which is why this stayed invisible for so long.
    `repositories.projection.drain` breaks that pattern deliberately: it is a
    *read* path that writes, filling the resolved projection before the
    request queries it, and it commits so the recomputed rows and the claimed
    staleness markers land together.

    What makes the failure worth its own test is its shape. Postgres resets
    an undeclared GUC to the empty string rather than to null, and
    `db.tenant.enable_rls_statements` wraps the setting in `NULLIF(..., '')`
    so an unset tenant fails *closed* — the right choice, and the reason this
    does not raise. Every query after the commit simply matches nothing, and
    the endpoint answers 200 with an empty page.

    Uses `db.session.set_rls_user` rather than this module's own `_become`,
    and that distinction is the whole point: `_become` sets the GUC at
    *session* scope (`is_local=false`) so it survives a commit, while
    production sets it at *transaction* scope so a pooled connection cannot
    leak one request's tenant into the next. Only the production spelling can
    exhibit this.

    Asserted here rather than in the accounting suite because only this
    module runs under a real policy: `tests/conftest.py` builds its schema
    with `Base.metadata.create_all`, which creates none, so a missing re-arm
    is undetectable there by construction.
    """
    with Session(bind=seeded) as session:
        set_rls_user(session, TENANT_A)
        before = session.execute(text("SELECT count(*) FROM accounting.accounts")).scalar_one()
        assert before == 1, "the seed should give tenant A exactly one account to see"

        session.commit()
        after_commit = session.execute(text("SELECT count(*) FROM accounting.accounts")).scalar_one()

        set_rls_user(session, TENANT_A)
        after_rearming = session.execute(text("SELECT count(*) FROM accounting.accounts")).scalar_one()

    assert after_commit == 0, (
        "a committed transaction is expected to drop `app.current_user_id` — if this ever stops being true, "
        "the re-arm at the end of `repositories.projection.drain` is dead code and should go with this test"
    )
    assert after_rearming == 1, "re-arming after the commit did not restore the tenant's own rows"


def test_the_projection_drain_re_arms_the_session_it_committed(seeded: Engine) -> None:
    """The fix for the case above, asserted on the function that needs it rather than on the mechanism.

    `drain` over a tenant with nothing dirty does no work and must not
    disturb the session; over a tenant with something dirty it commits, and
    the caller has to be able to keep reading afterwards. Both are the same
    assertion from the request's point of view: after `drain`, this session
    can still see its own rows.
    """
    from accounting.repositories.projection import drain  # noqa: PLC0415 — keeps this module importable without `api`

    with Session(bind=seeded) as session:
        set_rls_user(session, TENANT_A)
        session.execute(
            text("INSERT INTO accounting.resolved_postings_dirty (user_id, transaction_id) VALUES (:t, :t)"),
            {"t": TENANT_A},
        )
        session.commit()
        set_rls_user(session, TENANT_A)

        assert drain(session, TENANT_A) == 1, "the marker should have been claimed"
        assert session.execute(text("SELECT count(*) FROM accounting.accounts")).scalar_one() == 1, (
            "the session could not see its own rows after `drain` committed"
        )
