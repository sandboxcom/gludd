"""
Thin Ansible-compatible wrapper around core ModelGateway and HashEmbedder.

All chat / stream / list logic delegates to ``ModelGateway``; all
embedding to ``HashEmbedder``.  This module contains ZERO algorithmic
reimplementations — only import-path setup, interface adaptation, and
lazy gateway construction.

Usage
-----
    from ansible_collections.general_ludd.agent.plugins.module_utils.model_client import (
        ModelClient,
        Message,
    )

    client = ModelClient("default")
    response = client.chat([{"role": "user", "content": "Hello"}])
    for event in client.chat_stream([{"role": "user", "content": "Write a haiku"}]):
        print(event)
    embedding = client.embed("What is the meaning of life?")
    profiles = client.list_models()

Environment variables
---------------------
GLUDD_MODEL_PROFILE_ID
    Model profile ID used by the gateway (default ``"default"``).
GLUDD_MODEL_PROVIDER
    Provider name — ``openai``, ``deepseek``, etc. (default ``"deepseek"``).
GLUDD_MODEL_NAME
    Model name (default ``"deepseek-chat"``).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

# -- sys.path for ansible runtime ----------------------------------------
_SRC = Path(__file__).resolve().parents[6] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from general_ludd.models.gateway import ModelGateway, ModelProfile  # type: ignore[import]  # noqa: E402
from general_ludd.models.provider_registry import ProviderRegistry  # type: ignore[import]  # noqa: E402
from general_ludd.skills.embeddings import HashEmbedder  # type: ignore[import]  # noqa: E402


# ---------------------------------------------------------------------------
# Gateway singleton — constructed lazily from environment
# ---------------------------------------------------------------------------

_gateway: ModelGateway | None = None


def _get_gateway() -> ModelGateway:  # pragma: no cover — live deps
    """Return a lazily-built ModelGateway from env vars.

    On first call creates a single ``ModelProfile`` from
    ``GLUDD_MODEL_*`` env vars and builds a ``ModelGateway`` with a
    ``ProviderRegistry`` seeded from that profile.  Subsequent calls
    return the cached instance.

    The lazy singleton avoids importing heavy langchain / pydantic deps
    at module-load time — construction only happens on first use.
    """
    global _gateway
    if _gateway is not None:
        return _gateway

    profile_id = os.environ.get("GLUDD_MODEL_PROFILE_ID", "default")
    provider = os.environ.get("GLUDD_MODEL_PROVIDER", "deepseek")
    model_name = os.environ.get("GLUDD_MODEL_NAME", "deepseek-chat")

    profile = ModelProfile(
        model_profile_id=profile_id,
        provider=provider,
        model_name=model_name,
        enabled=True,
        api_metered=False,
    )

    _gateway = ModelGateway(
        profiles=[profile],
        provider_registry=ProviderRegistry.from_profiles([profile]),
    )
    return _gateway


# ---------------------------------------------------------------------------
# Message helper
# ---------------------------------------------------------------------------


def Message(role: str, content: str) -> dict[str, str]:
    """Build a ``{"role": …, "content": …}`` message dict."""
    return {"role": role, "content": content}


# ---------------------------------------------------------------------------
# ModelClient — thin wrapper class (backward-compatible with prior HTTP impl)
# ---------------------------------------------------------------------------


class ModelClient:
    """Synchronous model client backed by :class:`ModelGateway`.

    Parameters
    ----------
    profile_name:
        Model profile ID to use for ``chat`` / ``chat_stream`` calls.
        Default ``"default"``.
    """

    def __init__(self, profile_name: str = "default") -> None:
        self._profile = profile_name
        self._gateway = _get_gateway()
        self._embedder = HashEmbedder()

    # ------------------------------------------------------------------
    # chat
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Synchronous chat completion via ModelGateway.call_model.

        Returns a dict with keys ``text``, ``model_profile_id``,
        ``usage``, and ``_status``.
        """
        from general_ludd.models.gateway import ModelResponse  # type: ignore[import]

        response: ModelResponse = self._gateway.call_model(
            self._profile,
            messages,
            **kwargs,
        )
        return {
            "text": response.content,
            "model_profile_id": self._profile,
            "model_name": response.model_name,
            "usage": response.usage_metadata,
            "cost_estimate": response.cost_estimate,
            "_status": 200,
        }

    # ------------------------------------------------------------------
    # chat_stream
    # ------------------------------------------------------------------

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Generator[dict[str, Any], None, None]:
        """Streaming chat completion via ModelGateway.call_model_stream.

        Yields dicts with ``delta`` (the incremental text content).
        """
        for chunk in self._gateway.call_model_stream(
            self._profile,
            messages,
            **kwargs,
        ):
            content = getattr(chunk, "content", "")
            if content:
                yield {"delta": str(content)}

    # ------------------------------------------------------------------
    # embed
    # ------------------------------------------------------------------

    def embed(
        self,
        texts: str | list[str],
        *,
        include_embedding: bool = True,
    ) -> dict[str, Any]:
        """Get embeddings via :class:`HashEmbedder`.

        Single-string input returns ``{"embedding": [...], "dim": N}``.
        List input returns ``{"embeddings": [[...], ...], "dim": N}``.
        """
        if isinstance(texts, str):
            vec = self._embedder.embed(texts)
            return {
                "embedding": vec,
                "embedding_method": "hash",
                "dim": len(vec),
            }

        results = [self._embedder.embed(t) for t in texts]
        return {
            "embeddings": results,
            "embedding_method": "hash",
            "dim": len(results[0]) if results else 0,
        }

    # ------------------------------------------------------------------
    # list_models
    # ------------------------------------------------------------------

    def list_models(self) -> dict[str, Any]:
        """Return available model profiles from the gateway."""
        profiles = self._gateway.list_profiles()
        return {
            "profiles": [p.model_dump() for p in profiles],
            "_status": 200,
        }

    # ------------------------------------------------------------------
    # reachable
    # ------------------------------------------------------------------

    def reachable(self) -> bool:
        """Return ``True`` when at least one profile is configured."""
        try:
            return len(self._gateway.list_profiles()) > 0
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Top-level convenience functions (ansible-compatible, stdlib-only signature)
# ---------------------------------------------------------------------------


def chat(messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
    """Top-level chat — delegates to :meth:`ModelClient.chat`."""
    return ModelClient().chat(messages, **kwargs)


def chat_stream(
    messages: list[dict[str, str]],
    **kwargs: Any,
) -> Generator[dict[str, Any], None, None]:
    """Top-level streaming chat — delegates to :meth:`ModelClient.chat_stream`."""
    return ModelClient().chat_stream(messages, **kwargs)


def embed(text: str) -> dict[str, Any]:
    """Top-level embed — delegates to :meth:`ModelClient.embed`."""
    return ModelClient().embed(text)


def list_models() -> dict[str, Any]:
    """Top-level profile listing — delegates to :meth:`ModelClient.list_models`."""
    return ModelClient().list_models()
