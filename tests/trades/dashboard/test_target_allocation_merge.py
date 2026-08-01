"""Two concurrent merge patches of different symbols, and the promise that both survive (A4b).

`application/merge-patch+json` does not merely permit patches of disjoint
keys to compose — composing is what the media type is *for*. The handler
used to read the whole `symbol -> percentage` map into Python, add its own
key to the copy it read and write the map back, so two patches naming
different symbols did not compose at all: the later commit silently dropped
the earlier one, with no conflict, no 409 and nothing in either response to
say a write had been lost.

The fix is a database-side `jsonb` merge rather than a version column, and
the distinction matters for what these tests assert. A version would have
made the two patches *conflict* — one of them 409s and is retried — which is
a worse answer than merging them, and `dashboard.settings.save_settings`
documents at length why this one row is deliberately last-write-wins. The
merge composes by construction instead: `ON CONFLICT DO UPDATE` re-reads the
row it is updating, so the second patch's `||` lands on the first's
committed value.

So the interesting test is not "does a patch work" — `tests/trades/api/test_api.py`
covers the semantics through the route — but "do two of them, contending for
the same row at the same instant, both end up in it". That needs two
connections whose transactions really commit, which is why this is its own
module rather than another case in the API suite: the `db_session` fixture
hands out one connection inside one never-committed transaction.
"""

from __future__ import annotations

import threading
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import db.models
from tests.support.concurrency import THREAD_TIMEOUT_SECONDS, wait_until_a_backend_blocks_on_a_lock
from trades.dashboard.settings import merge_target_allocation
from trades.db.models import DashboardSettings as DashboardSettingsRow

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine


@pytest.fixture
def committed_user_id(_db_engine: Engine) -> Iterator[uuid.UUID]:
    """A `User` row that is really committed, so a second connection can foreign-key against it.

    `tests/conftest.py`'s `test_user_id` writes inside a transaction that is
    never committed, so no other session can see it and
    `fk_dashboard_settings_user_id_users` would reject every row here.

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
        teardown.query(DashboardSettingsRow).filter_by(user_id=user_id).delete()
        teardown.query(db.models.User).filter_by(id=user_id).delete()
        teardown.commit()


def _allocation(engine: Engine, user_id: uuid.UUID) -> dict[str, Decimal]:
    """Read the persisted allocation back on its own connection.

    Returns
    -------
    dict[str, decimal.Decimal]
    """
    with Session(engine) as reader:
        row = reader.get(DashboardSettingsRow, user_id)
        return dict(row.target_allocation_pct) if row is not None else {}


def test_a_first_patch_creates_the_settings_row(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    committed_user_id: uuid.UUID,
) -> None:
    """The row is created lazily, so an upsert rather than an update — a user's first patch must land."""
    with Session(_db_engine) as session:
        result = merge_target_allocation(session, committed_user_id, {"VOO": Decimal(60)})

    assert result == {"VOO": Decimal(60)}
    assert _allocation(_db_engine, committed_user_id) == {"VOO": Decimal(60)}


def test_one_patch_can_set_and_delete_at_once(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    committed_user_id: uuid.UUID,
) -> None:
    """A body that both sets a symbol and nulls another, which is where operator precedence bites.

    Postgres binds binary `-` tighter than `||`, so the unparenthesised
    merge would compute `patch - removals` and concatenate *that* — applying
    the deletion to the incoming patch instead of to the stored map. The
    delete-only case looks like a plain no-op when that happens; this case
    is the one where the set half still works and the delete half quietly
    does not, which is harder to notice.
    """
    with Session(_db_engine) as session:
        merge_target_allocation(session, committed_user_id, {"VOO": Decimal(60), "BND": Decimal(40)})
        result = merge_target_allocation(session, committed_user_id, {"BND": None, "NVDA": Decimal(15)})

    assert result == {"VOO": Decimal(60), "NVDA": Decimal(15)}


def test_the_exact_decimal_survives_the_round_trip(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    committed_user_id: uuid.UUID,
) -> None:
    """The merge goes through `db.base.RateMap` rather than formatting JSON by hand.

    That is what keeps a rate an exact decimal string in the column instead
    of the nearest double — the reason `RateMap` exists — and it has to hold
    for a value written by this statement just as much as for one written
    through the ORM.
    """
    with Session(_db_engine) as session:
        merge_target_allocation(session, committed_user_id, {"VTI": Decimal("33.333333")})

    assert _allocation(_db_engine, committed_user_id) == {"VTI": Decimal("33.333333")}


def test_two_concurrent_patches_of_different_symbols_both_survive(
    _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    committed_user_id: uuid.UUID,
) -> None:
    """The whole point of A4b: disjoint patches compose, and neither is lost.

    Both contenders are genuinely queued on the same row before either can
    proceed. A third session takes `SELECT ... FOR UPDATE` on it and holds
    it; both patches then block *inside Postgres* on that lock, which is
    waited for rather than slept on. Releasing the holder lets them run back
    to back against the same row, which is exactly the interleaving that used
    to lose a write — under the old read-modify-write both had already read
    the map, and whichever committed second wrote its own stale copy over the
    other's symbol.

    Nothing here mocks the contention: the composition being asserted is a
    property of `ON CONFLICT DO UPDATE` re-reading under READ COMMITTED, so
    only a real second connection can demonstrate it.
    """
    starting = {"SPY": Decimal(10)}
    with Session(_db_engine) as setup:
        merge_target_allocation(setup, committed_user_id, starting)

    outcomes: dict[str, Any] = {}

    def patch(name: str, symbol: str, value: str) -> None:
        try:
            with Session(_db_engine) as session:
                outcomes[name] = merge_target_allocation(session, committed_user_id, {symbol: Decimal(value)})
        except BaseException as error:  # noqa: BLE001 — re-raised on the main thread below
            outcomes[f"{name}-error"] = error

    threads = [
        threading.Thread(target=patch, args=("a", "VOO", "60"), daemon=True),
        threading.Thread(target=patch, args=("b", "BND", "30"), daemon=True),
    ]

    with Session(_db_engine) as holder:
        holder.execute(
            text("SELECT 1 FROM trades.dashboard_settings WHERE user_id = :u FOR UPDATE"),
            {"u": committed_user_id},
        )
        for thread in threads:
            thread.start()
        try:
            wait_until_a_backend_blocks_on_a_lock(_db_engine, count=2)
        finally:
            # Always release, even if the wait timed out: both threads are
            # parked on this transaction and would otherwise hang the suite.
            holder.rollback()

    for thread in threads:
        thread.join(timeout=THREAD_TIMEOUT_SECONDS)
        assert not thread.is_alive()

    assert "a-error" not in outcomes, outcomes.get("a-error")
    assert "b-error" not in outcomes, outcomes.get("b-error")
    assert _allocation(_db_engine, committed_user_id) == {
        "SPY": Decimal(10),
        "VOO": Decimal(60),
        "BND": Decimal(30),
    }, "a concurrent patch of a different symbol was lost, which is the thing merge-patch promises cannot happen"
