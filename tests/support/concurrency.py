"""Synchronising a two-connection race on what Postgres is actually doing, rather than on a sleep.

Shared by every suite that forces a real interleaving between two sessions:
`tests/db/test_base_concurrency.py` (a lost `merge_by_natural_key` insert)
and `tests/db/test_provisioning.py` (a lost identity link). Both have the
same shape — a winner holds an uncommitted row, a loser blocks on it inside
Postgres, and the test has to know the loser has got there before committing
the winner — so the waiting belongs here rather than copied into each.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy import Engine

LOCK_WAIT_TIMEOUT_SECONDS = 15.0
"""How long to wait for the losing backend to actually block on the winner's uncommitted row."""

THREAD_TIMEOUT_SECONDS = 30.0
"""How long to wait for the losing thread to finish once the winner has committed."""


def wait_until_a_backend_blocks_on_a_lock(engine: Engine, *, count: int = 1) -> None:
    """Block until `count` backends in this database are waiting on a lock, or fail the test.

    The synchronisation point of a forced race. The losing session runs in
    its own thread and, on hitting the winner's uncommitted row, stops
    *inside Postgres* — no Python-visible event fires — so the only honest
    way to know it has got there is to ask Postgres. `pg_stat_activity`
    reports live backend state rather than an MVCC snapshot, so a third
    connection polling it sees the wait as soon as it starts.

    Deliberately not a `sleep`: a fixed sleep either flakes on a slow machine
    or makes every run pay for the slowest one, and worse, a sleep that is
    too short would let the winner commit *before* the loser's statement is
    even sent, which is a different (and much easier) interleaving than the
    one these tests exist to cover.

    Parameters
    ----------
    engine
        An engine on the database the race is running in. A further
        connection is opened on it to watch, so it must not be one of the
        racing sessions'.
    count
        How many backends must be blocked at once. More than one when
        several contenders have to be queued behind the same row before the
        holder releases it, so that they genuinely contend rather than
        running one after another.
    """
    deadline = time.monotonic() + LOCK_WAIT_TIMEOUT_SECONDS
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as watcher:
        while time.monotonic() < deadline:
            waiting = watcher.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            if waiting >= count:
                return
            time.sleep(0.02)
    pytest.fail(f"fewer than {count} session(s) ever blocked on the held row")
