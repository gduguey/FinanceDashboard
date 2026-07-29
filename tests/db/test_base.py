"""Tests for `db.base`'s `uuid7()` primary-key default and the natural-key/id translation seam.

Replaces the tests for the content-hashed id these two mechanisms retired
between them (DB-audit D3) — the `uuid5` of `(user_id, table, natural_key)`
that used to be every row's primary key. What that hash guaranteed — "the
same natural key always names the same row" — is now guaranteed by
`UNIQUE (user_id, natural_key)`, and an id is looked up rather than
recomputed, so the properties worth testing changed shape entirely:

- the id itself has to be a **well-formed, time-ordered** UUIDv7, since
  nothing derives it any more and insert locality is the whole point;
- the **lookup** has to round-trip a natural key to an id and back.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import accounting.db as adb
import db.models
from db.base import ids_by_natural_key, natural_keys_by_id

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_UUID_VERSION_7 = 7
_RFC_9562_VARIANT = 0b10
"""The two high bits of the variant octet every RFC 9562 UUID must carry."""


def _tag_row(user_id: uuid.UUID, natural_key: str) -> adb.Tag:
    """Build a minimal row on a table whose primary key defaults to `uuid7()`.

    Returns
    -------
    accounting.db.Tag
    """
    return adb.Tag(user_id=user_id, natural_key=natural_key, name=natural_key)


def test_uuid7_default_fills_in_the_primary_key_on_flush(db_session: Session, test_user_id: uuid.UUID) -> None:
    """Nothing sets `id`, so the column's server default has to be what produces one.

    SQLAlchemy reads it back via `INSERT ... RETURNING id`, which is what
    lets every caller that used to recompute a derived id read the real one
    off the flushed instance instead.
    """
    row = _tag_row(test_user_id, "tag:trip")
    db_session.add(row)
    db_session.flush()

    assert row.id is not None


def test_uuid7_sets_the_version_and_variant_nibbles(db_session: Session, test_user_id: uuid.UUID) -> None:
    """A generated id must actually be a UUIDv7, not merely a UUID-shaped 16 bytes.

    `uuid.UUID.version` reads the version nibble and `.variant` the variant
    bits, so this checks the two fields `db.base._CREATE_UUID7_SQL` overwrites
    by hand — the ones a wrong mask or a wrong byte offset would corrupt.
    """
    row = _tag_row(test_user_id, "tag:trip")
    db_session.add(row)
    db_session.flush()

    assert row.id.version == _UUID_VERSION_7
    assert row.id.int >> 62 & 0b11 == _RFC_9562_VARIANT


def test_uuid7_encodes_the_current_time_in_its_leading_48_bits(db_session: Session, test_user_id: uuid.UUID) -> None:
    """The timestamp prefix has to be real milliseconds since the epoch, big-endian.

    A byte-order or scale mistake still produces ids that sort consistently
    among themselves, so ordering alone would not catch one — decoding the
    prefix back to a wall-clock time does. Ten minutes of slack covers any
    clock skew between this process and Postgres without admitting a
    prefix that is off by a factor of 1000 or byte-reversed.
    """
    before_ms = time.time() * 1000
    row = _tag_row(test_user_id, "tag:trip")
    db_session.add(row)
    db_session.flush()
    after_ms = time.time() * 1000

    encoded_ms = row.id.int >> 80
    slack_ms = 10 * 60 * 1000
    assert before_ms - slack_ms <= encoded_ms <= after_ms + slack_ms


def test_uuid7_batch_sorts_in_generation_order(db_session: Session, test_user_id: uuid.UUID) -> None:
    """The property the whole change exists for: a later insert's key sorts after an earlier one's.

    This is what makes inserts append to the right-hand edge of the primary
    key's B-tree instead of scattering through it (DB-audit D3), and it is
    exactly what a content hash could never provide.

    Spaced past the millisecond the timestamp is quantized to, because that
    is the resolution RFC 9562 v7 guarantees ordering at — within one
    millisecond `rand_a` is random here rather than a counter, which the spec
    permits, so ids minted inside the same millisecond are unordered among
    themselves by design.
    """
    ids: list[uuid.UUID] = []
    for index in range(5):
        row = _tag_row(test_user_id, f"tag:{index}")
        db_session.add(row)
        db_session.flush()
        ids.append(row.id)
        time.sleep(0.002)

    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_uuid7_is_distinct_per_row_within_one_millisecond(db_session: Session, test_user_id: uuid.UUID) -> None:
    """Two rows written in the same flush still get different ids — the random half has to be random."""
    rows = [_tag_row(test_user_id, f"tag:{index}") for index in range(50)]
    db_session.add_all(rows)
    db_session.flush()

    assert len({row.id for row in rows}) == len(rows)


def test_ids_by_natural_key_round_trips_through_natural_keys_by_id(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """The two halves of the translation seam are inverses over the rows that exist."""
    rows = [_tag_row(test_user_id, "tag:trip"), _tag_row(test_user_id, "tag:vacation")]
    db_session.add_all(rows)
    db_session.flush()

    forward = ids_by_natural_key(db_session, adb.Tag, test_user_id, ["tag:trip", "tag:vacation"])
    backward = natural_keys_by_id(db_session, adb.Tag, test_user_id, forward.values())

    assert forward == {"tag:trip": rows[0].id, "tag:vacation": rows[1].id}
    assert backward == {rows[0].id: "tag:trip", rows[1].id: "tag:vacation"}


def test_ids_by_natural_key_omits_a_key_with_no_row(db_session: Session, test_user_id: uuid.UUID) -> None:
    """A missing key is absent rather than mapped to `None`, so a caller can subscript to fail loudly."""
    db_session.add(_tag_row(test_user_id, "tag:trip"))
    db_session.flush()

    resolved = ids_by_natural_key(db_session, adb.Tag, test_user_id, ["tag:trip", "tag:never-created"])

    assert set(resolved) == {"tag:trip"}


def test_ids_by_natural_key_ignores_none_entries_and_empty_input(db_session: Session, test_user_id: uuid.UUID) -> None:
    """A `None` is "no reference to resolve", not a missing row — and an all-`None` batch queries nothing."""
    assert ids_by_natural_key(db_session, adb.Tag, test_user_id, []) == {}
    assert ids_by_natural_key(db_session, adb.Tag, test_user_id, [None, None]) == {}


def test_ids_by_natural_key_never_crosses_users(db_session: Session, test_user_id: uuid.UUID) -> None:
    """Another user's identical natural key is "does not exist" here, as Row-Level Security already makes it."""
    other_user_id = uuid.uuid4()
    db_session.add(db.models.User(id=other_user_id, email=f"{other_user_id}@example.com"))
    # Flushed before the tag: nothing relates the two mappers, so one flush
    # would leave the insert order to chance and trip `fk_tags_user_id_users`.
    db_session.flush()
    db_session.add(_tag_row(other_user_id, "tag:trip"))
    db_session.flush()

    assert ids_by_natural_key(db_session, adb.Tag, test_user_id, ["tag:trip"]) == {}
