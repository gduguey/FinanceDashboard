"""A throwaway Postgres database, created for one pytest run and dropped after it.

Every suite in this repo that needs a real database gets its own, created
here and named uniquely per run. That is not tidiness — it is the only thing
that makes two concurrent `pytest` invocations safe. The session fixture used
to open `DATABASE_URL_TEST` verbatim and `DROP SCHEMA ... CASCADE` before
yielding, which is correct for one run and destructive for two: a second
session starting mid-suite pulled the tables out from under the first, and the
first then failed in whichever tests happened to be executing. The failures
looked like a real regression rather than a fixture problem — a different
scattered set each time — and cost real debugging time during PR 5.

`DATABASE_URL_TEST`'s own database is therefore never written to. It is read
for its host, credentials and port, and the database name in it is replaced.
It still has to exist and be connectable, because that is where the
credentials come from.

Two suites need different things built into their scratch database, and both
come through here:

- the main suite builds its schema with `Base.metadata.create_all`, which is
  fast and creates no policies;
- the RLS suites and the latency gate run `apply_migrations`, because what
  they assert about — policies, grants, the restricted `app_runtime` role —
  exists only in the migrations.

The lifecycle is identical either way, so it is written once.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.engine import make_url

from db.session import create_one_shot_engine

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import URL

_MAINTENANCE_DATABASE = "postgres"
"""Where `CREATE DATABASE`/`DROP DATABASE` are issued from — never the database being created or dropped."""


def _scratch_name(prefix: str) -> str:
    """Build a database name no other run can collide with.

    The uuid alone guarantees uniqueness. The `pytest-xdist` worker id is
    folded in as well so that a database orphaned by a crashed run can be
    attributed to the worker that made it, rather than being one of several
    identical-looking leftovers.

    Parameters
    ----------
    prefix
        Which suite is asking, so an orphan names its own origin.

    Returns
    -------
    str
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    parts = [part for part in (prefix, worker, uuid.uuid4().hex[:12]) if part]
    return "_".join(parts)


@contextmanager
def scratch_database(configured_url: str | URL, *, prefix: str) -> Iterator[URL]:
    """Create an empty database for the caller, and drop it when the caller is done.

    Parameters
    ----------
    configured_url
        Any URL on the target server — only its host, port and credentials
        are used. Its database name is replaced, so passing
        `DATABASE_URL_TEST` here does not put the caller anywhere near
        `finance_test` itself.
    prefix
        Leading component of the generated database name.

    Yields
    ------
    URL
        The scratch database's URL, as the role `configured_url` names.
    """
    base_url = make_url(configured_url) if isinstance(configured_url, str) else configured_url
    name = _scratch_name(prefix)

    maintenance = create_one_shot_engine(base_url.set(database=_MAINTENANCE_DATABASE), autocommit=True)
    try:
        with maintenance.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        maintenance.dispose()

    try:
        yield base_url.set(database=name)
    finally:
        # `WITH (FORCE)` rather than trusting every caller to have disposed of
        # its engines: a drop that fails leaves an orphan database behind on
        # the developer's machine, silently, run after run.
        maintenance = create_one_shot_engine(base_url.set(database=_MAINTENANCE_DATABASE), autocommit=True)
        try:
            with maintenance.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        finally:
            maintenance.dispose()


def apply_migrations(url: URL) -> None:
    """Run `alembic upgrade head` into `url`.

    Alembic reads `DATABASE_URL` through `db.settings` rather than taking a
    URL argument, so that variable is swapped for the duration of the upgrade
    and restored afterwards — including when it was unset to begin with,
    which is the normal case under `tests/conftest.py`'s
    `_no_real_database_by_default`.

    Parameters
    ----------
    url
        The database to migrate, as a role that owns it.
    """
    from alembic import command  # noqa: PLC0415 — importing alembic is only worth it for the suites that migrate
    from alembic.config import Config  # noqa: PLC0415

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    try:
        command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
