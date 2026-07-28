"""Mistral `LLMProvider`, via `mistralai` — the fallback provider when Gemini errors or hits its free-tier limit."""

from __future__ import annotations

from accounting.llm.provider import LLMProviderError

DEFAULT_MODEL = "mistral-small-latest"


class MistralProvider:
    """Wraps `mistralai.client.Mistral` behind the shared `LLMProvider.complete` interface."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        """Bind this provider to one API key and model."""
        self._api_key = api_key
        self._model = model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """See `LLMProvider.complete`.

        Returns
        -------
        str
            Mistral's raw text response.

        Raises
        ------
        LLMProviderError
            If `mistralai` isn't installed, or the API call fails for any reason (rate limit, auth, network).
        """
        try:
            from mistralai.client import Mistral  # noqa: PLC0415
        except ImportError as error:
            message = "mistralai is not installed"
            raise LLMProviderError(message) from error
        # Plain dicts satisfy the SDK's message TypedDicts structurally at runtime; mypy can't verify that
        # through the Union of TypedDict/model types, hence the ignore.
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            client = Mistral(api_key=self._api_key)
            response = client.chat.complete(model=self._model, messages=messages)  # type: ignore[arg-type]
        except Exception as error:
            raise LLMProviderError(str(error)) from error
        if not response.choices or response.choices[0].message is None:
            return ""
        content = response.choices[0].message.content
        return content if isinstance(content, str) else ""


def verify_mistral_key(api_key: str) -> None:
    """Check that an API key actually authenticates, without spending a completion.

    `models.list()` is a free, read-only call — validates the key the same
    way `complete()`'s first real call would, without spending a
    completion on a key that might not even work.

    Parameters
    ----------
    api_key
        The Mistral API key to check.

    Raises
    ------
    LLMProviderError
        If `mistralai` isn't installed, or the key is rejected.
    """
    try:
        from mistralai.client import Mistral  # noqa: PLC0415
    except ImportError as error:
        message = "mistralai is not installed"
        raise LLMProviderError(message) from error
    try:
        Mistral(api_key=api_key).models.list()
    except Exception as error:
        raise LLMProviderError(str(error)) from error
