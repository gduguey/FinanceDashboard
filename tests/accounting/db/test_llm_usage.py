"""Tests for `accounting.llm.usage`: the Postgres-backed per-(user, provider) LLM call counter."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import db.models as dbm
from accounting.llm.usage import load_usage, record_call, save_usage

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_load_usage_with_nothing_saved_starts_every_provider_at_zero(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    usage = load_usage(db_session, test_user_id)
    assert usage["gemini"].used_count == 0
    assert usage["gemini"].is_limited is False
    assert usage["mistral"].used_count == 0


def test_save_then_load_usage_round_trips(db_session: Session, test_user_id: uuid.UUID) -> None:
    usage = load_usage(db_session, test_user_id)
    usage["gemini"] = usage["gemini"].model_copy(update={"used_count": 5})

    save_usage(usage, db_session, test_user_id)

    assert load_usage(db_session, test_user_id)["gemini"].used_count == 5


def test_record_call_increments_count_on_success(db_session: Session, test_user_id: uuid.UUID) -> None:
    record_call("gemini", db_session, test_user_id, error=None)
    record_call("gemini", db_session, test_user_id, error=None)

    usage = load_usage(db_session, test_user_id)
    assert usage["gemini"].used_count == 2
    assert usage["gemini"].is_limited is False
    assert usage["gemini"].last_error is None


def test_record_call_marks_limited_on_failure_and_keeps_the_error(db_session: Session, test_user_id: uuid.UUID) -> None:
    record_call("gemini", db_session, test_user_id, error="429 RESOURCE_EXHAUSTED: quota exceeded")

    usage = load_usage(db_session, test_user_id)
    assert usage["gemini"].used_count == 0
    assert usage["gemini"].is_limited is True
    assert usage["gemini"].last_error == "429 RESOURCE_EXHAUSTED: quota exceeded"


def test_load_usage_does_not_leak_between_users(db_session: Session, test_user_id: uuid.UUID) -> None:
    other_user_id = uuid.uuid4()
    db_session.add(dbm.User(id=other_user_id, email=f"{other_user_id}@example.com"))
    db_session.commit()

    record_call("gemini", db_session, test_user_id, error=None)

    assert load_usage(db_session, other_user_id)["gemini"].used_count == 0
