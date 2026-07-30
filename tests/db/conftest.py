"""A scratch database with the real migrations applied, shared by the RLS modules.

Both `test_rls_coverage.py` (does every tenant table have a policy?) and
`test_rls_isolation.py` (do those policies actually keep two tenants apart?)
need a database built by Alembic rather than by
`Base.metadata.create_all` — see `test_rls_coverage.py`'s docstring for why
the session-wide `_db_engine` cannot serve either of them.

Migrating is the slow part, so the fixture is session-scoped and both modules
share one scratch database.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from db.session import create_one_shot_engine
from db.settings import AppRuntimeDatabaseSettings, TestDatabaseSettings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine
    from sqlalchemy.engine import URL


def test_database_url() -> str | None:
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


def app_runtime_database_url() -> str | None:
    """The configured `app_runtime` URL, or `None` if it is unset.

    Only the username and password are used; the host and database name come
    from the scratch database the migrations just built.

    Returns
    -------
    str or None
    """
    try:
        url = AppRuntimeDatabaseSettings().database_url  # type: ignore[call-arg]
    except Exception:  # noqa: BLE001 — any settings failure means "not configured"
        return None
    return url


@pytest.fixture(scope="session")
def migrated_url() -> Iterator[URL]:
    """Create a scratch database, run the real migrations into it, drop it afterwards.

    Yields
    ------
    URL
        The scratch database's URL, as the owning role.
    """
    configured = test_database_url()
    if configured is None:
        pytest.skip("DATABASE_URL_TEST is not configured")

    base_url = make_url(configured)
    scratch_name = f"rls_{uuid.uuid4().hex[:12]}"
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
            config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            command.upgrade(config, "head")
        finally:
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous

        yield scratch_url
    finally:
        maintenance = create_one_shot_engine(base_url.set(database="postgres"), autocommit=True)
        try:
            with maintenance.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch_name}" WITH (FORCE)'))
        finally:
            maintenance.dispose()


@pytest.fixture(scope="session")
def migrated_engine(migrated_url: URL) -> Iterator[Engine]:
    """The migrated scratch database, as the owning role.

    Yields
    ------
    Engine
    """
    engine = create_one_shot_engine(migrated_url)
    try:
        yield engine
    finally:
        engine.dispose()
