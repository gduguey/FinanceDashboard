"""Row-Level Security coverage: every tenant table has a forced policy, with no exceptions.

This is the test VISION-AUDIT T3 says did not exist. Three tables shipped
with a `user_id` column and no policy, and nothing noticed, because RLS was
applied by hand-copying a list into each migration and coverage was never
asserted anywhere.

It runs against its **own** database, built by running the real Alembic
migrations, for two reasons the main suite's fixture cannot satisfy:

- The main `_db_engine` fixture builds the schema with
  `Base.metadata.create_all`, which creates no policies at all — a
  coverage test there would be asserting against a database that never has
  any, and would pass or fail for the wrong reason.
- Turning `FORCE ROW LEVEL SECURITY` on in the shared fixture would break
  every other Postgres-backed test, since they connect as the table owner
  without setting `app.current_user_id`.

So this module provisions a scratch database, migrates it, introspects
`pg_policies`, and drops it. The broader `app_runtime` isolation suite
(proving one tenant genuinely cannot read another's rows through the
restricted role) is PR5's.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, NamedTuple

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

import accounting.db  # noqa: F401 — registers the accounting tables on Base.metadata
import trades.db  # noqa: F401 — registers the trades tables on Base.metadata
from db.base import Base
from db.session import create_one_shot_engine
from db.settings import TestDatabaseSettings
from db.tenant import POLICY_NAME, RLS_EXEMPT, is_reference_table, tenant_tables

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine


def _test_database_url() -> str | None:
    """The configured test database URL, or `None` if it is unset or unreachable.

    Returns
    -------
    str or None
    """
    try:
        url = TestDatabaseSettings().database_url  # type: ignore[call-arg]
    except Exception:  # noqa: BLE001 — any settings failure means "not configured"
        return None
    return url


_TEST_DATABASE_URL = _test_database_url()

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="DATABASE_URL_TEST is not configured",
)


@pytest.fixture(scope="module")
def migrated_engine() -> Iterator[Engine]:
    """A scratch database with the real migrations applied, dropped afterwards.

    Separate from the session-wide `_db_engine` on purpose — see this
    module's docstring.

    Yields
    ------
    Engine
        Connected to the migrated scratch database.
    """
    assert _TEST_DATABASE_URL is not None
    base_url = make_url(_TEST_DATABASE_URL)
    scratch_name = f"rls_coverage_{uuid.uuid4().hex[:12]}"
    maintenance = create_one_shot_engine(base_url.set(database="postgres"), autocommit=True)
    try:
        with maintenance.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{scratch_name}"'))
    finally:
        maintenance.dispose()

    scratch_url = base_url.set(database=scratch_name)
    try:
        # Alembic reads DATABASE_URL through `db.settings`, so point it at the
        # scratch database for the duration of this upgrade.
        from alembic import command  # noqa: PLC0415 — only needed for this fixture
        from alembic.config import Config  # noqa: PLC0415

        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = scratch_url.render_as_string(hide_password=False)
        try:
            config = Config(str(_repo_root() / "alembic.ini"))
            command.upgrade(config, "head")
        finally:
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous

        engine = create_one_shot_engine(scratch_url)
        try:
            yield engine
        finally:
            engine.dispose()
    finally:
        maintenance = create_one_shot_engine(base_url.set(database="postgres"), autocommit=True)
        try:
            with maintenance.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch_name}" WITH (FORCE)'))
        finally:
            maintenance.dispose()


def _repo_root():
    """Locate the repo root, where `alembic.ini` lives."""
    from pathlib import Path  # noqa: PLC0415

    return Path(__file__).resolve().parents[2]


class Predicates(NamedTuple):
    """One policy's two expressions.

    Read and write are separate in Postgres: `USING` filters what a query can
    see, `WITH CHECK` constrains what it may write. Asserting only `USING`
    would pass a policy that reads correctly and lets a tenant insert or
    update a row owned by someone else, which is the same leak in the other
    direction.
    """

    using: str
    with_check: str


def _policies(engine: Engine) -> dict[tuple[str, str], str]:
    """Read every `user_isolation` policy from the live database.

    Parameters
    ----------
    engine
        Connected to the migrated database.

    Returns
    -------
    dict[tuple[str, str], Predicates]
        `(schema, table)` to the policy's read and write expressions.
    """
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT schemaname, tablename, qual, with_check FROM pg_policies WHERE policyname = :name"),
            {"name": POLICY_NAME},
        ).all()
    # `with_check` is NULL when the policy omits it, in which case Postgres
    # applies `USING` to writes as well — so falling back to `qual` reflects
    # what the engine actually enforces rather than inventing a hole.
    return {
        (row.schemaname, row.tablename): Predicates(
            using=row.qual or "", with_check=row.with_check if row.with_check is not None else (row.qual or "")
        )
        for row in rows
    }


def _forced_tables(engine: Engine) -> set[tuple[str, str]]:
    """Every table with both `ENABLE` and `FORCE` row-level security.

    Parameters
    ----------
    engine
        Connected to the migrated database.

    Returns
    -------
    set[tuple[str, str]]
    """
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT n.nspname AS schema_name, c.relname AS table_name "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relrowsecurity AND c.relforcerowsecurity"
            )
        ).all()
    return {(row.schema_name, row.table_name) for row in rows}


def test_every_tenant_table_has_a_forced_isolation_policy(migrated_engine: Engine) -> None:
    """The coverage guarantee itself: no tenant table may ship without a policy."""
    expected = {(t.schema, t.table) for t in tenant_tables(Base.metadata)}
    assert expected, "no tenant tables were derived — the metadata import is broken"

    missing_policy = expected - set(_policies(migrated_engine))
    assert not missing_policy, f"tenant tables with no {POLICY_NAME} policy: {sorted(missing_policy)}"

    missing_force = expected - _forced_tables(migrated_engine)
    assert not missing_force, f"tenant tables whose RLS is not FORCEd: {sorted(missing_force)}"


def test_no_table_in_the_database_carries_user_id_without_a_policy(migrated_engine: Engine) -> None:
    """Catch a table that exists in Postgres but never made it into the model metadata."""
    with migrated_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.table_schema, c.table_name FROM information_schema.columns c "
                "JOIN information_schema.tables t "
                "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
                "WHERE c.column_name = 'user_id' AND t.table_type = 'BASE TABLE' "
                "  AND c.table_schema IN ('public', 'accounting', 'trades')"
            )
        ).all()
    with_user_id = {(row.table_schema, row.table_name) for row in rows}
    covered = set(_policies(migrated_engine))
    uncovered = with_user_id - covered - set(RLS_EXEMPT)
    assert not uncovered, f"tables carry user_id but have no {POLICY_NAME} policy: {sorted(uncovered)}"


def test_every_policy_fails_closed_on_an_unset_current_user(migrated_engine: Engine) -> None:
    """An unset `app.current_user_id` must return zero rows, never raise and never return everything."""
    for (schema, table), predicates in _policies(migrated_engine).items():
        for direction, expression in (("USING", predicates.using), ("WITH CHECK", predicates.with_check)):
            assert "NULLIF" in expression, (
                f"{schema}.{table}'s {direction} expression does not guard against an empty setting: {expression}"
            )


def test_the_users_table_is_scoped_by_its_own_primary_key(migrated_engine: Engine) -> None:
    """`users` has no `user_id`; its policy must compare `id` instead."""
    predicates = _policies(migrated_engine).get(("public", "users"))
    assert predicates is not None, "public.users has no isolation policy"
    assert "id" in predicates.using
    assert "id" in predicates.with_check


def test_every_rls_exemption_records_a_reason() -> None:
    """An exemption is a documented hole in the guarantee, never a silent omission."""
    assert RLS_EXEMPT, "the exemption list should not be empty without also removing this test"
    for key, reason in RLS_EXEMPT.items():
        assert reason, f"{key} is exempt from RLS with no reason given"
        assert len(reason.strip()) > 40, f"{key}'s exemption reason is too terse to be a real justification"


def test_the_previously_uncovered_transfer_tables_are_covered_now(migrated_engine: Engine) -> None:
    """Regression guard for the exact three tables VISION-AUDIT T3 found unprotected.

    `transfer_rule_exclusions` is now `categorization_rule_exclusions` — the
    same table, renamed when `transfer_rules` and `category_patterns` merged
    into `categorization_rules`.
    """
    covered = set(_policies(migrated_engine))
    for table in ("transfer_links", "transfer_linked_transactions", "categorization_rule_exclusions"):
        assert ("accounting", table) in covered, f"accounting.{table} lost its policy again"


def test_the_dimension_tables_are_absent_from_the_policy_set_without_being_exempted(
    migrated_engine: Engine,
) -> None:
    """Move #3 added three reference tables and needed no new hole in the guarantee.

    `currencies`, `institutions` and `securities` carry no `user_id`, so
    `tenant_tables` never selects them and there is nothing for a policy to
    compare against — which is a different fact from an `RLS_EXEMPT` entry.
    An exemption names a table that *does* carry `user_id` and deliberately
    has none anyway (`external_identities`, and only that one). Asserting the
    distinction here is what stops a future reference table from being waved
    through with an exemption it does not need, and — more importantly — stops
    a genuinely tenant-scoped table from being mistaken for reference data.
    """
    dimensions = [("public", "currencies"), ("accounting", "institutions"), ("trades", "securities")]
    tenant = {(entry.schema, entry.table) for entry in tenant_tables(Base.metadata)}
    for key in dimensions:
        assert key not in tenant, f"{key} was derived as a tenant table"
        assert key not in RLS_EXEMPT, f"{key} was given an RLS exemption it does not need"
        assert key not in _policies(migrated_engine), f"{key} has an isolation policy on data belonging to nobody"
    assert set(RLS_EXEMPT) == {("public", "external_identities")}, (
        "the exemption list changed — every entry is a documented hole in tenant isolation"
    )


def test_no_table_without_a_policy_is_anything_other_than_reference_data(migrated_engine: Engine) -> None:
    """The converse coverage: every unprotected table in the live database is one this repo classifies as shared.

    `test_no_table_in_the_database_carries_user_id_without_a_policy` catches a
    tenant table that lost its policy. This catches the opposite mistake — a
    table that quietly has no `user_id` (and so no policy) when it should have
    had one, which the `user_id`-keyed test cannot see by construction.
    """
    with migrated_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_type = 'BASE TABLE' AND table_schema IN ('public', 'accounting', 'trades') "
                "  AND table_name <> 'alembic_version'"
            )
        ).all()
    live = {(row.table_schema, row.table_name) for row in rows}
    unprotected = live - set(_policies(migrated_engine)) - set(RLS_EXEMPT)
    modelled = {(table.schema or "public", table.name): table for table in Base.metadata.tables.values()}
    for key in sorted(unprotected):
        table = modelled.get(key)
        assert table is not None, f"{key} exists in Postgres but not in the models"
        assert is_reference_table(table), f"{key} has no isolation policy and is not reference data"
