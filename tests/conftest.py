"""Shared test fixtures.

Disables R2 across the whole suite by default so every existing test keeps
exercising the local-disk fallback path deterministically, regardless of
what's actually configured in the developer's own `.env` — mirrors how
`IbkrFlexCredentials` tests pass `_env_file=None` rather than relying on
whatever `.env` happens to contain. Tests that specifically want to
exercise the R2 branch construct their own configured `R2Credentials`
instance and pass it to `StatementArchive` directly instead of relying on
this default.

`_env_file=None` alone only disables reading `.env` as a file — it does
NOT stop pydantic-settings from reading real `R2_*` values straight out of
the process environment, which is exactly what happens under the VS Code
Python extension: it auto-loads `${workspaceFolder}/.env` (its default
`python.envFile`) into every test run's environment. Left unguarded, that
makes the whole suite silently read from and write to the real production
R2 bucket — including tests that construct `R2Credentials` directly
rather than through `get_r2_credentials()`. So this fixture also strips
the real `R2_*` variables from `os.environ` itself, which covers every
construction site, not just the ones monkeypatched below.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

import accounting.db  # noqa: F401  (registers accounting.* tables on Base.metadata)
import accounting.utils.statement_archive as accounting_storage
import db.models
import trades.db  # noqa: F401  (registers trades.* tables on Base.metadata)
import trades.utils.statement_archive as trades_storage
from db.base import Base
from db.settings import TestDatabaseSettings

if TYPE_CHECKING:
    from collections.abc import Iterator

_R2_ENV_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_ENDPOINT_URL")


@pytest.fixture(autouse=True)
def _no_r2_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _R2_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(trades_storage, "get_r2_credentials", lambda: trades_storage.R2Credentials(_env_file=None))
    monkeypatch.setattr(
        accounting_storage, "get_r2_credentials", lambda: accounting_storage.R2Credentials(_env_file=None)
    )


@pytest.fixture(scope="session")
def _db_engine() -> Iterator[Engine]:
    """One Postgres engine for the whole test session, with a freshly (re)built schema.

    Deliberately never points at `DatabaseSettings().database_url` (the dev
    database) — `TestDatabaseSettings` reads a separate `DATABASE_URL_TEST`,
    so a misconfigured `.env` fails loudly instead of a test run silently
    wiping dev data.
    """
    engine = create_engine(TestDatabaseSettings().database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS accounting CASCADE"))
        connection.execute(text("DROP SCHEMA IF EXISTS trades CASCADE"))
        connection.execute(text("CREATE SCHEMA accounting"))
        connection.execute(text("CREATE SCHEMA trades"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(_db_engine: Engine) -> Iterator[Session]:
    """One Postgres session per test, wrapped in a transaction that's always rolled back.

    Uses `join_transaction_mode="create_savepoint"` so that repository code
    calling `session.commit()` (the normal, expected thing for it to do)
    only ends a SAVEPOINT rather than the outer transaction — the test's
    writes are still fully undone once this fixture calls `rollback()`
    itself below. Deliberately does NOT use `connection.begin()` as a
    context manager for that outer transaction — its context-manager form
    commits on a clean exit and only rolls back on an exception, which
    would silently persist every passing test's writes into the next test.
    """
    connection = _db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def test_user_id(db_session: Session) -> uuid.UUID:
    """A `User` row that exists for the duration of one test, for other rows to foreign-key against."""
    user_id = uuid.uuid4()
    db_session.add(db.models.User(id=user_id, email=f"{user_id}@example.com", hashed_password="unset"))  # noqa: S106
    db_session.commit()
    return user_id
