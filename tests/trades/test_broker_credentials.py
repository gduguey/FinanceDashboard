"""Tests for `trades.broker_credentials`: generic, DB-backed, per-broker credential storage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from trades.broker_credentials import (
    broker_is_configured,
    clear_broker_credentials,
    load_broker_credentials,
    save_broker_credentials,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


def test_load_broker_credentials_with_nothing_saved_is_empty(db_session: Session, test_user_id: uuid.UUID) -> None:
    assert load_broker_credentials(db_session, test_user_id, "ibkr") == {}


def test_save_then_load_broker_credentials_round_trips(db_session: Session, test_user_id: uuid.UUID) -> None:
    save_broker_credentials(db_session, test_user_id, "ibkr", {"token": "secret-token", "query_id": "12345"})

    fields = load_broker_credentials(db_session, test_user_id, "ibkr")

    assert fields == {"token": "secret-token", "query_id": "12345"}


def test_save_broker_credentials_overwrites_the_full_set(db_session: Session, test_user_id: uuid.UUID) -> None:
    save_broker_credentials(db_session, test_user_id, "ibkr", {"token": "first", "query_id": "111"})
    save_broker_credentials(db_session, test_user_id, "ibkr", {"token": "second"})

    fields = load_broker_credentials(db_session, test_user_id, "ibkr")

    assert fields == {"token": "second"}  # query_id from the first save is gone: this call replaced the whole set


def test_different_brokers_never_collide_for_the_same_user(db_session: Session, test_user_id: uuid.UUID) -> None:
    save_broker_credentials(db_session, test_user_id, "ibkr", {"token": "ibkr-token"})
    save_broker_credentials(db_session, test_user_id, "schwab", {"token": "schwab-token"})

    assert load_broker_credentials(db_session, test_user_id, "ibkr") == {"token": "ibkr-token"}
    assert load_broker_credentials(db_session, test_user_id, "schwab") == {"token": "schwab-token"}


def test_clear_broker_credentials_removes_them(db_session: Session, test_user_id: uuid.UUID) -> None:
    save_broker_credentials(db_session, test_user_id, "ibkr", {"token": "secret-token", "query_id": "12345"})

    clear_broker_credentials(db_session, test_user_id, "ibkr")

    assert load_broker_credentials(db_session, test_user_id, "ibkr") == {}


def test_broker_is_configured_false_when_a_required_field_is_missing(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    save_broker_credentials(db_session, test_user_id, "ibkr", {"token": "secret-token"})

    assert broker_is_configured(db_session, test_user_id, "ibkr", ("token", "query_id")) is False


def test_broker_is_configured_true_once_every_required_field_is_set(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    save_broker_credentials(db_session, test_user_id, "ibkr", {"token": "secret-token", "query_id": "12345"})

    assert broker_is_configured(db_session, test_user_id, "ibkr", ("token", "query_id")) is True
