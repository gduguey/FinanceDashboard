"""Self-tracked call counters for each LLM provider's free-tier limit.

Neither Gemini nor Mistral's API exposes a live "remaining credits" figure
through the calls this module makes (see `llm.gemini`/`llm.mistral`) — the
real limit is only ever discovered the hard way, as a rate-limit-shaped
error on some future call. So rather than guess a cap up front, this counts
every call attempted through this app and remembers the exact error text
the provider returned the last time one was refused — that error is a more
accurate description of the real limit than any number this module could
hardcode. `RESET_PERIOD` is only the display/reset cadence each provider's
free tier is publicly known to use (Gemini: daily, Mistral: monthly), not a
guess at the cap itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from accounting.db.llm import LLMUsage as LLMUsageRow
from accounting.llm.provider import LLMProviderError

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from accounting.llm.provider import LLMProvider

ResetPeriod = Literal["daily", "monthly"]

RESET_PERIOD: dict[str, ResetPeriod] = {"gemini": "daily", "mistral": "monthly"}


class ProviderUsage(BaseModel):
    """One provider's call count and rate-limit state for its current period."""

    model_config = ConfigDict(frozen=True)

    period_start: datetime
    used_count: int = 0
    is_limited: bool = False
    last_error: str | None = None


def _current_period_start(period: ResetPeriod, now: datetime) -> datetime:
    # Naive, like every other timestamp this app stores in Postgres (see
    # `trades.config.TimezoneConfig`'s own docstring on the same
    # convention) — `period_start` round-trips through a plain (non-tz)
    # `DateTime` column, so it must be compared against a naive value too,
    # regardless of whether the caller passed an aware `now`.
    naive_now = now.replace(tzinfo=None) if now.tzinfo is not None else now
    start_of_day = naive_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day if period == "daily" else start_of_day.replace(day=1)


def load_usage(session: Session, user_id: uuid.UUID, now: datetime | None = None) -> dict[str, ProviderUsage]:
    """Read this user's usage for every known provider, rolling each one over to a fresh period if its own has elapsed.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose usage to read.
    now
        The current time, for computing each provider's period boundary — overridable for tests.

    Returns
    -------
    dict[str, ProviderUsage]
        Every known provider (see `RESET_PERIOD`), keyed by name.
    """
    now = now or datetime.now(UTC)
    usage: dict[str, ProviderUsage] = {}
    for provider, period in RESET_PERIOD.items():
        current_start = _current_period_start(period, now)
        row = session.get(LLMUsageRow, (user_id, provider))
        entry = (
            ProviderUsage(
                period_start=row.period_start,
                used_count=row.used_count,
                is_limited=row.is_limited,
                last_error=row.last_error,
            )
            if row is not None
            else None
        )
        is_current = entry is not None and entry.period_start >= current_start
        usage[provider] = entry if is_current and entry is not None else ProviderUsage(period_start=current_start)
    return usage


def save_usage(usage: dict[str, ProviderUsage], session: Session, user_id: uuid.UUID) -> None:
    """Persist this user's usage for every provider, overwriting whatever was saved before.

    Parameters
    ----------
    usage
        Every provider's usage to persist.
    session
        An active database session.
    user_id
        Whose usage this is.
    """
    for provider, entry in usage.items():
        row = session.get(LLMUsageRow, (user_id, provider))
        if row is None:
            row = LLMUsageRow(user_id=user_id, provider=provider)
            session.add(row)
        row.period_start = entry.period_start
        row.used_count = entry.used_count
        row.is_limited = entry.is_limited
        row.last_error = entry.last_error
    session.commit()


def record_call(provider: str, session: Session, user_id: uuid.UUID, error: str | None) -> None:
    """Record one attempted call to `provider` for this user: increment counter on success, freeze on failure.

    Parameters
    ----------
    provider
        The provider name that was called (see `RESET_PERIOD`).
    session
        An active database session.
    user_id
        Who made the call.
    error
        The provider's own error text if the call failed, `None` if it succeeded.

        Behavior:
        - On success: counter increments, flag clears
        - On failure: counter frozen, flag sets
        - On success after failure: counter resets to 0, flag clears
    """
    usage = load_usage(session, user_id)
    current = usage[provider]

    if error is None:
        # Success
        new_count = 0 if current.is_limited else current.used_count + 1
        new_limited = False
    else:
        # Failure: freeze counter, set flag
        new_count = current.used_count
        new_limited = True

    usage[provider] = current.model_copy(
        update={"used_count": new_count, "is_limited": new_limited, "last_error": error}
    )
    save_usage(usage, session, user_id)


class TrackedProvider:
    """Wraps an `LLMProvider`, recording every call's outcome to the `llm_usage` table before returning/re-raising."""

    def __init__(self, inner: LLMProvider, name: str, session: Session, user_id: uuid.UUID) -> None:
        self._inner = inner
        self._name = name
        self._session = session
        self._user_id = user_id

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """See `LLMProvider.complete` — identical behavior, with a usage record written on the way out.

        Returns
        -------
        str
            The wrapped provider's raw text response.

        Raises
        ------
        LLMProviderError
            Whatever the wrapped provider raised, after recording it — never swallowed.
        """
        try:
            result = self._inner.complete(system_prompt, user_prompt)
        except LLMProviderError as error:
            record_call(self._name, self._session, self._user_id, str(error))
            raise
        record_call(self._name, self._session, self._user_id, None)
        return result
