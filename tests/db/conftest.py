"""A scratch database with the real migrations applied, shared by the RLS modules.

Both `test_rls_coverage.py` (does every tenant table have a policy?) and
`test_rls_isolation.py` (do those policies actually keep two tenants apart?)
need a database built by Alembic rather than by
`Base.metadata.create_all` — see `test_rls_coverage.py`'s docstring for why
the session-wide `_db_engine` cannot serve either of them.

Migrating is the slow part, so the fixture is session-scoped and both modules
share one scratch database.

The database's lifecycle is `tests.support.scratch_db`'s, which every suite
in this repo that needs a real database now goes through — this one first,
and `tests/conftest.py` since.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from db.session import create_one_shot_engine
from db.settings import AppRuntimeDatabaseSettings, TestDatabaseSettings
from tests.support.scratch_db import apply_migrations, scratch_database

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

    with scratch_database(configured, prefix="rls") as scratch_url:
        apply_migrations(scratch_url)
        yield scratch_url


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
