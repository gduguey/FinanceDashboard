"""Two real sessions racing `db.base.merge_by_natural_key` on the same new natural key.

Separate from `test_base.py` because these tests cannot use the `db_session`
fixture at all, and the reason is the point of the file: that fixture hands
every test one connection inside one transaction that is rolled back at the
end, and a race needs two connections whose transactions can genuinely see
(or fail to see) each other's committed work. So the sessions here **commit
for real** against the shared test database and clean up after themselves,
which is also why every row they write is scoped to a throwaway user id.

Nothing here fakes the failure. The `IntegrityError` that
`merge_by_natural_key` recovers from is raised by Postgres, against the real
`UNIQUE (user_id, natural_key)` index, because a second connection really did
commit that key first — a mocked `IntegrityError` would have proved only that
the `except` clause is spelled correctly, not that the SAVEPOINT rollback
leaves the loser's session usable, that the re-lookup under READ COMMITTED
actually sees the winner's row, or that the retry lands as an `UPDATE` on it.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

import accounting.db as adb
import db.models
from db.base import NATURAL_KEY_MERGE_ATTEMPTS, ConcurrentNaturalKeyInsertError, merge_by_natural_key

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine

_RACED_KEY = "tag:raced"
"""The one natural key both sessions try to create."""

_LOCK_WAIT_TIMEOUT_SECONDS = 15.0
"""How long to wait for the losing backend to actually block on the winner's uncommitted row."""

_THREAD_TIMEOUT_SECONDS = 30.0
"""How long to wait for the losing thread to finish once the winner has committed."""


def _tag_row(user_id: uuid.UUID, natural_key: str, name: str) -> adb.Tag:
    """Build one `tags` row, whose `(user_id, natural_key)` is the unique constraint under test.

    Returns
    -------
    accounting.db.Tag
    """
    return adb.Tag(user_id=user_id, natural_key=natural_key, name=name)


@pytest.fixture
def committed_user_id(_db_engine: Engine) -> Iterator[uuid.UUID]:
    """A `User` row that is really committed, so a *second* connection can foreign-key against it.

    `tests/conftest.py`'s `test_user_id` cannot serve: it writes inside the
    `db_session` fixture's never-committed transaction, so no other session
    can see the user, and `fk_tags_user_id_users` would reject every row
    written here. Torn down by hand for the same reason — there is no
    enclosing rollback to undo any of this.

    Yields
    ------
    uuid.UUID
    """
    user_id = uuid.uuid4()
    with Session(_db_engine) as setup:
        setup.add(db.models.User(id=user_id, email=f"{user_id}@example.com"))
        setup.commit()
    yield user_id
    with Session(_db_engine) as teardown:
        teardown.query(adb.Tag).filter_by(user_id=user_id).delete()
        teardown.query(db.models.User).filter_by(id=user_id).delete()
        teardown.commit()


def _wait_until_a_backend_blocks_on_a_lock(engine: Engine) -> None:
    """Block until some backend in this database is waiting on a lock, or fail the test.

    The synchronisation point of the race below. The losing session runs in
    its own thread and, on hitting the winner's uncommitted duplicate key,
    stops *inside Postgres* — no Python-visible event fires — so the only
    honest way to know it has got there is to ask Postgres. `pg_stat_activity`
    reports live backend state rather than an MVCC snapshot, so a third
    connection polling it sees the wait as soon as it starts.

    Deliberately not a `sleep`: a fixed sleep either flakes on a slow machine
    or makes every run pay for the slowest one, and worse, a sleep that is too
    short would let the winner commit *before* the loser's insert is even
    sent, which is a different (and much easier) interleaving than the one
    this file exists to test.
    """
    deadline = time.monotonic() + _LOCK_WAIT_TIMEOUT_SECONDS
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as watcher:
        while time.monotonic() < deadline:
            waiting = watcher.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            if waiting:
                return
            time.sleep(0.02)
    pytest.fail("the losing session never blocked on the winner's uncommitted row")


