"""Diskcache-based model response caching for ModelGateway."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, cast

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
        from diskcache import Cache
        self._cache: Any = Cache(os.path.expanduser(cache_dir or DEFAULT_CACHE_DIR))

    def get(self, cache_key: str) -> dict[str, Any] | None:
        result: Any = self._cache.get(cache_key)
        if isinstance(result, dict):
            return cast(dict[str, Any], result)
        return None

    def set(self, cache_key: str, response: dict[str, Any]) -> None:
        self._cache.set(cache_key, response)

    def invalidate(self, cache_key: str) -> None:
        self._cache.delete(cache_key)

    def clear(self) -> None:
        self._cache.clear()

    def close(self) -> None:
        self._cache.close()
