"""Engine and session management: one engine per process, one session per request.

`get_engine` is cached so every caller in a running process shares the same
connection pool rather than each opening its own — the pool itself is what
makes concurrent requests safe against Postgres, not anything session-level.
"""

from __future__ import annotations

import uuid  # noqa: TC003 — get_db's own Depends(get_current_user_id) needs uuid.UUID resolvable at runtime
from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from db.current_user import get_current_user_id
from db.settings import AppRuntimeDatabaseSettings

if TYPE_CHECKING:
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


def set_rls_user(session: Session, user_id: uuid.UUID) -> None:
    """(Re-)establish the `app.current_user_id` session variable every Row-Level Security policy checks.

    Scoped to the session's *current* transaction only (`set_config`'s
    `is_local=true` — Postgres has no bind-parameter form of `SET LOCAL`
    itself, only of the equivalent `set_config` function), so it's reset
    the moment that transaction ends — never leaking into a pooled
    connection's next, unrelated request.

    That reset is the gotcha this function exists to let a caller undo:
    `app.current_user_id` is a custom (placeholder) GUC, never declared by
    any loaded extension, and Postgres's reset value for an *undeclared*
    GUC is an empty string, not `NULL` — so `current_setting(..., true)`
    returns `''`, not `None`/unset, the moment a `session.commit()` ends
    the transaction this was set in. Any code that calls `session.commit()`
    mid-request and then runs a *further* RLS-protected query on that same
    session (e.g. `ledger.transfers.reconcile_and_persist_rule_links`,
    called after `store.save_store`'s own commit) must call this again
    first, or every RLS policy's own `(current_setting(...))::uuid` cast
    fails outright on the empty string — confirmed empirically, not
    theoretical (see `tests.db.test_session`).

    Parameters
    ----------
    session
        An open database session, already inside (or about to start) a transaction.
    user_id
        The user to scope this transaction's RLS policies to.
    """
    session.execute(text("SELECT set_config('app.current_user_id', :user_id, true)"), {"user_id": str(user_id)})


@contextmanager
def session_scope(user_id: uuid.UUID) -> Iterator[Session]:
    """Open one `Session` scoped to `user_id` for Row-Level Security, outside a FastAPI request.

    Does exactly what `get_db` does for a request — build a session and
    set the `app.current_user_id` session variable every RLS policy checks
    (see `set_rls_user`) — but takes the acting user explicitly rather than
    through `get_current_user_id()`, since a cron script or CLI entrypoint
    has no "current request" to read that from. Every caller outside a
    request passes a real, specific user id it already has in hand — e.g.
    `trades.market_data.price_sync` loops over every real user id (read via
    the superuser role, the same way `db.backup` bypasses RLS) and opens
    one of these per user; `trades.api.webhooks` passes the fresh id it
    just generated for a newly provisioned user. `tests.conftest`'s
    `DEFAULT_USER_ID` — a stable, arbitrary test-fixture id, never a real
    user — is a test-only concept and never appears here.

    Parameters
    ----------
    user_id
        The user to scope this session's RLS policies to.

    Yields
    ------
    Session
    """
    with session_factory()() as session:
        set_rls_user(session, user_id)
        yield session


def get_db(user_id: Annotated[uuid.UUID, Depends(get_current_user_id)]) -> Iterator[Session]:
    """FastAPI dependency yielding one `Session` per request.

    Sets the Postgres session variable every Row-Level Security policy
    checks — see `set_rls_user` — to the acting user, scoped to this
    request's own transaction. Callers that call `session.commit()`
    mid-request and then run a further query on this same session must
    call `set_rls_user(session, user_id)` again first, or that later query
    silently loses RLS scoping (see `set_rls_user`'s own docstring for why).

    `user_id` is resolved through `Depends(get_current_user_id)` rather
    than called as a plain function specifically so it's a real parent
    dependency of this one in FastAPI's own dependency graph — a *sibling*
    dependency (e.g. one merely listed via a router's own `dependencies=`)
    has no guaranteed order relative to this one, empirically confirmed
    unreliable; a parent dependency's own sub-dependencies are always
    resolved first. `trades.api.api` overrides `get_current_user_id`
    itself (its own body always raises — see that function's docstring)
    with the real Clerk-session resolver, so this never touches Clerk
    directly and stays usable by any test that overrides `get_db` itself,
    same as before.

    Callers are responsible for calling `session.commit()` themselves after
    a successful write — this dependency only guarantees the session is
    closed afterward, and rolled back automatically if the request raised.

    Yields
    ------
    Session
    """
    with session_scope(user_id) as session:
        yield session
