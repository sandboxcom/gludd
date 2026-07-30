"""Diskcache-based model response caching for ModelGateway."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from types import TracebackType
from typing import Any, cast

from general_ludd.security.safe_diskcache import (
    open_safe_diskcache,
    prepare_safe_cache_directory,
)

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = "~/.cache/general-ludd/response-cache"

# Default TTL (seconds) for cached model responses. LLM outputs are
# non-deterministic / time-sensitive, so entries must expire.
DEFAULT_CACHE_TTL_SECONDS = 3600


def _make_cache_key(
    profile_id: str,
    messages: list[dict[str, str]],
    *,
    model_name: str | None = None,
    **kwargs: object,
) -> str:
    # Include the resolved model_name so that swapping a profile's underlying
    # model invalidates its cached output instead of serving the old model's
    # response under the same profile id.
    payload = {
        "profile": profile_id,
        "model_name": model_name,
        "messages": messages,
        "kwargs": kwargs,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class ModelResponseCache:
    def __init__(self, cache_dir: str | None = None) -> None:
        path = os.path.expanduser(cache_dir or DEFAULT_CACHE_DIR)
        # All callers share the non-executable MessagePack adapter. It stores
        # data in a versioned owner-only namespace and never reads legacy
        # pickle rows (CVE-2025-69872).
        prepare_safe_cache_directory(path)
        self._cache_path = path
        self._cache: Any | None = None
        self._cache_lock = threading.Lock()

    def __enter__(self) -> ModelResponseCache:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _get_cache(self) -> Any:
        cache = self._cache
        if cache is not None:
            return cache
        with self._cache_lock:
            cache = self._cache
            if cache is None:
                cache = open_safe_diskcache(self._cache_path)
                self._cache = cache
            return cache

    def get(self, cache_key: str) -> dict[str, object] | None:
        result: object = self._get_cache().get(cache_key)
        if isinstance(result, dict):
            return cast(dict[str, object], result)
        return None

    def set(
        self,
        cache_key: str,
        response: dict[str, object],
        *,
        expire: float | None = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        self._get_cache().set(cache_key, response, expire=expire)

    def invalidate(self, cache_key: str) -> None:
        self._get_cache().delete(cache_key)

    def clear(self) -> None:
        self._get_cache().clear()

    def close(self) -> None:
        with self._cache_lock:
            cache = self._cache
            self._cache = None
        if cache is not None:
            cache.close()
