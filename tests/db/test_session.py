"""Tests for `db.session.get_db`/`session_scope`: the Postgres session-variable wiring every RLS policy checks."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine, text

import db.session as session_module
from tests.conftest import DEFAULT_USER_ID


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
