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
from cryptography.fernet import Fernet
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

import db.models
from db.base import Base
from db.settings import TestDatabaseSettings

if TYPE_CHECKING:
    from collections.abc import Iterator

# `accounting` and `trades` are independent packages (see docs/architecture.md:
# deleting either one should never break the other's tests) — guarded rather than
# imported unconditionally, so a tree with only one of them still collects fine.
try:
    import accounting.db  # noqa: F401  (registers accounting.* tables on Base.metadata)
    import accounting.utils.statement_archive as accounting_storage
except ModuleNotFoundError:
    accounting_storage = None

try:
    import trades.db  # noqa: F401  (registers trades.* tables on Base.metadata)
    import trades.utils.statement_archive as trades_storage
except ModuleNotFoundError:
    trades_storage = None


_R2_ENV_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_ENDPOINT_URL")


@pytest.fixture(autouse=True)
def _no_r2_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _R2_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    if trades_storage is not None:
        monkeypatch.setattr(trades_storage, "get_r2_credentials", lambda: trades_storage.R2Credentials(_env_file=None))
    if accounting_storage is not None:
        monkeypatch.setattr(
            accounting_storage, "get_r2_credentials", lambda: accounting_storage.R2Credentials(_env_file=None)
        )


_TEST_SECRETS_ENCRYPTION_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _fixed_secrets_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a fixed, in-memory `db.encryption` key instead of whatever `.env` has (or lacks).

    Same reasoning as `_no_r2_by_default`: a real key in the developer's own
    `.env` must never leak into a test run (a test asserting on ciphertext
    would otherwise depend on production key material), and a run with no
    key configured at all must not fail every secrets-related test with a
    missing-env-var error unrelated to what's actually being tested.
    """
    monkeypatch.setenv("APP_SECRETS_ENCRYPTION_KEY", _TEST_SECRETS_ENCRYPTION_KEY)
    monkeypatch.setenv("APP_SECRETS_ENCRYPTION_KEY_VERSION", "1")
    monkeypatch.delenv("APP_SECRETS_ENCRYPTION_KEYS_PREVIOUS", raising=False)


@pytest.fixture(autouse=True)
def _no_real_database_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip `DATABASE_URL`/`DATABASE_URL_APP` (the real dev database) from every test's environment.

    `TestDatabaseSettings` reading a separate `DATABASE_URL_TEST` only
    protects tests that go through it — `db.session.get_engine` reads
    `AppRuntimeDatabaseSettings().database_url` (`DATABASE_URL_APP`, falling
    back to `DATABASE_URL`) directly, and an API test that forgets to
    override the `get_db` FastAPI dependency (see
    `test_api.py`/`test_accounting_api.py`/`test_accounting_goals_api.py`)
    would otherwise silently connect to and mutate the real dev database
    instead of failing. With both gone, that failure mode becomes a loud
    `ValidationError` (missing required field) instead.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_APP", raising=False)


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
