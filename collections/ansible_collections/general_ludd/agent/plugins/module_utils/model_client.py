"""Authenticated stdlib model client for collection execution.

The Ansible execution environment never imports model providers or loads model
weights.  Every operation crosses the versioned Gludd daemon HTTP boundary and
therefore shares the daemon's model cache, budget guard, and routing policy.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any, cast

from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    DEFAULT_DAEMON_URL,
    DEFAULT_TIMEOUT,
    GluddClient,
)


def Message(role: str, content: str) -> dict[str, str]:
    """Build one validated-on-the-server chat message."""
    return {"role": role, "content": content}


def _prompt_parts(messages: list[dict[str, str]]) -> tuple[str, str]:
    """Convert chat messages to the daemon's bounded single-turn contract."""
    if not messages:
        raise ValueError("messages must not be empty")
    system_parts: list[str] = []
    prompt_parts: list[str] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = str(message.get("content", ""))
        if role == "system":
            system_parts.append(content)
        else:
            prompt_parts.append(f"{role}: {content}")
    prompt = "\n".join(prompt_parts).strip()
    if not prompt:
        raise ValueError("messages must contain non-system content")
    return prompt, "\n".join(system_parts).strip()


class ModelClient:
    """Synchronous compatibility client backed only by :class:`GluddClient`."""

    def __init__(
        self,
        profile_name: str = "default",
        *,
        daemon_url: str = DEFAULT_DAEMON_URL,
        psk: str = "",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self._profile = profile_name
        self._client = GluddClient(base_url=daemon_url, psk=psk, timeout=timeout)

    def chat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return the daemon model response without an in-process fallback."""
        prompt, system = _prompt_parts(messages)
        max_tokens = kwargs.get("max_tokens", 2048)
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool):
            raise TypeError("max_tokens must be an integer")
        result = self._client.call_model(
            prompt,
            model_profile=self._profile,
            route_task_type=kwargs.get("route_task_type"),
            max_tokens=max_tokens,
            system=system or None,
            response_format=kwargs.get("response_format"),
            response_schema=kwargs.get("response_schema"),
        )
        if result.get("_error"):
            return cast(dict[str, Any], result)
        return {
            "text": result.get("text", ""),
            "model_profile_id": result.get("model_profile_id", self._profile),
            "model_name": result.get("model_name", ""),
            "usage": result.get("usage", result.get("usage_metadata", {})),
            "cost_estimate": result.get("cost_estimate"),
            "_status": result.get("_status", 200),
        }

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Generator[dict[str, Any], None, None]:
        """Yield one compatible delta from the bounded synchronous endpoint."""
        result = self.chat(messages, **kwargs)
        text = result.get("text")
        if isinstance(text, str) and text:
            yield {"delta": text}

    def embed(
        self,
        texts: str | list[str],
        *,
        include_embedding: bool = True,
    ) -> dict[str, Any]:
        """Embed text through the daemon's canonical embeddings endpoint."""
        batch = [texts] if isinstance(texts, str) else list(texts)
        if not batch:
            return {"embeddings": [], "embedding_method": "daemon", "dim": 0}
        # The compare endpoint deliberately requires at least two texts.  A
        # duplicate sentinel keeps one-text calls on that same canonical path.
        request_batch = batch if len(batch) >= 2 else [batch[0], batch[0]]
        result = self._client.post(
            "/api/embeddings/compare",
            {"texts": request_batch, "include_embeddings": include_embedding},
        )
        embeddings = result.get("embeddings")
        vectors = embeddings if isinstance(embeddings, list) else []
        if isinstance(texts, str):
            return {
                "embedding": vectors[0] if vectors else [],
                "embedding_method": result.get("embedding_method", "daemon"),
                "dim": result.get("dim", 0),
                "_status": result.get("_status", 0),
            }
        return {
            "embeddings": vectors[: len(batch)],
            "embedding_method": result.get("embedding_method", "daemon"),
            "dim": result.get("dim", 0),
            "_status": result.get("_status", 0),
        }

    def list_models(self) -> dict[str, Any]:
        """Return daemon-owned model profiles."""
        return cast(dict[str, Any], self._client.get("/admin/models"))

    def reachable(self) -> bool:
        """Return whether the authenticated daemon health endpoint responds."""
        return bool(self._client.reachable())


def chat(messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
    """Top-level compatibility wrapper."""
    return ModelClient().chat(messages, **kwargs)


def chat_stream(
    messages: list[dict[str, str]],
    **kwargs: Any,
) -> Generator[dict[str, Any], None, None]:
    """Top-level streaming compatibility wrapper."""
    return ModelClient().chat_stream(messages, **kwargs)


def embed(text: str) -> dict[str, Any]:
    """Top-level embedding compatibility wrapper."""
    return ModelClient().embed(text)


def list_models() -> dict[str, Any]:
    """Top-level profile-list compatibility wrapper."""
    return ModelClient().list_models()
