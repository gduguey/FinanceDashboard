"""Tests for `db.secrets`: the encrypted-at-rest key/value store every broker/LLM credential is built on."""

from __future__ import annotations

from typing import TYPE_CHECKING

from db.models import UserSecret
from db.secrets import delete_secret, get_secret, set_secret

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


def test_get_secret_returns_none_when_nothing_saved(db_session: Session, test_user_id: uuid.UUID) -> None:
    assert get_secret(db_session, test_user_id, "does-not-exist") is None


def test_set_then_get_secret_roundtrips_the_plaintext(db_session: Session, test_user_id: uuid.UUID) -> None:
    set_secret(db_session, test_user_id, key="ibkr-token", kind="broker_credentials", plaintext="super-secret-value")

    assert get_secret(db_session, test_user_id, "ibkr-token") == "super-secret-value"


def test_set_secret_never_stores_the_plaintext_in_the_row(db_session: Session, test_user_id: uuid.UUID) -> None:
    set_secret(db_session, test_user_id, key="ibkr-token", kind="broker_credentials", plaintext="super-secret-value")

    row = db_session.get(UserSecret, (test_user_id, "ibkr-token"))
    assert row is not None
    assert row.ciphertext != "super-secret-value"
    assert row.kind == "broker_credentials"
    assert row.encryption_key_version >= 1


def test_set_secret_overwrites_an_existing_value_for_the_same_key(db_session: Session, test_user_id: uuid.UUID) -> None:
    set_secret(db_session, test_user_id, key="ibkr-token", kind="broker_credentials", plaintext="first-value")
    set_secret(db_session, test_user_id, key="ibkr-token", kind="broker_credentials", plaintext="second-value")

    assert get_secret(db_session, test_user_id, "ibkr-token") == "second-value"


def test_delete_secret_removes_it(db_session: Session, test_user_id: uuid.UUID) -> None:
    set_secret(db_session, test_user_id, key="ibkr-token", kind="broker_credentials", plaintext="value")

    delete_secret(db_session, test_user_id, "ibkr-token")

    assert get_secret(db_session, test_user_id, "ibkr-token") is None


def test_delete_secret_is_a_no_op_when_nothing_was_saved(db_session: Session, test_user_id: uuid.UUID) -> None:
    delete_secret(db_session, test_user_id, "never-set")  # must not raise
