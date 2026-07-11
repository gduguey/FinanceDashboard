"""Engine and session management: one engine per process, one session per request.

`get_engine` is cached so every caller in a running process shares the same
connection pool rather than each opening its own — the pool itself is what
makes concurrent requests safe against Postgres, not anything session-level.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from db.current_user import get_current_user_id
from db.settings import AppRuntimeDatabaseSettings

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterator


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Build (once) and return the process-wide SQLAlchemy engine.

    Returns
    -------
    Engine
        Bound to `AppRuntimeDatabaseSettings().database_url` — the
        `app_runtime` role Row-Level Security policies actually apply to,
        once it's configured (`DATABASE_URL_APP`); the superuser/owner
        role from `DATABASE_URL` otherwise. `pool_pre_ping` guards against
        Postgres having silently closed an idle connection (e.g. after a
        container restart) by testing it before reuse rather than failing
        the request that happens to draw it next.
    """
    # database_url has no default (see AppRuntimeDatabaseSettings) — pydantic-settings fills
    # it from DATABASE_URL_APP/DATABASE_URL at runtime, but mypy has no pydantic plugin
    # configured here to know that, so it sees a required constructor argument never passed.
    return create_engine(AppRuntimeDatabaseSettings().database_url, pool_pre_ping=True)  # type: ignore[call-arg]


def session_factory() -> sessionmaker[Session]:
    """Build a `sessionmaker` bound to the process-wide engine.

    Returns
    -------
    sessionmaker[Session]
        `expire_on_commit=False` so a row returned by a repository function
        can still be read after that function's own `session.commit()`,
        without an extra round trip to re-fetch it.
    """
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope(user_id: uuid.UUID) -> Iterator[Session]:
    """Open one `Session` scoped to `user_id` for Row-Level Security, outside a FastAPI request.

    Does exactly what `get_db` does for a request — build a session and
    set the `app.current_user_id` session variable every RLS policy checks
    — but takes the acting user explicitly rather than through
    `get_current_user_id()`, since a cron script or CLI entrypoint has no
    "current request" to read that from. Callers running outside a
    request (e.g. `python -m` scripts on a schedule) pass
    `db.current_user.DEFAULT_USER_ID` explicitly, the same convention
    `db.backup`/`trades.utils.statement_archive` already use.

    Parameters
    ----------
    user_id
        The user to scope this session's RLS policies to.

    Yields
    ------
    Session
    """
    with session_factory()() as session:
        session.execute(text("SELECT set_config('app.current_user_id', :user_id, true)"), {"user_id": str(user_id)})
        yield session


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding one `Session` per request.

    Sets the Postgres session variable every Row-Level Security policy
    checks (`current_setting('app.current_user_id', true)`) to the acting
    user, scoped to this session's own transaction via `set_config`'s
    `is_local=true` (Postgres has no bind-parameter form of `SET LOCAL`
    itself, only of the equivalent `set_config` function) — so it's reset
    the moment this request's transaction ends, never leaking into a
    pooled connection's next, unrelated request.

    Callers are responsible for calling `session.commit()` themselves after
    a successful write — this dependency only guarantees the session is
    closed afterward, and rolled back automatically if the request raised.

    Yields
    ------
    Session
    """
    with session_scope(get_current_user_id()) as session:
        yield session
