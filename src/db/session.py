"""Engine and session management: one engine per process, one session per request.

`get_engine` is cached so every caller in a running process shares the same
connection pool rather than each opening its own — the pool itself is what
makes concurrent requests safe against Postgres, not anything session-level.
Its pool is sized here rather than left on SQLAlchemy's defaults, because
the deployment container is capped at 1 CPU and Postgres' `max_connections`
is a shared budget.

Every engine this process builds comes from one of exactly two functions in
this module: `get_engine` for request traffic (the `app_runtime` role, RLS
applies, pooled) and `create_one_shot_engine` for the two administrative
tasks that deliberately need the superuser role (unpooled, disposed after
use). Nothing else calls `create_engine` — connection policy has one home.
"""

from __future__ import annotations

import uuid  # noqa: TC003 — get_db's own Depends(get_current_user_id) needs uuid.UUID resolvable at runtime
from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from db.current_user import get_current_user_id
from db.settings import AppRuntimeDatabaseSettings, StatementTimeoutSettings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import URL


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Build (once) and return the process-wide SQLAlchemy engine that serves requests.

    Returns
    -------
    Engine
        Bound to `AppRuntimeDatabaseSettings().database_url` — the
        `app_runtime` role Row-Level Security policies actually apply to
        (`DATABASE_URL_APP`, required, no superuser fallback). The pool is
        explicitly sized rather than left on SQLAlchemy's defaults; see
        `AppRuntimeDatabaseSettings`' own field descriptions for why each
        number is what it is. `pool_pre_ping` guards against Postgres
        having silently closed an idle connection (e.g. after a container
        restart) by testing it before reuse rather than failing the
        request that happens to draw it next.
    """
    # database_url has no default (see AppRuntimeDatabaseSettings) — pydantic-settings fills
    # it from DATABASE_URL_APP at runtime, but mypy has no pydantic plugin configured here
    # to know that, so it sees a required constructor argument never passed.
    settings = AppRuntimeDatabaseSettings()  # type: ignore[call-arg]
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_recycle=settings.pool_recycle_seconds,
        pool_timeout=settings.pool_timeout_seconds,
    )


def create_one_shot_engine(url: str | URL, *, autocommit: bool = False) -> Engine:
    """Build an unpooled engine for a single administrative task, then throw it away.

    Every engine in this process is built either here or in `get_engine`,
    so connection policy lives in exactly one module. The two callers —
    the price-sync user enumeration and backup verification — connect as
    the migration-owning superuser to do something Row-Level Security is
    deliberately meant to prevent an ordinary request from doing, run
    once, and exit. Pooling them would hold superuser connections open for
    the life of the process against Postgres' `max_connections` budget for
    no benefit, so they get `NullPool`: connect, work, disconnect.

    Parameters
    ----------
    url
        The connection string to bind to.
    autocommit
        Run every statement outside a transaction block. Required for
        `CREATE DATABASE`/`DROP DATABASE`, which Postgres refuses to run
        inside one — that is the whole reason backup verification needs it.

    Returns
    -------
    Engine
        Unpooled. The caller owns disposing of it.
    """
    if autocommit:
        return create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    return create_engine(url, poolclass=NullPool)


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


@lru_cache(maxsize=1)
def _statement_timeouts() -> StatementTimeoutSettings:
    """Read the statement-timeout settings once per process — `set_rls_user` runs on every request.

    Returns
    -------
    StatementTimeoutSettings
    """
    return StatementTimeoutSettings()


def set_rls_user(session: Session, user_id: uuid.UUID, *, background: bool = False) -> None:
    """(Re-)establish the `app.current_user_id` session variable every RLS policy checks, and bound the transaction.

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
    called after a repository write's own commit) must call this again
    first, or every RLS policy's own `(current_setting(...))::uuid` cast
    fails outright on the empty string — confirmed empirically, not
    theoretical (see `tests.db.test_session`).

    `statement_timeout` is set here, in the same call and with the same
    transaction-local scope, rather than anywhere else. That is deliberate
    and structural: acquiring a scoped session and acquiring a time bound are
    now one action, so no new code path can obtain one without the other. It
    is the same reasoning that makes tenant isolation a policy rather than a
    convention — the audit's finding was that store, postings, goals and LLM
    paths passed no time bound at all (VISION-AUDIT T5), and a bound that
    each call site has to remember is a bound that some call site will
    forget.

    Both are `is_local=true`, so a pooled connection's next request starts
    from the server default again rather than inheriting either.

    Parameters
    ----------
    session
        An open database session, already inside (or about to start) a transaction.
    user_id
        The user to scope this transaction's RLS policies to.
    background
        Use the much larger `background_statement_timeout_seconds` instead of
        the per-request bound. For work that is legitimately slow — a broker
        sync, a statement import, a ledger rebuild, an LLM categorization
        pass. Still bounded; nothing runs unbounded.
    """
    timeouts = _statement_timeouts()
    seconds = timeouts.background_statement_timeout_seconds if background else timeouts.statement_timeout_seconds
    session.execute(text("SELECT set_config('app.current_user_id', :user_id, true)"), {"user_id": str(user_id)})
    # A unit-bearing string rather than a bare number, so the value cannot be
    # misread as milliseconds; `set_config`'s third argument is the local flag.
    session.execute(text("SELECT set_config('statement_timeout', :timeout, true)"), {"timeout": f"{seconds}s"})


@contextmanager
def session_scope(user_id: uuid.UUID, *, background: bool = True) -> Iterator[Session]:
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
    background
        Defaults to `True`, unlike `get_db`'s per-request bound: every caller
        of this function is a cron job, CLI entrypoint or webhook doing work
        that is expected to take a while. Pass `False` for a short-lived
        script that should be held to the request bound.

    Yields
    ------
    Session
    """
    with session_factory()() as session:
        set_rls_user(session, user_id, background=background)
        yield session


def allow_background_runtime(session: Session, user_id: uuid.UUID) -> None:
    """Raise this request's statement timeout to the background bound.

    For the handful of endpoints that are legitimately slow — importing a
    statement, rebuilding the ledger from every archive, a bulk LLM
    categorization pass. They are still bounded (see
    `db.settings.StatementTimeoutSettings`), just far more generously than a
    read is; the point of the per-request bound is that an ordinary read
    cannot run for minutes, not that nothing may.

    A call rather than a parameter on `get_db`, because the bound belongs to
    the *handler* that knows it is slow, not to the dependency that hands out
    sessions — and because it re-establishes the RLS variable at the same
    time, which is what a mid-request `commit()` requires anyway (see
    `set_rls_user`).

    Parameters
    ----------
    session
        The request's session.
    user_id
        The acting user, re-established along with the new bound.
    """
    set_rls_user(session, user_id, background=True)


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

    **`background=False` is not the default and has to be passed.**
    `session_scope` defaults it to `True` because every *other* caller of it
    is a cron job, CLI entrypoint or webhook. Omitting it here silently gave
    every HTTP request the 600-second background bound instead of the
    15-second request bound, which is precisely the thing the per-request
    bound exists to prevent (VISION-AUDIT T5: "a pathological query fails
    its own request instead of holding a connection and a worker
    indefinitely"), and it also made `allow_background_runtime` a no-op,
    since the bound it raises was already raised. `session_scope`'s own
    docstring said "unlike `get_db`'s per-request bound" throughout, so the
    documentation was right and the code was not.

    Yields
    ------
    Session
    """
    with session_scope(user_id, background=False) as session:
        yield session
