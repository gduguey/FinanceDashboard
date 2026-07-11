"""Tests for `accounting.llm.settings`: per-user, DB-backed LLM provider API keys."""

from __future__ import annotations

from typing import TYPE_CHECKING

from accounting.llm.settings import (
    clear_llm_api_key,
    load_llm_api_key,
    resolve_llm_credentials,
    save_llm_api_key,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


def test_load_llm_api_key_with_nothing_saved_is_none(db_session: Session, test_user_id: uuid.UUID) -> None:
    assert load_llm_api_key(db_session, test_user_id, "gemini") is None


def test_save_then_load_llm_api_key_round_trips(db_session: Session, test_user_id: uuid.UUID) -> None:
    save_llm_api_key(db_session, test_user_id, "gemini", "gem-key")

    assert load_llm_api_key(db_session, test_user_id, "gemini") == "gem-key"


def test_providers_never_collide_for_the_same_user(db_session: Session, test_user_id: uuid.UUID) -> None:
    save_llm_api_key(db_session, test_user_id, "gemini", "gem-key")
    save_llm_api_key(db_session, test_user_id, "mistral", "mis-key")

    assert load_llm_api_key(db_session, test_user_id, "gemini") == "gem-key"
    assert load_llm_api_key(db_session, test_user_id, "mistral") == "mis-key"


def test_clear_llm_api_key_removes_it(db_session: Session, test_user_id: uuid.UUID) -> None:
    save_llm_api_key(db_session, test_user_id, "gemini", "gem-key")

    clear_llm_api_key(db_session, test_user_id, "gemini")

    assert load_llm_api_key(db_session, test_user_id, "gemini") is None


def test_resolve_llm_credentials_with_nothing_saved_leaves_both_unset(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    credentials = resolve_llm_credentials(db_session, test_user_id)

    assert credentials.gemini_api_key is None
    assert credentials.mistral_api_key is None


def test_resolve_llm_credentials_reflects_only_the_saved_provider(db_session: Session, test_user_id: uuid.UUID) -> None:
    save_llm_api_key(db_session, test_user_id, "gemini", "gem-key")

    credentials = resolve_llm_credentials(db_session, test_user_id)

    assert credentials.gemini_api_key is not None
    assert credentials.gemini_api_key.get_secret_value() == "gem-key"
    assert credentials.mistral_api_key is None
