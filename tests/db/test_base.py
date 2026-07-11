"""Tests for `db.base.derive_id`: the deterministic natural-key -> surrogate-id derivation."""

from __future__ import annotations

import uuid

from db.base import derive_id


def test_derive_id_is_deterministic() -> None:
    user_id = uuid.uuid4()

    first = derive_id(user_id, "accounts", "sofi:checking:1234")
    second = derive_id(user_id, "accounts", "sofi:checking:1234")

    assert first == second


def test_derive_id_differs_by_natural_key() -> None:
    user_id = uuid.uuid4()

    assert derive_id(user_id, "accounts", "a") != derive_id(user_id, "accounts", "b")


def test_derive_id_differs_by_table() -> None:
    user_id = uuid.uuid4()

    assert derive_id(user_id, "accounts", "x") != derive_id(user_id, "categories", "x")


def test_derive_id_differs_by_user() -> None:
    assert derive_id(uuid.uuid4(), "accounts", "x") != derive_id(uuid.uuid4(), "accounts", "x")


def test_derive_id_output_is_pinned() -> None:
    """Guards `_ID_NAMESPACE` itself against an accidental edit slipping through review.

    `derive_id` must keep producing this exact id for this exact input,
    forever — every already-derived id in any real database depends on
    `_ID_NAMESPACE` never changing (see `db.base`'s own docstring, and
    `src/db/README.md`). A one-character edit to that constant wouldn't
    change any test above (they only compare derived ids against each
    other), but it would silently invalidate every id ever derived from
    real data — this test fails loudly on that specific change instead.
    """
    fixed_user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    assert derive_id(fixed_user_id, "accounts", "chase:checking:1234") == uuid.UUID(
        "065d8eef-d911-5a94-9f0e-759b3fbfee66"
    )
