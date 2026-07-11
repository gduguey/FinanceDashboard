"""Tests for `db.session.get_db`: the Postgres session-variable wiring every RLS policy checks."""

from __future__ import annotations

from sqlalchemy import Engine, text

import db.session as session_module
from db.current_user import DEFAULT_USER_ID


def test_get_db_sets_the_current_user_id_session_variable(monkeypatch, _db_engine: Engine) -> None:  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    monkeypatch.setattr(session_module, "get_engine", lambda: _db_engine)

    # `dependency` must stay a live reference for the generator's suspended `yield` to
    # survive — an inline `next(session_module.get_db())` drops the only reference to the
    # generator immediately, so CPython GCs it on the spot, throwing `GeneratorExit` at
    # `get_db`'s `yield` and closing/rolling back the session before this test ever runs a
    # query. FastAPI's real dependency injection holds a reference for the whole request,
    # so production `get_db` callers never hit this — only an inline `next(...)` here would.
    dependency = session_module.get_db()
    session = next(dependency)
    try:
        current = session.execute(text("SELECT current_setting('app.current_user_id', true)")).scalar()
        assert current == str(DEFAULT_USER_ID)
    finally:
        session.rollback()
        session.close()
