"""Small OpenAI SDK adapter used when LangChain is intentionally absent."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


class ChatOpenAI:
    """ChatOpenAI-compatible surface backed by the lightweight OpenAI SDK."""

    def __init__(self, **kwargs: Any) -> None:
        self.model = kwargs.pop("model", kwargs.pop("model_name", None))
        if not self.model:
            raise ValueError("ChatOpenAI requires a model/model_name")
        self._client_kwargs = kwargs
        self._client: Any = None

    def invoke(
        self,
        messages: Iterable[Mapping[str, Any]],
        **kwargs: Any,
    ) -> Any:
        """Send a chat request and return the SDK response message."""
        if self._client is None:
            try:
                from openai import OpenAI
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "Install the optional 'openai' SDK to invoke ChatOpenAI"
                ) from exc
            self._client = OpenAI(**self._client_kwargs)
        response = self._client.chat.completions.create(
            model=self.model,
            messages=list(messages),
            **kwargs,
        )
        return response.choices[0].message
