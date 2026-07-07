"""RendererCache — in-memory TTL cache for rendered playbooks.

Keyed by renderer name. Stores the validated :class:`RendererOutput` (not the
HTML) so the template layer can be re-rendered without re-executing the
playbook. Entries expire ``ttl`` seconds after ``set``; a ``ttl`` of ``0``
disables caching for that entry (``get`` always returns ``None``).
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 30.0


class RendererCache:
    """Simple thread-unsafe TTL dict (the daemon is single-threaded for render)."""

    def __init__(self, ttl_default: float = _DEFAULT_TTL) -> None:
        self._ttl_default = ttl_default
        self._entries: dict[str, tuple[object, float]] = {}

    def get(self, name: str) -> Any | None:
        """Return the cached value if present and unexpired, else ``None``."""
        entry = self._entries.get(name)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            # Lazy expiry: drop the stale entry.
            self._entries.pop(name, None)
            return None
        return value

    def set(self, name: str, value: Any, ttl: float | None = None) -> None:
        """Store ``value`` under ``name`` with ``ttl`` seconds lifetime."""
        effective = self._ttl_default if ttl is None else float(ttl)
        if effective <= 0:
            # ttl=0 means "do not cache" — record nothing so get() always misses.
            self._entries.pop(name, None)
            return
        self._entries[name] = (value, time.monotonic() + effective)

    def clear(self, name: str) -> bool:
        """Drop a single entry. Returns ``True`` if it was present."""
        return self._entries.pop(name, None) is not None

    def clear_all(self) -> int:
        """Drop every entry. Returns the count cleared."""
        n = len(self._entries)
        self._entries.clear()
        return n

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: object) -> bool:
        return self.get(str(name)) is not None
