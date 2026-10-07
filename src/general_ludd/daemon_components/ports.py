"""Dependency ports resolved by the daemon compatibility facade at call time."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LifecyclePorts:
    """Facade-owned lifecycle dependencies and monkeypatch seams."""

    values: Mapping[str, Any]

    def __getattr__(self, name: str) -> Any:
        """Resolve one dependency from the live facade namespace."""
        try:
            return self.values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc
