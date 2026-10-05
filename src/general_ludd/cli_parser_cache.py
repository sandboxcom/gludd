"""Bounded, patch-aware cache for the immutable CLI parser graph."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Generic, TypeVar

_ResultT = TypeVar("_ResultT")
_HandlerFingerprint = tuple[tuple[str, str, int], ...]


class CommandGraphCache(Generic[_ResultT]):
    """Retain one canonical graph while isolating replaced command handlers."""

    def __init__(self, builder: Callable[[], _ResultT], *, module_prefix: str) -> None:
        """Initialize a single-entry cache for one handler-module family."""
        self._builder = builder
        self._module_prefix = module_prefix
        self._entry: tuple[_HandlerFingerprint, _ResultT] | None = None
        self._lock = threading.RLock()

    def _handler_fingerprint(self) -> tuple[_HandlerFingerprint, bool]:
        entries: list[tuple[str, str, int]] = []
        cacheable = True
        for module_name, module in tuple(sys.modules.items()):
            if module is None or not (
                module_name == self._module_prefix
                or module_name.startswith(f"{self._module_prefix}_")
            ):
                continue
            for attribute_name, value in vars(module).items():
                if not attribute_name.startswith("_cmd_") or not callable(value):
                    continue
                entries.append((module_name, attribute_name, id(value)))
                if (
                    getattr(value, "__module__", None) != module_name
                    or getattr(value, "__name__", None) != attribute_name
                ):
                    cacheable = False
        return tuple(sorted(entries)), cacheable

    def get(self) -> _ResultT:
        """Return the canonical graph or an uncached graph for replaced handlers."""
        with self._lock:
            fingerprint, cacheable = self._handler_fingerprint()
            if cacheable and self._entry is not None and self._entry[0] == fingerprint:
                return self._entry[1]

            result = self._builder()
            fingerprint, cacheable = self._handler_fingerprint()
            if cacheable:
                self._entry = (fingerprint, result)
            return result
