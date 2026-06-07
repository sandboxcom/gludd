"""Diskcache-based model response caching for ModelGateway."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = "~/.cache/general-ludd/response-cache"


def _make_cache_key(
    profile_id: str,
    messages: list[dict[str, str]],
    **kwargs: Any,
) -> str:
    payload = {
        "profile": profile_id,
        "messages": messages,
        "kwargs": kwargs,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class ModelResponseCache:
    def __init__(self, cache_dir: str | None = None) -> None:
        import os

        from diskcache import Cache  # type: ignore[import-untyped]

        resolved = os.path.expanduser(cache_dir or DEFAULT_CACHE_DIR)
        os.makedirs(resolved, exist_ok=True)
        self._cache = Cache(resolved)

    def get(self, cache_key: str) -> dict[str, Any] | None:
        return self._cache.get(cache_key)  # type: ignore[no-any-return]

    def set(self, cache_key: str, response: dict[str, Any]) -> None:
        self._cache.set(cache_key, response)

    def invalidate(self, cache_key: str) -> None:
        self._cache.delete(cache_key)

    def clear(self) -> None:
        self._cache.clear()

    def close(self) -> None:
        self._cache.close()
