"""Diskcache-based model response caching for ModelGateway."""

from __future__ import annotations

import contextlib
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

        path = os.path.expanduser(cache_dir or DEFAULT_CACHE_DIR)
        # Mitigation for diskcache CVE-2025-69872 (pickle deserialization →
        # arbitrary code execution for anyone with WRITE access to the cache
        # dir). diskcache has no fixed release; we cannot remove the pickle
        # codepath, so we remove the precondition: create the cache directory
        # owner-only (0o700) so no other local user can plant a malicious
        # pickle. See SECURITY.md "Known dependency advisories".
        os.makedirs(path, mode=0o700, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700)
        self._cache: Any = Cache(path)

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
