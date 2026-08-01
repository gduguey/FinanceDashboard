"""Tests for `db.external_identities`: the (provider, external_id) -> internal user id mapping.

`external_identities` has no Row-Level Security (see that module's own
docstring) — but `lookup_user_id`/`link_identity` still take an explicit
`session`, same shape as `db.secrets.get_secret`/`set_secret`, so tests
exercise it through the same rolled-back-transaction `db_session` fixture
every other repository-style test in this repo uses, with no special
cross-connection handling needed.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from db.external_identities import link_identity, lookup_user_id
from db.models import ExternalIdentity, User

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_lookup_returns_none_when_nothing_is_linked(db_session: Session) -> None:
    assert lookup_user_id(db_session, "clerk", "user_does_not_exist") is None


def test_link_then_lookup_roundtrips(db_session: Session) -> None:
    user_id = uuid.uuid4()
    db_session.add(User(id=user_id, email=f"{user_id}@example.com"))
    db_session.commit()

    link_identity(db_session, user_id, "clerk", "user_abc123")
    db_session.commit()

    assert lookup_user_id(db_session, "clerk", "user_abc123") == user_id


def test_link_is_idempotent_for_a_repeated_delivery(db_session: Session) -> None:
    user_id = uuid.uuid4()
    db_session.add(User(id=user_id, email=f"{user_id}@example.com"))
    db_session.commit()

    link_identity(db_session, user_id, "clerk", "user_redelivered")
    link_identity(db_session, user_id, "clerk", "user_redelivered")
    db_session.commit()

    assert lookup_user_id(db_session, "clerk", "user_redelivered") == user_id


def test_different_providers_with_the_same_external_id_are_independent(db_session: Session) -> None:
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    db_session.add_all([
        User(id=user_a, email=f"{user_a}@example.com"),
        User(id=user_b, email=f"{user_b}@example.com"),
    ])
    db_session.commit()

    link_identity(db_session, user_a, "clerk", "shared-looking-id")
    link_identity(db_session, user_b, "other-provider", "shared-looking-id")
    db_session.commit()

    assert lookup_user_id(db_session, "clerk", "shared-looking-id") == user_a
    assert lookup_user_id(db_session, "other-provider", "shared-looking-id") == user_b


def test_reassigning_an_external_id_to_a_different_user_is_a_one_row_update(db_session: Session) -> None:
    """The whole point of this table: reconnecting someone after a delete/re-invite touches one row, not 31 tables."""
    original_user_id = uuid.uuid4()
    db_session.add(User(id=original_user_id, email=f"{original_user_id}@example.com"))
    db_session.commit()
    link_identity(db_session, original_user_id, "clerk", "user_original_signup")
    db_session.commit()

    identity = db_session.get(ExternalIdentity, ("clerk", "user_original_signup"))
    assert identity is not None
    identity.external_id = "user_after_delete_and_reinvite"
    db_session.commit()

    assert lookup_user_id(db_session, "clerk", "user_original_signup") is None
    assert lookup_user_id(db_session, "clerk", "user_after_delete_and_reinvite") == original_user_id


def test_link_returns_the_id_it_linked(db_session: Session) -> None:
    """The ordinary case, and the value `db.provisioning` compares against to detect that it lost."""
    user_id = uuid.uuid4()
    db_session.add(User(id=user_id, email=f"{user_id}@example.com"))
    db_session.commit()

    assert link_identity(db_session, user_id, "clerk", "user_fresh_link") == user_id


def test_link_returns_the_existing_id_rather_than_the_one_offered(db_session: Session) -> None:
    """The silent case this return value exists for.

    A second caller offering a *different* user id for an already-linked
    external account does not conflict on anything it can see: `users.email`
    carries no unique constraint (see `db.models.User`), so its own `users`
    row inserts happily, and `ON CONFLICT DO NOTHING` then declines to
    repoint the link without saying so. Returning the offered id here is
    what stranded a user; returning the linked one is what
    `db.provisioning` needs to roll its orphan back.
    """
    first_user_id = uuid.uuid4()
    second_user_id = uuid.uuid4()
    db_session.add_all([
        User(id=first_user_id, email="shared@example.com"),
        User(id=second_user_id, email="shared@example.com"),
    ])
    db_session.commit()

    link_identity(db_session, first_user_id, "clerk", "user_contested")
    db_session.commit()

    assert link_identity(db_session, second_user_id, "clerk", "user_contested") == first_user_id
    assert lookup_user_id(db_session, "clerk", "user_contested") == first_user_id
