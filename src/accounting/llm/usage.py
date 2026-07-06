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

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from accounting.llm.provider import LLMProviderError
from trades.utils.io_utils import write_json_atomic

if TYPE_CHECKING:
    from pathlib import Path

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
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day if period == "daily" else start_of_day.replace(day=1)


def load_usage(path: Path, now: datetime | None = None) -> dict[str, ProviderUsage]:
    """Read every provider's usage, rolling each one over to a fresh period if its own has elapsed.

    Parameters
    ----------
    path
        Where usage is persisted; treated as empty if it doesn't exist yet.
    now
        The current time, for computing each provider's period boundary — overridable for tests.

    Returns
    -------
    dict[str, ProviderUsage]
        Every known provider (see `RESET_PERIOD`), keyed by name.
    """
    now = now or datetime.now(UTC)
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    usage: dict[str, ProviderUsage] = {}
    for provider, period in RESET_PERIOD.items():
        current_start = _current_period_start(period, now)
        stored = raw.get(provider)
        entry = ProviderUsage.model_validate(stored) if stored is not None else None
        usage[provider] = (
            entry
            if entry is not None and entry.period_start >= current_start
            else ProviderUsage(period_start=current_start)
        )
    return usage


def save_usage(usage: dict[str, ProviderUsage], path: Path) -> None:
    """Persist every provider's usage, overwriting whatever was saved before."""
    write_json_atomic({provider: entry.model_dump(mode="json") for provider, entry in usage.items()}, path)


def record_call(provider: str, path: Path, error: str | None) -> None:
    """Record one attempted call to `provider`: increment counter on success, freeze on failure.

    Parameters
    ----------
    provider
        The provider name that was called (see `RESET_PERIOD`).
    path
        Where usage is persisted.
    error
        The provider's own error text if the call failed, `None` if it succeeded.

        Behavior:
        - On success: counter increments, flag clears
        - On failure: counter frozen, flag sets
        - On success after failure: counter resets to 0, flag clears
    """
    usage = load_usage(path)
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
    save_usage(usage, path)


class TrackedProvider:
    """Wraps an `LLMProvider`, recording every call's outcome to `usage.json` before returning/re-raising."""

    def __init__(self, inner: LLMProvider, name: str, usage_path: Path) -> None:
        self._inner = inner
        self._name = name
        self._usage_path = usage_path

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
            record_call(self._name, self._usage_path, str(error))
            raise
        record_call(self._name, self._usage_path, None)
        return result
