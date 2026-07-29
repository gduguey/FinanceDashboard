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
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

import accounting.db  # noqa: F401 — registers the accounting tables on Base.metadata
import trades.db  # noqa: F401 — registers the trades tables on Base.metadata
from db.base import Base
from db.session import create_one_shot_engine
from db.settings import TestDatabaseSettings
from db.tenant import POLICY_NAME, RLS_EXEMPT, tenant_tables

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


def _policies(engine: Engine) -> dict[tuple[str, str], str]:
    """Read every `user_isolation` policy from the live database.

    Parameters
    ----------
    engine
        Connected to the migrated database.

    Returns
    -------
    dict[tuple[str, str], str]
        `(schema, table)` to the policy's `USING` expression.
    """
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT schemaname, tablename, qual FROM pg_policies WHERE policyname = :name"),
            {"name": POLICY_NAME},
        ).all()
    return {(row.schemaname, row.tablename): row.qual for row in rows}


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
    for (schema, table), qual in _policies(migrated_engine).items():
        assert "NULLIF" in (qual or ""), f"{schema}.{table}'s policy does not guard against an empty setting: {qual}"


def test_the_users_table_is_scoped_by_its_own_primary_key(migrated_engine: Engine) -> None:
    """`users` has no `user_id`; its policy must compare `id` instead."""
    qual = _policies(migrated_engine).get(("public", "users"))
    assert qual is not None, "public.users has no isolation policy"
    assert "id" in qual


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
