"""Provider-agnostic interface for LLM-assisted category suggestions.

Every concrete provider (Gemini, Mistral, ...) implements the same
`LLMProvider.complete` method, so `llm.categorize` — the one place that
actually prompts an LLM — never imports a specific SDK. Adding a new
provider means writing one new class here, never touching a caller.
"""

from __future__ import annotations

from typing import Protocol


class LLMProviderError(Exception):
    """Raised by any `LLMProvider.complete` on failure — rate limit, auth, network, missing SDK, etc.

    Callers (see `complete_with_fallback`) catch exactly this to fall back
    to the next configured provider, rather than any exception at all —
    a bug inside this module's own prompt-building code should still
    surface as a real traceback, not be swallowed as "try the next provider".
    """


class LLMProvider(Protocol):
    """Anything that can turn a system/user prompt pair into raw text."""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Send a prompt, return the model's raw text response.

        Parameters
        ----------
        system_prompt
            Instructions and context (the category taxonomy, few-shot examples) — same across every call.
        user_prompt
            The one thing being asked about this call — here, the description to categorize.

        Returns
        -------
        str
            The model's raw text response, not yet parsed or validated.

        Raises
        ------
        LLMProviderError
            If the call fails for any reason.
        """
        ...


def complete_with_fallback(providers: list[LLMProvider], system_prompt: str, user_prompt: str) -> str:
    """Try each provider in order, falling back to the next on failure — e.g. default-to-Gemini, then Mistral.

    Parameters
    ----------
    providers
        Providers to try, in priority order.
    system_prompt
        Passed through to each provider's `complete`.
    user_prompt
        Passed through to each provider's `complete`.

    Returns
    -------
    str
        The first successful provider's raw response.

    Raises
    ------
    LLMProviderError
        If every provider failed, or `providers` is empty — names every provider's own failure.
    """
    errors: list[str] = []
    for provider in providers:
        try:
            return provider.complete(system_prompt, user_prompt)
        except LLMProviderError as error:
            errors.append(str(error))
    if not providers:
        message = "No LLM provider was configured"
    else:
        message = f"Every configured LLM provider failed: {'; '.join(errors)}"
    raise LLMProviderError(message)
