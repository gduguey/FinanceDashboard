from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from accounting.llm.provider import LLMProviderError
from accounting.llm.usage import TrackedProvider, load_usage, record_call, save_usage

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


class _FakeProvider:
    def __init__(self, response: str | None = None, error: str | None = None) -> None:
        self._response = response
        self._error = error

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if self._error is not None:
            raise LLMProviderError(self._error)
        assert self._response is not None
        return self._response


def test_load_usage_with_nothing_saved_starts_every_provider_at_zero(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    usage = load_usage(db_session, test_user_id)
    assert usage["gemini"].used_count == 0
    assert usage["gemini"].is_limited is False
    assert usage["mistral"].used_count == 0


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
    # A failed call doesn't count against the quota shown to the user — the
    # counter freezes at whatever it was, only the limited flag/error move.
    assert usage["gemini"].used_count == 0
    assert usage["gemini"].is_limited is True
    assert usage["gemini"].last_error == "429 RESOURCE_EXHAUSTED: quota exceeded"


def test_record_call_success_clears_a_previous_limited_state(db_session: Session, test_user_id: uuid.UUID) -> None:
    record_call("gemini", db_session, test_user_id, error="429 quota exceeded")
    record_call("gemini", db_session, test_user_id, error=None)
    usage = load_usage(db_session, test_user_id)
    assert usage["gemini"].is_limited is False
    assert usage["gemini"].last_error is None
    # Recovering from a limit starts the count fresh rather than resuming
    # the frozen pre-limit count.
    assert usage["gemini"].used_count == 0


def test_record_call_does_not_affect_other_providers(db_session: Session, test_user_id: uuid.UUID) -> None:
    record_call("gemini", db_session, test_user_id, error="quota exceeded")
    usage = load_usage(db_session, test_user_id)
    assert usage["mistral"].used_count == 0
    assert usage["mistral"].is_limited is False


def test_load_usage_resets_a_daily_provider_once_the_day_has_rolled_over(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    seeded = load_usage(db_session, test_user_id, now=datetime(2026, 7, 6, 3, 0, tzinfo=UTC))
    seeded["gemini"] = seeded["gemini"].model_copy(update={"used_count": 5})
    save_usage(seeded, db_session, test_user_id)

    same_day = load_usage(db_session, test_user_id, now=datetime(2026, 7, 6, 20, 0, tzinfo=UTC))
    assert same_day["gemini"].used_count == 5

    next_day = load_usage(db_session, test_user_id, now=datetime(2026, 7, 7, 3, 0, tzinfo=UTC))
    assert next_day["gemini"].used_count == 0


def test_load_usage_does_not_reset_a_monthly_provider_within_the_same_month(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    seeded = load_usage(db_session, test_user_id, now=datetime(2026, 7, 1, tzinfo=UTC))
    seeded["mistral"] = seeded["mistral"].model_copy(update={"used_count": 3})
    save_usage(seeded, db_session, test_user_id)

    usage = load_usage(db_session, test_user_id, now=datetime(2026, 7, 20, tzinfo=UTC))
    assert usage["mistral"].used_count == 3


def test_load_usage_resets_a_monthly_provider_once_the_month_has_rolled_over(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    seeded = load_usage(db_session, test_user_id, now=datetime(2026, 7, 1, tzinfo=UTC))
    seeded["mistral"] = seeded["mistral"].model_copy(update={"used_count": 3})
    save_usage(seeded, db_session, test_user_id)

    usage = load_usage(db_session, test_user_id, now=datetime(2026, 8, 1, tzinfo=UTC))
    assert usage["mistral"].used_count == 0


def test_tracked_provider_records_a_success(db_session: Session, test_user_id: uuid.UUID) -> None:
    provider = TrackedProvider(_FakeProvider(response="ok"), "gemini", db_session, test_user_id)
    result = provider.complete("system", "user")
    assert result == "ok"
    assert load_usage(db_session, test_user_id)["gemini"].used_count == 1
    assert load_usage(db_session, test_user_id)["gemini"].is_limited is False


def test_tracked_provider_records_and_reraises_a_failure(db_session: Session, test_user_id: uuid.UUID) -> None:
    provider = TrackedProvider(_FakeProvider(error="rate limited"), "mistral", db_session, test_user_id)
    with pytest.raises(LLMProviderError, match="rate limited"):
        provider.complete("system", "user")
    usage = load_usage(db_session, test_user_id)
    assert usage["mistral"].used_count == 0
    assert usage["mistral"].is_limited is True
    assert usage["mistral"].last_error == "rate limited"
