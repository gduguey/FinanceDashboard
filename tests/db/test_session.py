"""Tests for `db.session.get_db`/`session_scope`: the Postgres session-variable wiring every RLS policy checks."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

import db.session as session_module
from db.session import set_rls_user
from db.settings import StatementTimeoutSettings
from tests.conftest import DEFAULT_USER_ID

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_get_db_sets_the_current_user_id_session_variable(monkeypatch, _db_engine: Engine) -> None:  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    monkeypatch.setattr(session_module, "get_engine", lambda: _db_engine)

    # `dependency` must stay a live reference for the generator's suspended `yield` to
    # survive — an inline `next(session_module.get_db(...))` drops the only reference to
    # the generator immediately, so CPython GCs it on the spot, throwing `GeneratorExit`
    # at `get_db`'s `yield` and closing/rolling back the session before this test ever
    # runs a query. FastAPI's real dependency injection holds a reference for the whole
    # request, so production `get_db` callers never hit this — only an inline `next(...)`
    # here would. `user_id` is passed explicitly here — outside a real request, nothing
    # resolves `get_db`'s own `Depends(get_current_user_id)` default for us.
    dependency = session_module.get_db(user_id=DEFAULT_USER_ID)
    session = next(dependency)
    try:
        current = session.execute(text("SELECT current_setting('app.current_user_id', true)")).scalar()
        assert current == str(DEFAULT_USER_ID)
    finally:
        session.rollback()
        session.close()


def test_session_scope_sets_the_current_user_id_session_variable(monkeypatch, _db_engine: Engine) -> None:  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    monkeypatch.setattr(session_module, "get_engine", lambda: _db_engine)
    user_id = uuid.uuid4()

    with session_module.session_scope(user_id) as session:
        current = session.execute(text("SELECT current_setting('app.current_user_id', true)")).scalar()
        assert current == str(user_id)
        session.rollback()


def test_current_user_id_reverts_to_empty_string_not_null_after_a_mid_request_commit(
    monkeypatch,
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
) -> None:
    """The exact gotcha `set_rls_user` exists to guard against.

    `app.current_user_id` is a custom, never-declared GUC — its reset
    value (what a transaction-scoped `set_config(..., true)` reverts to
    once that transaction commits) is an empty string, not `NULL`. A
    caller that assumes a plain `current_setting(..., true) IS NULL`
    fallback after a mid-request commit is wrong; every RLS policy's
    `(current_setting(...))::uuid` cast fails outright on `''` instead.
    """
    monkeypatch.setattr(session_module, "get_engine", lambda: _db_engine)
    user_id = uuid.uuid4()

    with session_module.session_scope(user_id) as session:
        session.commit()
        current = session.execute(text("SELECT current_setting('app.current_user_id', true)")).scalar()
        assert not current
        session.rollback()


def test_set_rls_user_re_establishes_it_after_a_mid_request_commit(monkeypatch, _db_engine: Engine) -> None:  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    monkeypatch.setattr(session_module, "get_engine", lambda: _db_engine)
    user_id = uuid.uuid4()

    with session_module.session_scope(user_id) as session:
        session.commit()
        session_module.set_rls_user(session, user_id)
        current = session.execute(text("SELECT current_setting('app.current_user_id', true)")).scalar()
        assert current == str(user_id)
        session.rollback()


def _statement_timeout_ms(session: Session) -> int:
    """Read `statement_timeout` in milliseconds.

    From `pg_settings` rather than `current_setting`, which returns the value
    Postgres has normalized to whatever unit it liked — `600s` reads back as
    `10min`. `pg_settings.setting` is always the plain millisecond count.
    """
    setting = session.execute(text("SELECT setting FROM pg_settings WHERE name = 'statement_timeout'")).scalar_one()
    return int(setting)


def test_set_rls_user_bounds_the_transaction_with_a_statement_timeout(db_session: Session) -> None:
    """Acquiring a scoped session acquires a time bound — the two are one call, so no path can skip it."""
    set_rls_user(db_session, DEFAULT_USER_ID)

    assert _statement_timeout_ms(db_session) == StatementTimeoutSettings().statement_timeout_seconds * 1000


def test_the_background_bound_is_larger_than_the_request_bound(db_session: Session) -> None:
    set_rls_user(db_session, DEFAULT_USER_ID, background=True)

    settings = StatementTimeoutSettings()
    assert _statement_timeout_ms(db_session) == settings.background_statement_timeout_seconds * 1000
    assert settings.background_statement_timeout_seconds > settings.statement_timeout_seconds


def test_a_statement_past_the_bound_is_cancelled_rather_than_left_running(db_session: Session) -> None:
    """The bound is enforced by Postgres, not merely recorded — a slow statement fails its own request."""
    set_rls_user(db_session, DEFAULT_USER_ID)
    db_session.execute(text("SET LOCAL statement_timeout = '50ms'"))

    with pytest.raises(OperationalError):
        db_session.execute(text("SELECT pg_sleep(1)"))