def test_a_lost_insert_race_retries_into_an_update_of_the_winners_row(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    committed_user_id: uuid.UUID,
) -> None:
    """The whole point: two sessions insert the same new natural key, and the loser recovers.

    The interleaving is forced by Postgres itself rather than by patching
    anything. The winner flushes its `INSERT` and holds the transaction open,
    so the index entry exists but is invisible; the loser's own lookup
    therefore honestly finds no row, and its `INSERT` then *blocks* on that
    uncommitted entry. Committing the winner at that moment is what turns the
    loser's wait into a real `duplicate key value violates unique constraint`
    — precisely the ordering that produced the 500 this retry exists to fix.

    The loser also has earlier work in the same transaction (an unrelated
    tag it flushed before calling), which must survive untouched. That is the
    property the SAVEPOINT buys over a plain retry loop, and the reason five
    of the six call sites can keep calling this from inside a larger
    transaction.
    """
    unrelated_key = "tag:the-losers-earlier-work"
    outcome: dict[str, Any] = {}

    def lose_the_race() -> None:
        with Session(_db_engine) as loser:
            try:
                loser.add(_tag_row(committed_user_id, unrelated_key, "earlier work"))
                loser.flush()
                outcome["ids"] = merge_by_natural_key(
                    loser, adb.Tag, committed_user_id, [_tag_row(committed_user_id, _RACED_KEY, "loser")]
                )
                loser.commit()
            except BaseException as error:  # noqa: BLE001 — re-raised on the main thread below
                outcome["error"] = error

    thread = threading.Thread(target=lose_the_race, daemon=True)
    with Session(_db_engine) as winner:
        winner_ids = merge_by_natural_key(
            winner, adb.Tag, committed_user_id, [_tag_row(committed_user_id, _RACED_KEY, "winner")]
        )
        thread.start()
        try:
            _wait_until_a_backend_blocks_on_a_lock(_db_engine)
        finally:
            # Always commit, even if the wait timed out: the losing thread is
            # parked on this transaction and would otherwise hang the suite.
            winner.commit()
    thread.join(timeout=_THREAD_TIMEOUT_SECONDS)

    assert not thread.is_alive()
    assert "error" not in outcome, outcome.get("error")
    # The loser succeeded, and against the winner's row rather than one of its own.
    assert outcome["ids"][_RACED_KEY] == winner_ids[_RACED_KEY]

    with Session(_db_engine) as reader:
        rows = reader.query(adb.Tag).filter_by(user_id=committed_user_id).order_by(adb.Tag.natural_key).all()
        raced = [row for row in rows if row.natural_key == _RACED_KEY]

        assert len(raced) == 1
        assert raced[0].id == winner_ids[_RACED_KEY]
        # The retry was an UPDATE of the winner's row, not a no-op: the
        # loser's own column values are what is persisted.
        assert raced[0].name == "loser"
        # The loser's pre-existing work in the same transaction survived the
        # savepoint rollback and was committed alongside the retry.
        assert {row.natural_key for row in rows} == {_RACED_KEY, unrelated_key}


def test_the_retry_bound_fails_loudly_instead_of_spinning(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    committed_user_id: uuid.UUID,
) -> None:
    """A competitor that keeps committing new keys of this batch exhausts the bound and raises.

    Genuinely unwinnable rather than artificially so: the batch holds one
    more new natural key than there are attempts, and a second connection
    commits the next one as each attempt opens its savepoint — after that
    attempt's natural-key lookup, so the row really is a surprise. Every
    attempt therefore collides with a *different* freshly committed row, and
    every re-lookup really does show progress, which is the only thing that
    buys another attempt. Nothing raises a fake `IntegrityError`; the
    listener chooses only *when* the competitor commits, and Postgres decides
    the rest.

    Hooked to `after_transaction_create` rather than to `before_flush`
    because the retry is per *attempt*, not per flush: one attempt flushes
    several times (`merge()` autoflushes before loading a row it has an id
    for), and a competitor firing on each of those would hand over every
    contested key inside the first attempt or two and let the batch win. A
    savepoint is opened exactly once per attempt.

    What must not happen is the loop absorbing this quietly — either by
    spinning forever on a held connection or by returning a partially
    written batch — so the assertion is on the named error.
    """
    keys = [f"tag:contested-{index}" for index in range(NATURAL_KEY_MERGE_ATTEMPTS + 1)]

    with Session(_db_engine) as victim:
        remaining = iter(keys)

        # `transaction` is a SessionTransaction; annotated `Any` because
        # SQLAlchemy does not export that class for annotation.
        def commit_one_key_as_each_attempt_opens(_session: Session, transaction: Any) -> None:
            if not transaction.nested:
                return
            next_key = next(remaining, None)
            if next_key is None:
                return
            with Session(_db_engine) as competitor:
                competitor.add(_tag_row(committed_user_id, next_key, "competitor"))
                competitor.commit()

        event.listen(victim, "after_transaction_create", commit_one_key_as_each_attempt_opens)
        try:
            with pytest.raises(ConcurrentNaturalKeyInsertError, match="lost the insert race"):
                merge_by_natural_key(
                    victim, adb.Tag, committed_user_id, [_tag_row(committed_user_id, key, "victim") for key in keys]
                )
        finally:
            event.remove(victim, "after_transaction_create", commit_one_key_as_each_attempt_opens)
            victim.rollback()

    # The competitor's rows are the only ones that exist: the victim's batch
    # was never partially written, because every attempt was undone by its
    # own savepoint.
    with Session(_db_engine) as reader:
        names = {row.name for row in reader.query(adb.Tag).filter_by(user_id=committed_user_id)}

        assert names == {"competitor"}
