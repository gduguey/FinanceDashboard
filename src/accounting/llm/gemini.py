"""Gemini `LLMProvider`, via `google-genai`."""

from __future__ import annotations

from accounting.llm.provider import LLMProviderError

DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiProvider:
    """Wraps `google.genai.Client` behind the shared `LLMProvider.complete` interface — the default provider."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
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
        except Exception as error:
            raise LLMProviderError(str(error)) from error
        return response.text or ""
