"""Engine and session management: one engine per process, one session per request.

`get_engine` is cached so every caller in a running process shares the same
connection pool rather than each opening its own — the pool itself is what
makes concurrent requests safe against Postgres, not anything session-level.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.settings import DatabaseSettings

if TYPE_CHECKING:
    from collections.abc import Iterator


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Build (once) and return the process-wide SQLAlchemy engine.

    Returns
    -------
    Engine
        Bound to `DatabaseSettings().database_url`. `pool_pre_ping` guards
        against Postgres having silently closed an idle connection (e.g.
        after a container restart) by testing it before reuse rather than
        failing the request that happens to draw it next.
    """
    # database_url has no default (see DatabaseSettings) — pydantic-settings fills it from
    # the DATABASE_URL env var at runtime, but mypy has no pydantic plugin configured here
    # to know that, so it sees a required constructor argument that was never passed.
    return create_engine(DatabaseSettings().database_url, pool_pre_ping=True)  # type: ignore[call-arg]


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


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding one `Session` per request.

    Callers are responsible for calling `session.commit()` themselves after
    a successful write — this dependency only guarantees the session is
    closed afterward, and rolled back automatically if the request raised.

    Yields
    ------
    Session
    """
    with session_factory()() as session:
        yield session
