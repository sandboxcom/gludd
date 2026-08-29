"""Stable data types shared by the self-update rollback engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType


@dataclass
class ModuleSnapshot:
    """A shallow, identity-preserving backup of Python module state."""

    modules: dict[str, ModuleType] = field(default_factory=dict)
    namespaces: dict[str, dict[str, object]] = field(default_factory=dict)
    snapshot_at: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        """Return whether the snapshot owns at least one module."""
        return bool(self.modules)
