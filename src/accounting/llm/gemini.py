"""Gemini `LLMProvider`, via `google-genai`."""

from __future__ import annotations

from accounting.llm.provider import LLMProviderError

DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiProvider:
    """Wraps `google.genai.Client` behind the shared `LLMProvider.complete` interface — the default provider."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        """Bind this provider to one API key and model."""
        self._api_key = api_key
        self._model = model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """See `LLMProvider.complete`.

        Returns
        -------
        str
            Gemini's raw text response.

        Raises
        ------
        LLMProviderError
            If `google-genai` isn't installed, or the API call fails for any reason (rate limit, auth, network).
        """
        try:
            from google import genai  # noqa: PLC0415
        except ImportError as error:
            message = "google-genai is not installed"
            raise LLMProviderError(message) from error
        try:
            client = genai.Client(api_key=self._api_key)
            response = client.models.generate_content(
                model=self._model,
                contents=user_prompt,
                config={"system_instruction": system_prompt},
            )
            text = response.text
        except Exception as error:
            raise LLMProviderError(str(error)) from error
        return text or ""


def verify_gemini_key(api_key: str) -> None:
    """Check that an API key actually authenticates, without generating any content.

    `models.list()` is a free, read-only call — validates the key the same
    way `complete()`'s first real call would, without spending a
    completion on a key that might not even work.

    Parameters
    ----------
    api_key
        The Gemini API key to check.

    Raises
    ------
    LLMProviderError
        If `google-genai` isn't installed, or the key is rejected.
    """
    try:
        from google import genai  # noqa: PLC0415
    except ImportError as error:
        message = "google-genai is not installed"
        raise LLMProviderError(message) from error
    try:
        client = genai.Client(api_key=api_key)
        next(iter(client.models.list(config={"page_size": 1})), None)
    except Exception as error:
        raise LLMProviderError(str(error)) from error
