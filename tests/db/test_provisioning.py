"""Two real sessions racing `db.provisioning.provision_linked_user` for the same external account.

Separate from the rest of the `db` suite for the reason
`test_base_concurrency.py` gives: the `db_session` fixture hands every test
one connection inside one transaction that is rolled back at the end, and a
race needs two connections whose transactions can genuinely see (or fail to
see) each other's committed work. So the sessions here **commit for real**
and clean up after themselves.

What makes this worth forcing rather than asserting from the outside is that
the bug it covers has no error in it. Two concurrent first provisions of the
same Clerk account each mint their own `uuid4`, and `users.email` carries no
unique constraint, so both `users` inserts succeed — there is nothing to
collide on but `external_identities`' own primary key, and `ON CONFLICT DO
NOTHING` resolves that in silence. The loser used to walk away with its own
id: an orphaned `users` row, and, on the auth path, a request's worth of
writes stranded under an id no later sign-in would ever resolve to. A
duplicate-key error would have been the *good* outcome. So the assertions
below are about which id came back and how many rows exist, not about
anything raising.
"""

from __future__ import annotations

import threading
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy.orm import Session

import db.session as session_module
from db.external_identities import link_identity, lookup_user_id
from db.models import ExternalIdentity, User
from db.provisioning import provision_linked_user
from tests.support.concurrency import THREAD_TIMEOUT_SECONDS, wait_until_a_backend_blocks_on_a_lock

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine

_PROVIDER = "clerk"


@pytest.fixture
def external_id() -> str:
    """One external account id, unique per test so nothing leaks between runs.

    Returns
    -------
    str
    """
    return f"user_provisioning_{uuid.uuid4().hex}"


@pytest.fixture(autouse=True)
def _provisioning_uses_the_test_engine(monkeypatch: pytest.MonkeyPatch, _db_engine: Engine) -> None:
    """Point `provision_linked_user`'s own `session_scope` at the test database.

    It opens its own session rather than taking one (it has to — the session
    must be scoped to a user id that does not exist yet), so like
    `trades.api.webhooks` it goes through the process-wide `get_engine`
    cache rather than through any fixture.
    """
    monkeypatch.setattr(session_module, "get_engine", lambda: _db_engine)


@pytest.fixture
def cleanup(_db_engine: Engine, external_id: str) -> Iterator[None]:
    """Delete whatever the test committed, since nothing here runs inside a rolled-back transaction.

    Yields
    ------
    None
    """
    yield
    with Session(_db_engine) as teardown:
        linked = teardown.query(ExternalIdentity).filter_by(provider=_PROVIDER, external_id=external_id).all()
        user_ids = [row.user_id for row in linked]
        for row in linked:
            teardown.delete(row)
        teardown.flush()
        for user_id in user_ids:
            teardown.query(User).filter_by(id=user_id).delete()
        # Any orphan the race left behind is identified by its email, since
        # by definition it has no link row to find it by. Deleting it here
        # keeps a failed run from poisoning the next one; the assertion that
        # it should not exist is in the test itself.
        teardown.query(User).filter_by(email=_orphan_email(external_id)).delete()
        teardown.commit()


def _orphan_email(external_id: str) -> str:
    """The email the losing provision would write, which is how an orphaned row is found.

    Returns
    -------
    str
    """
    return f"loser-{external_id}@example.test"


def test_provisioning_an_account_that_has_none_creates_the_row_and_its_link(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    external_id: str,
    cleanup: None,
) -> None:
    """The ordinary first sign-in: one `users` row, one link, and the id they agree on."""
    del cleanup
    user_id = provision_linked_user(_PROVIDER, external_id, "first@example.test")

    with Session(_db_engine) as reader:
        assert lookup_user_id(reader, _PROVIDER, external_id) == user_id
        user = reader.get(User, user_id)
        assert user is not None
        assert user.email == "first@example.test"
        assert user.is_active


def test_provisioning_twice_returns_the_same_id_and_writes_one_row(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    external_id: str,
    cleanup: None,
) -> None:
    """A webhook redelivery, or a just-in-time attempt after the webhook already landed."""
    del cleanup
    first = provision_linked_user(_PROVIDER, external_id, "first@example.test")
    second = provision_linked_user(_PROVIDER, external_id, "second@example.test")

    assert second == first
    with Session(_db_engine) as reader:
        assert reader.query(ExternalIdentity).filter_by(provider=_PROVIDER, external_id=external_id).count() == 1
        # The second call must not have overwritten the first's display
        # information either — `link_identity` never repoints an existing link.
        user = reader.get(User, first)
        assert user is not None
        assert user.email == "first@example.test"


def test_the_loser_of_a_concurrent_first_provision_adopts_the_winners_id(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    external_id: str,
    cleanup: None,
) -> None:
    """The whole point: two first provisions at once, and the loser must not keep an id of its own.

    The interleaving is forced by Postgres rather than by patching anything.
    The winner inserts its `users` row and its link and holds the
    transaction open, so the index entry exists but is invisible; the
    loser's own lookup therefore honestly finds nothing, it inserts a
    perfectly legal `users` row of its own (nothing to collide on — see this
    module's docstring), and its link insert then *blocks* on the winner's
    uncommitted entry. Committing the winner at that moment is what turns
    the wait into `ON CONFLICT DO NOTHING` doing nothing, which is the
    interleaving that used to produce a stranded user.

    Three assertions, and the third is the one that would have caught the
    original bug: the loser returned the winner's id, exactly one link
    exists, and **the loser's own `users` row is gone** — rolled back with
    the savepoint rather than left behind.
    """
    del cleanup
    winner_id = uuid.uuid4()
    outcome: dict[str, Any] = {}

    def lose_the_race() -> None:
        try:
            outcome["resolved"] = provision_linked_user(_PROVIDER, external_id, _orphan_email(external_id))
        except BaseException as error:  # noqa: BLE001 — re-raised on the main thread below
            outcome["error"] = error

    thread = threading.Thread(target=lose_the_race, daemon=True)
    with Session(_db_engine) as winner:
        winner.add(User(id=winner_id, email=f"winner-{external_id}@example.test"))
        winner.flush()
        link_identity(winner, winner_id, _PROVIDER, external_id)
        winner.flush()

        thread.start()
        try:
            wait_until_a_backend_blocks_on_a_lock(_db_engine)
        finally:
            # Always commit, even if the wait timed out: the losing thread is
            # parked on this transaction and would otherwise hang the suite.
            winner.commit()
    thread.join(timeout=THREAD_TIMEOUT_SECONDS)

    assert not thread.is_alive()
    assert "error" not in outcome, outcome.get("error")
    assert outcome["resolved"] == winner_id, "the loser kept an id of its own instead of adopting the winner's"

    with Session(_db_engine) as reader:
        links = reader.query(ExternalIdentity).filter_by(provider=_PROVIDER, external_id=external_id).all()
        assert len(links) == 1
        assert links[0].user_id == winner_id
        orphans = reader.query(User).filter_by(email=_orphan_email(external_id)).all()
        assert orphans == [], (
            "the losing provision left a `users` row nothing links to — every row the caller goes on to "
            "write under that id is unreachable from the next sign-in"
        )
