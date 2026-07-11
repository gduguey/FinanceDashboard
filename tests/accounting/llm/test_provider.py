import pytest

from accounting.llm.provider import LLMProviderError, complete_with_fallback


class _FakeProvider:
    def __init__(self, response: str | None = None, error: str | None = None) -> None:
        self._response = response
        self._error = error

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if self._error is not None:
            raise LLMProviderError(self._error)
        assert self._response is not None
        return self._response


def test_complete_with_fallback_uses_the_first_provider_when_it_succeeds() -> None:
    result = complete_with_fallback([_FakeProvider(response="gemini says hi")], "system", "user")
    assert result == "gemini says hi"


def test_complete_with_fallback_falls_back_to_the_next_provider_on_failure() -> None:
    providers = [_FakeProvider(error="rate limited"), _FakeProvider(response="mistral says hi")]
    result = complete_with_fallback(providers, "system", "user")
    assert result == "mistral says hi"


def test_complete_with_fallback_raises_naming_every_failure_when_all_fail() -> None:
    providers = [_FakeProvider(error="gemini down"), _FakeProvider(error="mistral down")]
    with pytest.raises(LLMProviderError, match=r"gemini down.*mistral down"):
        complete_with_fallback(providers, "system", "user")


def test_complete_with_fallback_raises_when_no_providers_are_configured() -> None:
    with pytest.raises(LLMProviderError, match="No LLM provider"):
        complete_with_fallback([], "system", "user")
