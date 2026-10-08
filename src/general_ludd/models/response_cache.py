"""Diskcache-based model response caching for ModelGateway."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from types import TracebackType
from typing import cast

from general_ludd.security.safe_diskcache import SafeCache, open_safe_diskcache

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
    """Cache model responses in the strict MessagePack namespace."""

    def __init__(self, cache_dir: str | None = None) -> None:
        """Open an owner-only response cache at cache_dir or the default path."""
        self._cache: SafeCache = open_safe_diskcache(
            cache_dir or DEFAULT_CACHE_DIR,
        )
        self._closed = False

    def __enter__(self) -> ModelResponseCache:
        """Return this cache as an owned context-manager resource."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the owned cache when leaving its context."""
        self.close()

    def __del__(self) -> None:
        """Best-effort close a cache whose deterministic owner was missed."""
        # A context manager is the deterministic ownership path.  Keep this
        # narrow fallback so a forgotten close cannot leave diskcache's SQLite
        # connection to emit an unraisable ResourceWarning during GC.
        with contextlib.suppress(Exception):
            self.close()

    def get(self, cache_key: str) -> dict[str, object] | None:
        """Return a cached response mapping, or None for misses/invalid values."""
        result: object = self._cache.get(cache_key)
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
        """Store a response under cache_key with the requested expiration."""
        self._cache.set(cache_key, response, expire=expire)

    def invalidate(self, cache_key: str) -> None:
        """Remove cache_key if it exists."""
        self._cache.delete(cache_key)

    def clear(self) -> None:
        """Remove every response from this safe cache namespace."""
        self._cache.clear()

    def close(self) -> None:
        """Close the owned cache exactly once."""
        if getattr(self, "_closed", True):
            return
        self._cache.close()
        self._closed = True
