"""Tests for `trades.brokers.ibkr.credentials`: the IBKR-specific adapter over `trades.broker_credentials`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from trades.brokers.ibkr.credentials import (
    IbkrCredentialsNotConfiguredError,
    clear_ibkr_credentials,
    ibkr_credential_fields,
    ibkr_is_configured,
    resolve_ibkr_credentials,
    save_ibkr_credentials,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


def test_resolve_ibkr_credentials_raises_when_nothing_is_saved(db_session: Session, test_user_id: uuid.UUID) -> None:
    with pytest.raises(IbkrCredentialsNotConfiguredError):
        resolve_ibkr_credentials(db_session, test_user_id)


def test_save_then_resolve_ibkr_credentials(db_session: Session, test_user_id: uuid.UUID) -> None:
    save_ibkr_credentials(db_session, test_user_id, token="secret-token", query_id="12345")  # noqa: S106

    credentials = resolve_ibkr_credentials(db_session, test_user_id)

    assert credentials.token.get_secret_value() == "secret-token"
    assert credentials.query_id == "12345"


def test_save_ibkr_credentials_merges_a_partial_update(db_session: Session, test_user_id: uuid.UUID) -> None:
    save_ibkr_credentials(db_session, test_user_id, token="secret-token", query_id="12345")  # noqa: S106

    save_ibkr_credentials(db_session, test_user_id, query_id="67890")

    credentials = resolve_ibkr_credentials(db_session, test_user_id)
    assert credentials.token.get_secret_value() == "secret-token"
    assert credentials.query_id == "67890"


def test_resolve_ibkr_credentials_raises_when_only_one_field_is_saved(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    save_ibkr_credentials(db_session, test_user_id, token="secret-token")  # noqa: S106

    with pytest.raises(IbkrCredentialsNotConfiguredError):
        resolve_ibkr_credentials(db_session, test_user_id)


def test_ibkr_is_configured_false_until_both_fields_are_saved(db_session: Session, test_user_id: uuid.UUID) -> None:
    assert ibkr_is_configured(db_session, test_user_id) is False

    save_ibkr_credentials(db_session, test_user_id, token="secret-token")  # noqa: S106
    assert ibkr_is_configured(db_session, test_user_id) is False

    save_ibkr_credentials(db_session, test_user_id, query_id="12345")
    assert ibkr_is_configured(db_session, test_user_id) is True


def test_ibkr_credential_fields_never_needed_to_expose_raw_values_to_report_what_is_set(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    save_ibkr_credentials(db_session, test_user_id, token="secret-token", query_id="12345")  # noqa: S106

    fields = ibkr_credential_fields(db_session, test_user_id)

    assert set(fields) == {"token", "query_id"}


def test_clear_ibkr_credentials_removes_them(db_session: Session, test_user_id: uuid.UUID) -> None:
    save_ibkr_credentials(db_session, test_user_id, token="secret-token", query_id="12345")  # noqa: S106

    clear_ibkr_credentials(db_session, test_user_id)

    assert ibkr_is_configured(db_session, test_user_id) is False
